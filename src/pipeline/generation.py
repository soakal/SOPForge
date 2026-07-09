"""Step generation orchestrator: per-record prompt -> LLM call -> round-trip
gate -> template fallback (invariants L2/L3, CLAUDE.md). Exactly one
generation attempt per step, ever — "Round-trip validator: ... failures
demonstrably fall back to template, never retry loops." Any generation
failure at all (bad HTTP status, malformed response body missing/short
`choices`, non-JSON reply, round-trip mismatch) falls back immediately;
nothing is ever retried, and the failure mode never propagates to the
caller — a broken LLM must never take down doc generation."""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from pipeline.roundtrip import round_trip_ok
from pipeline.template import render_step_template

# Bold markdown a model adds despite being told to write plain prose --
# round_trip_ok only checks verb/entity presence, never formatting, so raw
# **asterisks** would otherwise ship straight into the rendered doc
# untouched (observed from a real qwen3:32b reply: "The user clicks
# **File Explorer pinned** in the Taskbar window..."). Deliberately narrow
# (just the two standard bold delimiters) to avoid false-stripping a
# legitimate lone asterisk/underscore in ordinary prose.
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")


def _strip_markdown_emphasis(text):
    return _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2), text)


def _build_prompt(step):
    """A minimal, deterministic prompt built purely from manifest fields —
    the LLM only phrases what's already true, it never decides content
    (CLAUDE.md: "The LLM never decides what the steps are — only how to
    phrase them"). When window.title is empty, the fallback phrase is NOT
    re-wrapped in quotes + "window" -- doing that produced a literal,
    confusing "in the 'the current window' window" in the prompt itself,
    which a weaker model would sometimes echo almost verbatim instead of
    smoothing over (template.py's own _location_phrase already avoids this
    exact trap; the prompt needs to match it).

    Requests imperative second-person instructions ("Click the X button...")
    rather than third-person description ("a user clicks...") -- matching
    template.py's own render_step_template phrasing, so a document that mixes
    LLM-generated and template-fallback steps (any single LLM failure/
    round-trip miss) doesn't read as visibly stitched together from two
    different voices."""
    action_word = "Click" if step.action == "click" else "Enter a value into"
    target = step.element.name or step.element.control_type or "an element"
    location = (
        f"in the '{step.window.title}' window" if step.window.title else "in the current window"
    )
    return (
        f"Write one short imperative instruction telling the reader to "
        f"{action_word.lower()} '{target}' {location} -- second-person imperative "
        f'voice (e.g. "Click the Save button..."), not a description of what a '
        f"user does. Plain prose only, no markdown formatting (no **bold**, "
        f"no asterisks). Be factual and concise."
    )


def generate_step_text(step, llm_client):
    """Returns (text, used_fallback). Exactly one LLM call attempt; any
    failure — HTTP error, malformed response body, or a round-trip mismatch
    — falls back to the template, never retried."""
    prompt = _build_prompt(step)  # outside the try: a bug here is ours, not the LLM's
    try:
        reply = llm_client.chat([{"role": "user", "content": prompt}])
    except Exception:  # noqa: BLE001 - any generation failure means fallback, never retry
        return render_step_template(step), True

    reply = _strip_markdown_emphasis(reply).strip()
    ok, _mismatches = round_trip_ok(reply, step)
    if not ok:
        return render_step_template(step), True
    return reply, False


def generate_all_steps(manifest, llm_client, on_progress=None, max_concurrency=1):
    """Returns [{"step_id", "text", "used_fallback"}, ...] in manifest order
    — one generation attempt per step, invariant L1's 1:1 mapping preserved
    regardless of max_concurrency. `on_progress`, if given, is called as
    `on_progress(completed, total)` after each step so a caller (e.g. the
    session's job status) can report how far along a long generation run is.

    max_concurrency=1 (default) keeps the original strictly sequential loop
    — safest against an Ollama instance that isn't tuned for concurrent
    requests (an untuned server just queues them, and a queued step can then
    blow its own per-request timeout into a template fallback it didn't
    need). >1 dispatches steps to a bounded thread pool, the same
    order-preserving pattern vision.py's caption_images already uses:
    results are placed by index into a pre-sized list, never appended in
    completion order, so a step that happens to finish first can never land
    in the wrong position."""
    total = len(manifest.steps)
    if max_concurrency <= 1 or total <= 1:
        results = []
        for i, step in enumerate(manifest.steps, start=1):
            text, used_fallback = generate_step_text(step, llm_client)
            results.append({"step_id": step.id, "text": text, "used_fallback": used_fallback})
            if on_progress:
                on_progress(i, total)
        return results

    results = [None] * total
    with ThreadPoolExecutor(max_workers=min(max_concurrency, total)) as pool:
        futures = {
            pool.submit(generate_step_text, step, llm_client): (i, step)
            for i, step in enumerate(manifest.steps)
        }
        done = 0
        for future in as_completed(futures):
            i, step = futures[future]
            text, used_fallback = future.result()
            results[i] = {"step_id": step.id, "text": text, "used_fallback": used_fallback}
            done += 1
            if on_progress:
                on_progress(done, total)
    return results
