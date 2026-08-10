"""Step generation orchestrator: per-record prompt -> LLM call -> round-trip
gate -> template fallback (invariants L2/L3, CLAUDE.md). At most one
generation attempt per distinct prompt per job, and never more than one per
step — "Round-trip validator: ... failures demonstrably fall back to
template, never retry loops." Any generation failure at all (bad HTTP
status, malformed response body missing/short `choices`, non-JSON reply,
round-trip mismatch) falls back immediately; nothing is ever retried, and
the failure mode never propagates to the caller — a broken LLM must never
take down doc generation.

generate_all_steps additionally memoizes identical prompts within one job
(two steps that produce byte-identical prompts share a single LLM call) --
see its docstring for the invariant-preserving details."""

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


def _request_reply(step, llm_client):
    """Makes exactly ONE chat() call for this step's prompt. Returns the raw
    reply str, or None if the call failed at all -- never raises, never
    retries."""
    prompt = _build_prompt(step)  # outside the try: a bug here is ours, not the LLM's
    try:
        return llm_client.chat([{"role": "user", "content": prompt}])
    except Exception:  # noqa: BLE001 - any generation failure means fallback, never retry
        return None


def _finalize_reply(reply, step):
    """Strips markdown emphasis, then runs round_trip_ok for THIS step.
    Returns (text, used_fallback). Always runs per step, even when `reply`
    came from a prompt shared with other steps (generate_all_steps'
    memoization) -- a cached reply is re-gated against every step that
    reuses it, so invariants L2/L3 hold per step regardless of which steps
    happened to share an LLM call. reply=None (the call itself failed)
    always falls back."""
    if reply is None:
        return render_step_template(step), True
    text = _strip_markdown_emphasis(reply).strip()
    ok, _mismatches = round_trip_ok(text, step)
    if not ok:
        return render_step_template(step), True
    return text, False


def generate_step_text(step, llm_client):
    """Returns (text, used_fallback). Exactly one LLM call attempt; any
    failure — HTTP error, malformed response body, or a round-trip mismatch
    — falls back to the template, never retried."""
    reply = _request_reply(step, llm_client)
    return _finalize_reply(reply, step)


def generate_all_steps(manifest, llm_client, on_progress=None, max_concurrency=1):
    """Returns [{"step_id", "text", "used_fallback"}, ...] in manifest order
    — invariant L1's 1:1 mapping preserved regardless of max_concurrency.
    `on_progress`, if given, is called as `on_progress(completed, total)`
    once per STEP (not per LLM call) so a caller (e.g. the session's job
    status) still sees progress reach (total, total) even when several
    steps share one call.

    Memoization: steps whose _build_prompt output is byte-identical (e.g.
    several checkboxes ticked in the same dialog) share at most ONE LLM call
    for that prompt -- a real capture session's most repetitive stretches
    are exactly where this saves the most wall-clock. round_trip_ok still
    runs per step against the shared reply (_finalize_reply), so two steps
    sharing a prompt can still land on different fallback outcomes if their
    manifest fields differ in a way the round-trip check cares about; a
    shared call's failure falls every step in that group back to its own
    (still complete and correct) template.

    max_concurrency=1 (default) keeps the original strictly sequential loop
    — safest against an Ollama instance that isn't tuned for concurrent
    requests (an untuned server just queues them, and a queued step can then
    blow its own per-request timeout into a template fallback it didn't
    need). >1 dispatches at most one request per distinct prompt group to a
    bounded thread pool, the same order-preserving pattern vision.py's
    caption_images already uses: results are placed by index into a
    pre-sized list, never appended in completion order, so a step that
    happens to finish first can never land in the wrong position."""
    total = len(manifest.steps)

    if max_concurrency <= 1 or total <= 1:
        results = []
        memo = {}  # prompt -> reply (None means that prompt's call already failed)
        for i, step in enumerate(manifest.steps, start=1):
            prompt = _build_prompt(step)
            if prompt in memo:
                reply = memo[prompt]
            else:
                reply = _request_reply(step, llm_client)
                memo[prompt] = reply
            text, used_fallback = _finalize_reply(reply, step)
            results.append({"step_id": step.id, "text": text, "used_fallback": used_fallback})
            if on_progress:
                on_progress(i, total)
        return results

    results = [None] * total
    # Group step indices by prompt (insertion order == manifest order) so
    # only one request is dispatched per distinct prompt.
    groups = {}
    for i, step in enumerate(manifest.steps):
        groups.setdefault(_build_prompt(step), []).append(i)

    with ThreadPoolExecutor(max_workers=min(max_concurrency, len(groups))) as pool:
        futures = {
            pool.submit(_request_reply, manifest.steps[indices[0]], llm_client): indices
            for indices in groups.values()
        }
        done = 0
        for future in as_completed(futures):
            indices = futures[future]
            reply = future.result()
            for i in indices:
                step = manifest.steps[i]
                text, used_fallback = _finalize_reply(reply, step)
                results[i] = {"step_id": step.id, "text": text, "used_fallback": used_fallback}
                done += 1
                if on_progress:
                    on_progress(done, total)
    return results
