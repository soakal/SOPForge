"""Step generation orchestrator: per-record LLM call -> round-trip gate ->
template fallback (invariants L1/L2/L3), exactly one generation attempt per
step, never retried (CLAUDE.md: "never a retry loop"). AC2: >=95% round-trip
pass rate on fixtures/ manifests with realistic mock step text."""

import threading
import time
from pathlib import Path

from pipeline.generation import _build_prompt, generate_all_steps, generate_step_text
from pipeline.manifest import Element, Manifest, Screen, Session, Step, Window, load_manifest
from pipeline.template import render_step_template
from pipeline.vision import _image_data_url

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


def _make_manifest(n):
    """A synthetic manifest with n steps, each carrying a distinct element
    name -- lets concurrency tests key a stub client's per-step delay/reply
    off the prompt text without depending on any fixture file's step count."""
    session = Session(
        id="sess-concurrency-test",
        title="",
        started_utc="2026-01-01T00:00:00.000Z",
        ended_utc="2026-01-01T00:01:00.000Z",
        machine="",
        os_build="",
        narration_wav=None,
    )
    steps = [
        Step(
            id=f"step-{i + 1:03d}",
            ts_utc="2026-01-01T00:00:00.000Z",
            action="click",
            button="left",
            screen=Screen(x=0, y=0, monitor=1),
            screenshot=f"{i + 1:03d}.png",
            window=Window(title="Test Window", process="test.exe", class_="win32"),
            element=Element(
                name=f"el-{i}", control_type="Button", automation_id="", framework="win32"
            ),
            redactions=[],
        )
        for i in range(n)
    ]
    return Manifest(schema_version="1.0", session=session, steps=steps)


class _StaggeredClient:
    """Thread-safe stub whose reply delay/text is keyed by the step's element
    name embedded in the prompt (_build_prompt always includes it) -- lets a
    test control completion order independently of submission order, and
    tracks peak concurrent in-flight calls."""

    def __init__(self, delay_for_name, reply_for_name=None):
        self.delay_for_name = delay_for_name
        self.reply_for_name = reply_for_name
        self._lock = threading.Lock()
        self.in_flight = 0
        self.peak_in_flight = 0

    def _name_in(self, content):
        for name in self.delay_for_name:
            if name in content:
                return name
        raise AssertionError(f"no known element name found in prompt: {content!r}")

    def chat(self, messages, **kwargs):
        name = self._name_in(messages[0]["content"])
        with self._lock:
            self.in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            time.sleep(self.delay_for_name[name])
            if self.reply_for_name:
                return self.reply_for_name[name]
            return "irrelevant text triggering fallback"
        finally:
            with self._lock:
                self.in_flight -= 1


class _RecordingClient:
    """A mock LLMClient recording every call, replying via reply_for_index(i)
    where i is the 0-based call count — matches generate_all_steps' in-order
    per-step iteration, so reply_for_index(i) can look up manifest.steps[i]."""

    def __init__(self, reply_for_index):
        self.reply_for_index = reply_for_index
        self.calls = []

    def chat(self, messages, **kwargs):
        idx = len(self.calls)
        self.calls.append(messages)
        return self.reply_for_index(idx)


def _realistic_reply(step):
    target = step.element.name or step.element.control_type or "the field"
    window = step.window.title or "the window"
    verb = "Click" if step.action == "click" else "Enter a value into"
    return f"{verb} {target} in {window}."


def test_realistic_mock_achieves_at_least_95_percent_round_trip():
    total_ok = 0
    total = 0
    for fixture in ("sample-manifest.json", "empty-elements-manifest.json"):
        manifest = load_manifest(FIXTURES / fixture)
        steps = list(manifest.steps)
        client = _RecordingClient(lambda idx, steps=steps: _realistic_reply(steps[idx]))
        results = generate_all_steps(manifest, client)
        total += len(results)
        total_ok += sum(1 for r in results if not r["used_fallback"])
        assert len(client.calls) == len(manifest.steps)  # one attempt per step

    assert total_ok / total >= 0.95


def test_successful_generation_is_not_marked_as_fallback():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]
    client = _RecordingClient(lambda idx, s=step: _realistic_reply(s))
    text, used_fallback = generate_step_text(step, client)
    assert used_fallback is False
    assert text == _realistic_reply(step)
    assert len(client.calls) == 1


def test_injected_mismatch_falls_back_with_exactly_one_attempt_and_no_retry():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]  # click, Save button, SmartDeploy Console

    client = _RecordingClient(lambda idx: "Completely unrelated wrong sentence.")
    text, used_fallback = generate_step_text(step, client)

    assert used_fallback is True
    assert len(client.calls) == 1  # exactly one attempt, never retried
    assert text == render_step_template(step)


def test_llm_exception_falls_back_with_exactly_one_attempt():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]

    class RaisingClient:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            raise RuntimeError("simulated LLM outage")

    client = RaisingClient()
    text, used_fallback = generate_step_text(step, client)
    assert used_fallback is True
    assert client.calls == 1
    assert text == render_step_template(step)


def test_malformed_response_falls_back_with_exactly_one_attempt():
    """Carried forward from task-05's review: a malformed LLM response
    (missing/empty `choices`, non-JSON body) must fall back cleanly rather
    than propagate an uncaught KeyError/IndexError/JSONDecodeError out of the
    orchestrator — generate_step_text's broad except covers whatever
    llm_client.chat() might raise, not just httpx.HTTPStatusError."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]

    class MalformedClient:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            raise KeyError("choices")  # simulates llm_client.py's raw KeyError

    client = MalformedClient()
    text, used_fallback = generate_step_text(step, client)
    assert used_fallback is True
    assert client.calls == 1
    assert text == render_step_template(step)


def test_vision_off_by_default_sends_plain_string_content():
    """Default (no use_vision arg at all) must be byte-identical to the
    pre-vision call: `content` is the plain prompt string, not a list."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]
    client = _RecordingClient(lambda idx, s=step: _realistic_reply(s))
    text, used_fallback = generate_step_text(step, client)
    assert used_fallback is False
    assert client.calls[0][0]["content"] == _build_prompt(step)
    assert isinstance(client.calls[0][0]["content"], str)


def test_vision_on_with_existing_screenshot_builds_multipart_content_and_succeeds(tmp_path):
    """use_vision=True + a real file at screenshot_dir/step.screenshot builds
    the two-block text+image_url content, and the reply is a genuine
    non-fallback pass (used_fallback is False) -- proves round_trip_ok saw a
    real reply, not a template masquerading as one."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]
    screenshot_path = tmp_path / step.screenshot
    screenshot_path.write_bytes(b"fake-png-bytes-for-test")

    client = _RecordingClient(lambda idx, s=step: _realistic_reply(s))
    text, used_fallback = generate_step_text(step, client, use_vision=True, screenshot_dir=tmp_path)

    assert used_fallback is False
    content = client.calls[0][0]["content"]
    assert content == [
        {"type": "text", "text": _build_prompt(step)},
        {"type": "image_url", "image_url": {"url": _image_data_url(screenshot_path)}},
    ]


def test_vision_on_with_existing_screenshot_and_mismatched_reply_falls_back(tmp_path):
    """Combines the vision-on multipart path with the round-trip-failure path:
    a real screenshot on disk builds the two-block text+image_url content (so
    this is a genuine vision call, not the missing-screenshot fall-through),
    but the reply fails the round-trip gate -- must still fall back to the
    template, exactly like the vision-off case, proving the fallback gate
    runs identically regardless of use_vision."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]  # click, Save button, SmartDeploy Console
    screenshot_path = tmp_path / step.screenshot
    screenshot_path.write_bytes(b"fake-png-bytes-for-test")

    client = _RecordingClient(lambda idx: "Completely unrelated wrong sentence.")
    text, used_fallback = generate_step_text(step, client, use_vision=True, screenshot_dir=tmp_path)

    assert used_fallback is True
    assert len(client.calls) == 1  # exactly one attempt, never retried
    assert text == render_step_template(step)
    content = client.calls[0][0]["content"]
    assert content == [
        {"type": "text", "text": _build_prompt(step)},
        {"type": "image_url", "image_url": {"url": _image_data_url(screenshot_path)}},
    ]


def test_vision_on_without_matching_screenshot_falls_through_to_plain_text(tmp_path):
    """A missing screenshot file is NOT a generation failure -- it just
    means vision can't be attached, so the call proceeds with the ordinary
    plain-string prompt (and can still succeed, not fall back)."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]  # tmp_path is empty -- no file named step.screenshot in it

    client = _RecordingClient(lambda idx, s=step: _realistic_reply(s))
    text, used_fallback = generate_step_text(step, client, use_vision=True, screenshot_dir=tmp_path)

    assert used_fallback is False
    assert client.calls[0][0]["content"] == _build_prompt(step)


def test_vision_on_without_matching_screenshot_logs_warning(tmp_path, caplog):
    """The missing-screenshot fall-through (generation.py's own comment: 'NOT
    treated as a generation failure') is otherwise silent -- mirror vision.py's
    _caption_one convention (a logger.warning naming the path) so the degrade
    still leaves a discoverable trace in logs."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]  # tmp_path is empty -- no file named step.screenshot in it

    client = _RecordingClient(lambda idx, s=step: _realistic_reply(s))
    with caplog.at_level("WARNING", logger="pipeline.generation"):
        generate_step_text(step, client, use_vision=True, screenshot_dir=tmp_path)

    assert len(caplog.records) == 1
    assert str(tmp_path / step.screenshot) in caplog.records[0].getMessage()


def test_vision_off_does_not_log_warning(caplog):
    """use_vision=False (the default) never even checks for a screenshot file,
    so it must not emit the missing-screenshot warning."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]

    client = _RecordingClient(lambda idx, s=step: _realistic_reply(s))
    with caplog.at_level("WARNING", logger="pipeline.generation"):
        generate_step_text(step, client)

    assert caplog.records == []


def test_vision_on_with_existing_screenshot_does_not_log_warning(tmp_path, caplog):
    """A screenshot that does exist on disk must not trigger the
    missing-screenshot warning."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]
    screenshot_path = tmp_path / step.screenshot
    screenshot_path.write_bytes(b"fake-png-bytes-for-test")

    client = _RecordingClient(lambda idx, s=step: _realistic_reply(s))
    with caplog.at_level("WARNING", logger="pipeline.generation"):
        generate_step_text(step, client, use_vision=True, screenshot_dir=tmp_path)

    assert caplog.records == []


def test_generate_all_steps_preserves_order_and_ids():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    client = _RecordingClient(lambda idx: "irrelevant text triggering fallback")
    results = generate_all_steps(manifest, client)
    assert [r["step_id"] for r in results] == [s.id for s in manifest.steps]


def test_generate_all_steps_reports_progress_after_each_step():
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    client = _RecordingClient(lambda idx: "irrelevant text triggering fallback")
    calls = []
    generate_all_steps(manifest, client, on_progress=lambda i, n: calls.append((i, n)))
    total = len(manifest.steps)
    assert calls == [(i, total) for i in range(1, total + 1)]


def test_build_prompt_does_not_double_wrap_the_empty_window_fallback():
    """Regression: a real qwen3:32b reply once echoed the prompt's own
    "in the 'the current window' window" almost verbatim -- caused by this
    prompt re-wrapping the already-worded fallback phrase in quotes +
    "window" a second time. template.py's _location_phrase never made this
    mistake; the prompt must match it."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[2].model_copy(
        update={"window": manifest.steps[2].window.model_copy(update={"title": ""})}
    )
    prompt = _build_prompt(step)
    assert "in the current window" in prompt
    assert "the current window' window" not in prompt
    assert "'the current window'" not in prompt


def test_markdown_bold_is_stripped_from_a_passing_generation():
    """Regression: a real qwen3:32b reply for a step that otherwise passed
    round_trip_ok included raw **bold** markers around the element name,
    which round_trip_ok doesn't check for -- they shipped straight into the
    rendered doc untouched. generate_step_text must strip them so a
    successful (non-fallback) generation is still plain prose."""
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    step = manifest.steps[0]  # click, 'Save' Button, SmartDeploy Console

    client = _RecordingClient(
        lambda idx, s=step: f"The user clicks **{s.element.name}** in the {s.window.title} window."
    )
    text, used_fallback = generate_step_text(step, client)
    assert used_fallback is False
    assert "**" not in text
    assert step.element.name in text


def test_concurrent_generation_preserves_manifest_order_despite_staggered_completion():
    """The as_completed-append footgun this guards against: collecting
    results in completion order instead of index order would land a fast
    LATER step's text at an EARLIER position. Reverse-staggered delays (later
    steps reply fastest) make that failure mode visible if it existed."""
    manifest = _make_manifest(6)
    delays = {
        step.element.name: (len(manifest.steps) - i) * 0.03 for i, step in enumerate(manifest.steps)
    }
    replies = {
        step.element.name: f"Click {step.element.name} in Test Window." for step in manifest.steps
    }
    client = _StaggeredClient(delays, replies)

    results = generate_all_steps(manifest, client, max_concurrency=4)

    assert [r["step_id"] for r in results] == [s.id for s in manifest.steps]
    for step, result in zip(manifest.steps, results):
        assert result["used_fallback"] is False
        assert step.element.name in result["text"]


def test_concurrency_cap_respected():
    manifest = _make_manifest(8)
    client = _StaggeredClient({step.element.name: 0.05 for step in manifest.steps})
    generate_all_steps(manifest, client, max_concurrency=3)
    assert client.peak_in_flight <= 3


class _VisionRecordingClient:
    """Thread-safe stub that records every call's raw content (str or the
    two-block vision list) and replies realistically -- used to prove
    use_vision/screenshot_dir reach generate_step_text through BOTH of
    generate_all_steps' code paths, sequential and ThreadPool."""

    def __init__(self, reply_for_name):
        self.reply_for_name = reply_for_name
        self._lock = threading.Lock()
        self.calls = []

    def chat(self, messages, **kwargs):
        content = messages[0]["content"]
        text = content[0]["text"] if isinstance(content, list) else content
        name = next(n for n in self.reply_for_name if n in text)
        with self._lock:
            self.calls.append(content)
        return self.reply_for_name[name]


def test_generate_all_steps_forwards_use_vision_sequential_path(tmp_path):
    """max_concurrency=1 (default) is the sequential loop -- use_vision=True
    plus real screenshots on disk must reach generate_step_text there too."""
    manifest = _make_manifest(4)
    for step in manifest.steps:
        (tmp_path / step.screenshot).write_bytes(b"fake-png-bytes-for-test")
    reply_for_name = {
        step.element.name: f"Click {step.element.name} in Test Window." for step in manifest.steps
    }
    client = _VisionRecordingClient(reply_for_name)

    results = generate_all_steps(manifest, client, use_vision=True, screenshot_dir=tmp_path)

    assert len(client.calls) == len(manifest.steps)
    assert all(not r["used_fallback"] for r in results)
    for step, content in zip(manifest.steps, client.calls):
        screenshot_path = tmp_path / step.screenshot
        assert content == [
            {"type": "text", "text": _build_prompt(step)},
            {"type": "image_url", "image_url": {"url": _image_data_url(screenshot_path)}},
        ]


def test_generate_all_steps_forwards_use_vision_concurrent_pool_path(tmp_path):
    """max_concurrency>1 dispatches through the ThreadPool pool.submit path --
    the easy mistake this guards against is wiring use_vision/screenshot_dir
    into only the sequential branch and forgetting this one."""
    manifest = _make_manifest(6)
    for step in manifest.steps:
        (tmp_path / step.screenshot).write_bytes(b"fake-png-bytes-for-test")
    reply_for_name = {
        step.element.name: f"Click {step.element.name} in Test Window." for step in manifest.steps
    }
    client = _VisionRecordingClient(reply_for_name)

    results = generate_all_steps(
        manifest, client, max_concurrency=3, use_vision=True, screenshot_dir=tmp_path
    )

    assert [r["step_id"] for r in results] == [s.id for s in manifest.steps]
    assert len(client.calls) == len(manifest.steps)
    assert all(not r["used_fallback"] for r in results)
    for call in client.calls:
        assert isinstance(call, list)
        assert call[0]["type"] == "text"
        assert call[1]["type"] == "image_url"
    for step in manifest.steps:
        screenshot_path = tmp_path / step.screenshot
        expected = [
            {"type": "text", "text": _build_prompt(step)},
            {"type": "image_url", "image_url": {"url": _image_data_url(screenshot_path)}},
        ]
        assert expected in client.calls


def test_generate_all_steps_use_vision_default_false_stays_plain_string_concurrent(tmp_path):
    """use_vision defaults to False even when max_concurrency>1 -- the
    ThreadPool path must stay byte-identical to the pre-vision call."""
    manifest = _make_manifest(4)
    for step in manifest.steps:
        (tmp_path / step.screenshot).write_bytes(b"fake-png-bytes-for-test")
    reply_for_name = {
        step.element.name: f"Click {step.element.name} in Test Window." for step in manifest.steps
    }
    client = _VisionRecordingClient(reply_for_name)

    generate_all_steps(manifest, client, max_concurrency=3)

    assert len(client.calls) == len(manifest.steps)
    for call in client.calls:
        assert isinstance(call, str)


def test_default_is_sequential():
    manifest = _make_manifest(5)
    client = _StaggeredClient({step.element.name: 0.01 for step in manifest.steps})
    generate_all_steps(manifest, client)  # max_concurrency defaults to 1
    assert client.peak_in_flight == 1


def test_per_step_fallback_isolated_under_concurrency():
    manifest = _make_manifest(5)
    failing_name = manifest.steps[2].element.name
    delays = {step.element.name: 0.01 for step in manifest.steps}
    replies = {
        step.element.name: f"Click {step.element.name} in Test Window." for step in manifest.steps
    }

    class _FailingOneClient(_StaggeredClient):
        def chat(self, messages, **kwargs):
            name = self._name_in(messages[0]["content"])
            if name == failing_name:
                raise RuntimeError("simulated LLM outage for this one step")
            return super().chat(messages, **kwargs)

    client = _FailingOneClient(delays, replies)
    progress = []
    results = generate_all_steps(
        manifest, client, max_concurrency=3, on_progress=lambda i, n: progress.append((i, n))
    )

    assert [r["step_id"] for r in results] == [s.id for s in manifest.steps]
    for i, (step, result) in enumerate(zip(manifest.steps, results)):
        if i == 2:
            assert result["used_fallback"] is True
            assert result["text"] == render_step_template(step)
        else:
            assert result["used_fallback"] is False

    total = len(manifest.steps)
    assert progress == [(i, total) for i in range(1, total + 1)]
