"""Live proof of the full 4-stage pipeline (steps -> narrative -> vision ->
polish) against the real Ollama host, driven through the real-capture
manifest build path -- a `fixtures/manifest.json` + screenshots, NOT
`photo_build.py` (that manifest-free mode is a different code path that
skips the LLM/round-trip gate entirely for steps).

WHAT THIS DRIVES AND WHY
-------------------------
Steps / narrative / polish are driven through a REAL FastAPI TestClient
session lifecycle -- `pipeline.server.create_app()` + `POST /sessions` +
polling `/status` -- the exact same `_ingest_session` -> `_generate()` ->
`_write_all_exports()` code path a real capture upload takes. The three
LLM-client factories `create_app()` accepts are given real `LLMClient`
instances (built from `config/models.toml`'s `[steps]`/`[narrative]`/
`[polish]` sections, at the real configured Ollama host/models) wrapped in
a `_CapturingClient` that records every raw chat() call and reply for this
script's own assertions -- the wrapper changes nothing about behavior, it
only observes. `config/models.toml` itself is opened read-only (never
written): polish is off there by default, so the initial session creation
exercises steps + narrative live; the polish stage is separately exercised
live via `POST /sessions/{id}/rerender?polish=local` -- the existing
per-job override server.py already ships (resolve_polish_config), which
forces the local ollama polish backend for that one job without touching
the config file on disk.

DEVIATION -- vision is NOT reachable through the real-capture path
--------------------------------------------------------------------
`grep -rn caption_images src/` shows `pipeline.vision.caption_images` is
called from exactly one place in the whole codebase: `server.py`'s
`_generate_photo()`, the manifest-free "screenshots + transcript" build
mode (`POST /ui/build`) -- explicitly the path the plan says NOT to use.
`_generate()`, the real-capture path this script drives for the other
three stages, never imports or calls `pipeline.vision` at all. There is no
way to exercise a live vision call through the real-capture HTTP path
because the production code simply does not wire vision into it -- and
per the rules for this cycle, server.py is read-only (no rewiring it in).

So, per the plan's own fallback instruction ("call the underlying pipeline
functions directly in sequence... vision folded in if it's part of the
steps stage" -- it is not), this script calls `pipeline.vision.
caption_images()` directly and live, against the SAME fixture screenshots
and the SAME synthesized narration the real-capture session just produced,
using the real `[vision]` config section (qwen2.5vl:7b). This proves the
vision stage genuinely executes against the live LLM and produces
non-degenerate captions (vision.py's own `degenerate_reason` detector is
the fallback signal here, exactly parallel to steps' `used_fallback` /
report's `template_fallback_steps`) -- it does not (because it cannot)
prove vision is wired into the real-capture session lifecycle, since it
factually is not in this codebase today.

FIXTURE
-------
`fixtures/sample-manifest.json` (3 steps, a mix of click/type actions, one
step with deliberately empty UIA element metadata) -- small per the risk
note. `fixtures/` ships no screenshots, so this script synthesizes simple
but non-blank PNGs (a title bar + a labelled rectangle near each step's
click coordinates) so the vision model has something concrete to caption,
matching each step's window/element metadata.

PASS/FAIL CONTRACT
-------------------
Exit 0 requires ALL of:
  - Ollama host reachable, and every required model (steps/narrative/
    vision/polish-local) present in `GET /api/tags`.
  - The real-capture session reaches status "done" (not "error").
  - report.json's `template_fallback_steps` is EMPTY (steps stage: zero
    template fallback -- the mandated deterministic fallback detector).
  - The narrative stage made at least one live, non-empty chat() call
    (2*passes - 1 expected) AND the assembled doc.md carries non-empty
    narrative content -- proof `generate_narrative` ran for real and its
    result wasn't swallowed by `_generate()`'s best-effort except.
  - `caption_images()` returns a caption (not None -- i.e. not degenerate,
    not a request failure) for every one of the 3 screenshots.
  - The rerender-with-`polish=local` job also reaches "done", and the
    polish capturing client shows a live chat() call actually completed
    (an HTTP round trip to the configured [polish] model happened and returned a reply) --
    per the plan's risk note, this script does NOT fail the proof if that
    reply is gate-rejected or generate_polish_pass() falls back to the
    original text; only a failure to even reach the model live is fatal
    for the polish stage. The gate/fallback outcome is printed either way.

Usage: python scripts/proof_pipeline_e2e_live.py
Exit code 0 = proof succeeded. Exit code 1 = proof failed (reported loudly).
"""

import logging
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

# The docx export stage (_write_all_exports -> docx_assembler.py) drives the
# external, unversioned SOP Factory 2 engine ("sop_lib") -- not vendored into
# this repo (CLAUDE.md: "SOP_Factory_2 engine (sop_lib) is external, not in
# the repo... For dev/tests, set SOPFORGE_SOP_FACTORY_2_DIR to the bundled
# copy at dist/sopforge-server/_internal/sop_factory_2"). Without this, a
# real (non-mocked) end-to-end generation run fails at the docx-export step
# with "No module named 'sop_lib'" -- unrelated to the LLM stages this script
# actually proves, but generation.raise on any exception still marks the
# whole session status "error" before report.json/doc.md are ever written.
# setdefault, not unconditional, so an operator's own override still wins.
os.environ.setdefault(
    "SOPFORGE_SOP_FACTORY_2_DIR",
    str(REPO_ROOT / "dist" / "sopforge-server" / "_internal" / "sop_factory_2"),
)

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from pipeline.config import (  # noqa: E402
    load_models_config,
    provider_api_key,
    provider_endpoint,
    resolve_polish_config,
)
from pipeline.llm_client import LLMClient  # noqa: E402
from pipeline.polish import _gate, _normalize  # noqa: E402
from pipeline.server import create_app  # noqa: E402
from pipeline.vision import caption_images  # noqa: E402

logging.basicConfig(level=logging.WARNING)

CONFIG_PATH = REPO_ROOT / "config" / "models.toml"  # read-only reference, never written
FIXTURE_MANIFEST = REPO_ROOT / "fixtures" / "sample-manifest.json"
CALL_TIMEOUT = 600.0  # generous -- real 27b/32b-class local models, per the risk note;
# bumped from 300s after an observed live ReadTimeout on a narrative
# critique/revise call at 300s
JOB_POLL_TIMEOUT = 1800.0  # 30 min ceiling per job -- multi-pass narrative + 3 steps is slow


class _CapturingClient:
    """Wraps a real LLMClient, recording every chat() call's reply (or
    exception) without altering behavior at all -- same non-invasive
    pattern as proof_polish_live.py's _CapturingClient, generalized to
    record every call instead of just the last one, since this script
    needs to prove narrative's multiple draft/critique/revise passes each
    actually happened live."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = []  # [{"reply": str|None, "error": repr|None}, ...]

    def chat(self, messages, **kwargs):
        try:
            reply = self._inner.chat(messages, **kwargs)
        except Exception as exc:  # noqa: BLE001 - record then propagate, caller falls back
            self.calls.append({"reply": None, "error": repr(exc)})
            raise
        self.calls.append({"reply": reply, "error": None})
        return reply

    def close(self):
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()


def check_ollama_reachable(base_url, timeout=5.0):
    root = base_url.split("/v1")[0].rstrip("/")
    url = f"{root}/api/tags"
    try:
        resp = httpx.get(url, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - report and abort, not a silent pass
        print(f"FAIL: Ollama host unreachable at {url}: {exc}")
        sys.exit(1)
    return resp.json()


def _draw_screenshot(step, path, size=(1920, 1080)):
    """A simple but non-blank UI mockup: a title bar labelled with the
    step's window title, and a rectangle labelled with the clicked
    element's name (or a generic marker for the empty-metadata step),
    positioned near the manifest's own screen coordinates -- so the vision
    model has concrete, step-specific content to caption instead of a
    blank canvas that would trivially fail vision.textgate's degenerate
    check."""
    img = Image.new("RGB", size, (245, 245, 248))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, size[0], 40], fill=(30, 60, 120))
    title = step.window.title or "(untitled window)"
    draw.text((12, 12), title, fill=(255, 255, 255))

    x, y = step.screen.x, step.screen.y
    label = step.element.name or step.element.control_type or "target"
    box = [max(0, x - 90), max(0, y - 25), x + 90, y + 25]
    draw.rectangle(box, outline=(200, 60, 60), width=3)
    draw.text((box[0] + 6, box[1] + 6), f"{step.action}: {label}", fill=(20, 20, 20))
    img.save(path)


def build_session_payload(session_tmp_dir):
    """Loads fixtures/sample-manifest.json and synthesizes a matching PNG
    per step into session_tmp_dir. Returns (manifest_json_text, files) in
    the exact shape tests/pipeline/test_server.py's own POST /sessions
    helper uses."""
    from pipeline.manifest import load_manifest

    manifest = load_manifest(FIXTURE_MANIFEST)
    shots_dir = session_tmp_dir / "shots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for step in manifest.steps:
        p = shots_dir / step.screenshot
        _draw_screenshot(step, p)
        files.append(("files", (step.screenshot, p.open("rb"), "image/png")))
    return FIXTURE_MANIFEST.read_text(encoding="utf-8"), files, manifest


def wait_for_terminal_status(client, session_id, label, timeout=JOB_POLL_TIMEOUT):
    deadline = time.monotonic() + timeout
    last_progress = None
    while time.monotonic() < deadline:
        status = client.get(f"/sessions/{session_id}/status").json()
        progress = status.get("progress")
        if progress != last_progress:
            print(f"  [{label}] status={status['status']!r} progress={progress}")
            last_progress = progress
        if status["status"] in ("done", "error"):
            return status
        time.sleep(2.0)
    print(f"FAIL: {label} never reached a terminal status within {timeout}s")
    sys.exit(1)


def main():
    print("=== proof_pipeline_e2e_live: 4-stage pipeline against the live Ollama host ===")
    print(f"Config: {CONFIG_PATH}")
    cfg = load_models_config(CONFIG_PATH)
    print(
        f"  steps:     provider={cfg.steps.provider!r} model={cfg.steps.model!r} "
        f"endpoint={cfg.steps.endpoint!r}"
    )
    print(
        f"  narrative: provider={cfg.narrative.provider!r} model={cfg.narrative.model!r} "
        f"passes={cfg.narrative.passes}"
    )
    print(
        f"  vision:    enabled={cfg.vision.enabled} provider={cfg.vision.provider!r} "
        f"model={cfg.vision.model!r}"
    )
    print(f"  polish:    (this run forces 'local' override) model={cfg.polish.model!r}")

    print(f"\nChecking Ollama reachability at {cfg.steps.endpoint} ...")
    tags = check_ollama_reachable(cfg.steps.endpoint)
    names = {m.get("name") for m in tags.get("models", [])}
    print(f"OK: host reachable, {len(names)} models present.")

    required_models = {cfg.steps.model, cfg.narrative.model, cfg.vision.model, cfg.polish.model}
    missing = required_models - names
    if missing:
        print(f"FAIL: required model(s) not found in /api/tags: {sorted(missing)}")
        print(f"      available: {sorted(names)}")
        sys.exit(1)
    print(f"OK: all required models present: {sorted(required_models)}")

    tmp_root = Path(tempfile.mkdtemp(prefix="sopforge_e2e_live_"))
    print(f"\nWorking dir: {tmp_root}")

    steps_clients, narrative_clients, polish_clients = [], [], []

    def steps_factory():
        client = _CapturingClient(LLMClient(cfg.steps, timeout=CALL_TIMEOUT))
        steps_clients.append(client)
        return client

    def narrative_factory():
        client = _CapturingClient(LLMClient(cfg.narrative, timeout=CALL_TIMEOUT))
        narrative_clients.append(client)
        return client

    def polish_factory(section):
        client = _CapturingClient(LLMClient(section, timeout=CALL_TIMEOUT))
        polish_clients.append(client)
        return client

    app = create_app(
        sessions_root=tmp_root / "sessions",
        config_path=CONFIG_PATH,
        llm_client_factory=steps_factory,
        narrative_llm_client_factory=narrative_factory,
        polish_llm_client_factory=polish_factory,
    )
    client = TestClient(app)

    # ---- Stage 1 + 2: real-capture session -> steps + narrative live ----
    print("\n--- Creating real-capture session (steps + narrative live) ---")
    manifest_json, files, manifest = build_session_payload(tmp_root)
    resp = client.post("/sessions", data={"manifest_json": manifest_json}, files=files)
    if resp.status_code != 200:
        print(f"FAIL: POST /sessions returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    session_id = resp.json()["session_id"]
    print(f"OK: session created: {session_id}")

    status = wait_for_terminal_status(client, session_id, "initial generation")
    if status["status"] != "done":
        print(f"FAIL: initial generation ended in status={status['status']!r}: {status.get('error')}")
        sys.exit(1)
    print("OK: initial generation reached status=done.")

    report = client.get(f"/sessions/{session_id}/report").json()
    fallback_steps = report.get("template_fallback_steps", [])
    print(f"report.template_fallback_steps = {fallback_steps}")
    if fallback_steps:
        print(
            "FAIL: one or more steps fell back to the deterministic template instead of "
            "using the live LLM reply -- steps stage did not genuinely execute against "
            f"the LLM for: {fallback_steps}"
        )
        sys.exit(1)
    print(f"OK: zero template-fallback steps out of {len(manifest.steps)} -- steps stage genuinely live.")

    if not steps_clients or not any(c.calls for c in steps_clients):
        print("FAIL: no live chat() calls were ever made on the steps LLM client.")
        sys.exit(1)
    total_step_calls = sum(len(c.calls) for c in steps_clients)
    print(f"OK: {total_step_calls} live steps/title chat() call(s) recorded across {len(steps_clients)} client(s).")

    if not narrative_clients:
        print("FAIL: narrative_llm_client_factory was never called -- generate_narrative did not run.")
        sys.exit(1)
    narrative_client = narrative_clients[-1]
    expected_min_calls = 2 * cfg.narrative.passes - 1
    ok_calls = [c for c in narrative_client.calls if c["error"] is None and (c["reply"] or "").strip()]
    print(
        f"narrative: {len(narrative_client.calls)} chat() call(s) recorded "
        f"(expected >= {expected_min_calls} for passes={cfg.narrative.passes}), "
        f"{len(ok_calls)} succeeded with non-empty replies."
    )
    if len(ok_calls) < expected_min_calls:
        print(
            "FAIL: narrative stage did not make the expected number of successful, "
            "non-empty live chat() calls -- see calls above for errors/empty replies."
        )
        for i, c in enumerate(narrative_client.calls):
            print(f"  call {i}: error={c['error']!r} reply_len={len(c['reply'] or '') if c['reply'] else 0}")
        sys.exit(1)

    doc_md = client.get(f"/sessions/{session_id}/doc.md").text
    title_line, _, rest = doc_md.partition("\n")
    narrative_section = rest.split("\n## ", 1)[0].strip()
    print(f"doc.md narrative section length: {len(narrative_section)} chars")
    if not narrative_section:
        print(
            "FAIL: doc.md has no narrative content between the title and the first step "
            "heading -- generate_narrative()'s result was swallowed (narrative_text stayed "
            "None) despite live chat() calls having been recorded above."
        )
        sys.exit(1)
    print("OK: narrative stage genuinely executed against the LLM and its output shipped in doc.md.")
    print("---- narrative section (first 400 chars) ----")
    print(narrative_section[:400])
    # Captured now (polish is still off at this point -- config/models.toml's
    # [polish].enabled default), so this is a genuine pre-polish snapshot for
    # the diagnostic _gate()/echo check after the polish=local rerender below.
    pre_polish_md = doc_md

    # ---- Stage 3: vision, direct live call (see docstring for why) ----
    print("\n--- Calling pipeline.vision.caption_images() directly and live ---")
    shots_dir = tmp_root / "shots"
    image_paths = [shots_dir / step.screenshot for step in manifest.steps]
    narration_for_vision = narrative_section or manifest.session.title or "SOP capture session"
    captions = caption_images(
        image_paths,
        narration_for_vision,
        provider_endpoint(cfg.vision.provider, cfg.vision.endpoint),
        cfg.vision.model,
        api_key=provider_api_key(cfg.vision.provider),
        timeout=CALL_TIMEOUT,
        max_workers=cfg.vision.max_concurrency,
    )
    print(f"captions: {[('<None>' if c is None else f'{len(c)} chars') for c in captions]}")
    if len(captions) != len(image_paths) or any(c is None for c in captions):
        print(
            "FAIL: vision stage fell back (returned None) for at least one screenshot -- "
            "either a live call failed or textgate.degenerate_reason rejected the reply. "
            f"captions={captions}"
        )
        sys.exit(1)
    print(f"OK: {len(captions)}/{len(captions)} screenshots captioned live with non-degenerate output.")
    for i, cap in enumerate(captions, start=1):
        print(f"  [{i}] {cap}")

    # ---- Stage 4: polish, forced live via the per-job override ----
    print(
        f"\n--- Rerendering with polish=local (forces the live {cfg.polish.model!r} polish pass) ---"
    )
    resp = client.post(f"/sessions/{session_id}/rerender", params={"polish": "local"})
    if resp.status_code != 200:
        print(f"FAIL: POST /rerender?polish=local returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    status = wait_for_terminal_status(client, session_id, "rerender (polish=local)")
    if status["status"] != "done":
        print(f"FAIL: rerender ended in status={status['status']!r}: {status.get('error')}")
        sys.exit(1)
    print("OK: rerender reached status=done.")

    report_after = client.get(f"/sessions/{session_id}/report").json()
    fallback_after = report_after.get("template_fallback_steps", [])
    if fallback_after:
        print(f"FAIL: steps stage fell back on rerender too: {fallback_after}")
        sys.exit(1)
    print("OK: steps stage still zero-fallback on rerender.")

    if not polish_clients:
        print("FAIL: polish_llm_client_factory was never called -- polish stage did not run at all.")
        sys.exit(1)
    polish_client = polish_clients[-1]
    if not polish_client.calls:
        print("FAIL: polish LLM client was constructed but chat() was never called.")
        sys.exit(1)
    polish_call = polish_client.calls[-1]
    if polish_call["error"] is not None:
        print(
            f"FAIL: the live polish chat() call itself raised: {polish_call['error']!r} -- "
            "polish stage never reached the model, which IS fatal for this proof (a gate "
            "rejection or fallback afterward would not be, but a call that never completed "
            "is not evidence of anything running live)."
        )
        sys.exit(1)
    print(f"OK: polish stage made a live chat() call to {cfg.polish.model!r} that returned a reply.")

    reply = polish_call["reply"]
    ok, reason = _gate(pre_polish_md, reply) if reply else (False, "empty reply")
    is_echo = reply is not None and _normalize(reply) == _normalize(pre_polish_md)
    print(f"polish diagnostic (informational, NOT part of pass/fail): gate_ok={ok} reason={reason} echo={is_echo}")
    print(
        "NOTE: per the plan's risk note, polish's own tolerated ~25-50% echo/fallback "
        "contract means a gate rejection or echoed reply here is NOT a proof failure -- "
        "only a failed/never-made live call would be, and that already passed above."
    )

    print("\n=== PROOF SUCCEEDED: steps, narrative, vision (direct) and polish all made ===")
    print("=== genuine live calls against the configured Ollama models, with zero      ===")
    print("=== template-fallback steps and zero degenerate vision captions.            ===")


if __name__ == "__main__":
    main()
