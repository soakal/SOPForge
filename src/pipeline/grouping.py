"""Phase grouping (Phase 6c): steps grouped into workflow phases, SOP Factory
style ("2. Sign in", "3. Request", ...), plus omission of exact-duplicate
screenshots. See phases/DEVIATIONS.md for why this keeps every invariant.

The LLM never decides what the steps are. It may only propose where each
phase starts and what it is called; propose_phases() turns that into a
contiguous partition by construction and validate_phases() re-checks full,
in-order coverage. Any failure falls back to group_by_window(), with no
retry loop. The chosen phases are saved to the session's phases.json so a
later re-export (the per-step edit path, which makes no LLM call) renders
the same structure."""

import json
import logging
from dataclasses import dataclass

from PIL import Image, ImageChops

logger = logging.getLogger(__name__)

DEFAULT_PHASE_NAME = "Procedure"
MIN_STEPS_TO_GROUP = 4
MAX_PHASE_NAME_LEN = 60
# Two annotated crops count as the same picture when under this fraction of
# their pixels changed by more than CHANGED_PIXEL_LEVEL (grayscale, 0-255).
# A mean difference is too lax: a click marker that moved, or one changed
# field, is a small share of the frame yet is exactly what the reader needs.
CHANGED_PIXEL_LEVEL = 32
DUPLICATE_MAX_CHANGED_FRACTION = 0.002

_BROWSER_SUFFIXES = (
    " - Google Chrome",
    " - Microsoft Edge",
    " - Microsoft​ Edge",
    " - Mozilla Firefox",
    " — Mozilla Firefox",
)


@dataclass(frozen=True)
class Phase:
    name: str
    step_ids: tuple[str, ...]


def default_phases(manifest):
    """One phase holding every step: exactly the pre-6c document layout."""
    return [Phase(DEFAULT_PHASE_NAME, tuple(step.id for step in manifest.steps))]


def validate_phases(phases, manifest):
    """True iff phases is a contiguous, order-preserving partition of the
    manifest's steps (each id exactly once) with usable names."""
    if not phases:
        return False
    flat = [step_id for phase in phases for step_id in phase.step_ids]
    if flat != [step.id for step in manifest.steps]:
        return False
    return all(
        phase.step_ids and phase.name.strip() and len(phase.name) <= MAX_PHASE_NAME_LEN
        for phase in phases
    )


def _window_phase_name(step):
    title = step.window.title.strip()
    for suffix in _BROWSER_SUFFIXES:
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
            break
    title = title or step.window.process.removesuffix(".exe")
    return title[:MAX_PHASE_NAME_LEN].strip() or DEFAULT_PHASE_NAME


def group_by_window(manifest):
    """Deterministic fallback: consecutive steps in the same window form a
    phase. A one-step detour between two runs of the same window (a taskbar
    click, a stray focus change) rejoins that window's phase; any other
    one-step run joins the phase before it (or after it, if first), so no
    phase is a single stray click. A single remaining run is the default."""
    if len(manifest.steps) < MIN_STEPS_TO_GROUP:
        return default_phases(manifest)
    runs = []
    for step in manifest.steps:
        key = (step.window.process, step.window.title)
        if runs and runs[-1][0] == key:
            runs[-1][1].append(step)
        else:
            runs.append((key, [step]))
    i = 1
    while i < len(runs) - 1:
        if len(runs[i][1]) == 1 and runs[i - 1][0] == runs[i + 1][0]:
            runs[i - 1][1].extend(runs[i][1] + runs[i + 1][1])
            del runs[i : i + 2]
        else:
            i += 1
    merged = []
    for key, steps in runs:
        if merged and len(steps) == 1:
            merged[-1][1].extend(steps)
        else:
            merged.append((key, list(steps)))
    if len(merged) > 1 and len(merged[0][1]) == 1:
        merged[1] = (merged[1][0], merged[0][1] + merged[1][1])
        merged.pop(0)
    if len(merged) == 1:
        return default_phases(manifest)
    return [
        Phase(_window_phase_name(steps[0]), tuple(step.id for step in steps))
        for _key, steps in merged
    ]


def _proposal_prompt(manifest, step_results):
    text_by_id = {r["step_id"]: r["text"] for r in step_results}
    lines = [
        f"{n}. [{step.window.title or step.window.process}] {text_by_id.get(step.id, '')}"
        for n, step in enumerate(manifest.steps, start=1)
    ]
    return (
        "These are the numbered steps of a recorded software procedure, with the "
        "window each happened in. Split them into 2 to 6 consecutive phases a "
        "reader would recognize (e.g. 'Sign in', 'Create the job', 'Verify'). "
        "Do not reorder, merge, drop or rename steps; only choose where each phase "
        f"starts. Phase names: short, title case, at most {MAX_PHASE_NAME_LEN} "
        "characters. Reply with JSON only, a list like "
        '[{"name": "Sign in", "start": 1}, {"name": "Request", "start": 4}]. '
        "The first phase must start at 1.\n\n" + "\n".join(lines)
    )


def parse_proposal(reply, manifest):
    """Phases from an LLM reply, or None if it isn't a usable proposal."""
    start, end = reply.find("["), reply.rfind("]")
    if start < 0 or end < start:
        return None
    try:
        items = json.loads(reply[start : end + 1])
    except ValueError:
        return None
    if not isinstance(items, list) or not items:
        return None
    n = len(manifest.steps)
    starts, names = [], []
    for item in items:
        if not isinstance(item, dict):
            return None
        name, first = item.get("name"), item.get("start")
        if not isinstance(name, str) or not isinstance(first, int) or isinstance(first, bool):
            return None
        starts.append(first)
        names.append(" ".join(name.split()))
    if starts[0] != 1 or any(b <= a for a, b in zip(starts, starts[1:])) or starts[-1] > n:
        return None
    ids = [step.id for step in manifest.steps]
    bounds = [s - 1 for s in starts] + [n]
    phases = [Phase(name, tuple(ids[bounds[i] : bounds[i + 1]])) for i, name in enumerate(names)]
    return phases if validate_phases(phases, manifest) else None


def propose_phases(manifest, step_results, llm_client):
    """(phases, source): source is "llm" when the model's proposal was
    accepted, else "window" for the deterministic fallback. Never raises."""
    if len(manifest.steps) < MIN_STEPS_TO_GROUP:
        return default_phases(manifest), "default"
    if llm_client is not None:
        try:
            reply = llm_client.chat(
                [{"role": "user", "content": _proposal_prompt(manifest, step_results)}]
            )
            phases = parse_proposal(reply or "", manifest)
            if phases is not None:
                return phases, "llm"
        except Exception as exc:  # noqa: BLE001 - grouping is layout only, never blocks the doc
            logger.warning("phase proposal failed (%s); grouping by window", exc)
    return group_by_window(manifest), "window"


def phases_to_json(phases, source, keep_screenshots=()):
    """phases.json content. keep_screenshots: step ids a reviewer restored,
    whose screenshot stays even when duplicate_screenshot_steps flags it.
    A writer that changes only the phases (e.g. a rename) must pass the
    existing load_keep_screenshots() value through, or restores are lost."""
    data = {
        "source": source,
        "phases": [{"name": p.name, "step_ids": list(p.step_ids)} for p in phases],
    }
    if keep_screenshots:
        data["keep_screenshots"] = sorted(keep_screenshots)
    return data


def load_keep_screenshots(path):
    """Restored step ids from phases.json; empty if absent or unreadable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return frozenset(str(i) for i in data.get("keep_screenshots", []))
    except (OSError, ValueError, TypeError, AttributeError):
        return frozenset()


def load_phases(path, manifest):
    """Saved phases if the file exists and still exactly partitions this
    manifest (a later step hide/confirm can change the step set); otherwise
    the window grouping, so a stale file can never drop or reorder a step."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        phases = [Phase(p["name"], tuple(p["step_ids"])) for p in data["phases"]]
    except (OSError, ValueError, KeyError, TypeError):
        return group_by_window(manifest)
    return phases if validate_phases(phases, manifest) else group_by_window(manifest)


def _same_target(a, b):
    return (
        (a.window.process, a.window.title) == (b.window.process, b.window.title)
        and (a.element.name or a.element.control_type)
        and (a.element.name, a.element.control_type, a.element.automation_id)
        == (b.element.name, b.element.control_type, b.element.automation_id)
    )


def _images_match(path_a, path_b):
    try:
        with Image.open(path_a) as a, Image.open(path_b) as b:
            if a.size != b.size:
                return False
            diff = ImageChops.difference(a.convert("L"), b.convert("L"))
            changed = sum(diff.histogram()[CHANGED_PIXEL_LEVEL + 1 :])
            return changed < DUPLICATE_MAX_CHANGED_FRACTION * a.width * a.height
    except OSError:
        return False


def duplicate_screenshot_steps(manifest, annotated_paths, phases):
    """Step ids whose annotated screenshot repeats the previous step's within
    the same phase: same window, same control, near-identical crop (e.g. a
    double-click). Annotated images are already cropped tight to the clicked
    control, so anything short of an exact repeat carries new information
    and is kept. The first step of a phase always keeps its picture."""
    path_by_id = {step.id: path for step, path in zip(manifest.steps, annotated_paths)}
    step_by_id = {step.id: step for step in manifest.steps}
    omitted = []
    for phase in phases:
        for prev_id, step_id in zip(phase.step_ids, phase.step_ids[1:]):
            prev, step = step_by_id[prev_id], step_by_id[step_id]
            if _same_target(prev, step) and _images_match(path_by_id[prev_id], path_by_id[step_id]):
                omitted.append(step_id)
    return omitted
