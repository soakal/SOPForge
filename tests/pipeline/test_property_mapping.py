"""Property test (invariant L1, CLAUDE.md): "set(doc.step_ids) ==
set(manifest.step_ids), order preserved." Proven across 1,000 randomly
generated schema-valid manifests via hypothesis — varying step count,
action, and empty-vs-populated element/window fields — not just the fixed
fixtures, since the assembler must never drop, invent, or reorder a step
id regardless of what the manifest's content looks like."""

from hypothesis import given, settings
from hypothesis import strategies as st

from pipeline.assembler import assemble_steps, check_1to1_mapping
from pipeline.manifest import load_manifest, select_manifest_steps
from pipeline.template import render_step_template

_WINDOWS = st.fixed_dictionaries(
    {
        "title": st.sampled_from(["", "Notepad", "Chrome - New Tab", "VS Code"]),
        "process": st.sampled_from(["", "notepad.exe", "chrome.exe", "code.exe"]),
        "class": st.sampled_from(["", "win32", "chromium", "electron"]),
    }
)
_ELEMENTS = st.fixed_dictionaries(
    {
        "name": st.sampled_from(["", "Save", "OK", "Computer name"]),
        "control_type": st.sampled_from(["", "Button", "Edit", "MenuItem"]),
        "automation_id": st.just(""),
        "framework": st.sampled_from(["", "Win32", "Chrome"]),
        "bounding_rect": st.one_of(st.none(), st.just([0, 0, 10, 10])),
    }
)


def _step_dict(index, action, window, element):
    step = {
        "id": f"step-{index + 1:03d}",
        "ts_utc": "2026-01-01T00:00:00Z",
        "action": action,
        "screen": {"x": index, "y": index * 2, "monitor": 1},
        "screenshot": f"{index + 1:03d}.png",
        "window": window,
        "element": element,
        "redactions": [],
    }
    if action == "click":
        step["button"] = "left"
    else:
        step["text_summary"] = "entered value in field (content not captured)"
    return step


def _steps_strategy(count):
    per_step = [
        st.builds(
            lambda action, window, element, i=i: _step_dict(i, action, window, element),
            st.sampled_from(["click", "type"]),
            _WINDOWS,
            _ELEMENTS,
        )
        for i in range(count)
    ]
    return st.tuples(*per_step).map(list)


def _manifest_strategy():
    return (
        st.integers(min_value=1, max_value=20)
        .flatmap(_steps_strategy)
        .map(
            lambda steps: {
                "schema_version": "1.0",
                "session": {
                    "id": "prop-test-session",
                    "title": "",
                    "started_utc": "2026-01-01T00:00:00Z",
                    "ended_utc": "2026-01-01T00:01:00Z",
                    "machine": "TEST",
                    "os_build": "1",
                    "narration_wav": None,
                },
                "steps": steps,
            }
        )
    )


@given(_manifest_strategy())
@settings(max_examples=1000, deadline=None)
def test_assemble_steps_never_drops_invents_or_reorders(manifest_data):
    manifest = load_manifest(manifest_data)
    doc_steps = assemble_steps(manifest, render_step_template)

    manifest_ids = [s.id for s in manifest.steps]
    doc_ids = [d["step_id"] for d in doc_steps]

    assert doc_ids == manifest_ids
    assert set(doc_ids) == set(manifest_ids)
    assert len(doc_steps) == len(manifest.steps)
    assert check_1to1_mapping(manifest, doc_steps) is True


@given(_manifest_strategy(), st.data())
@settings(max_examples=300, deadline=None)
def test_select_manifest_steps_any_permutation_preserves_1to1_mapping(manifest_data, data):
    """The steps-review page can drop an arbitrary, possibly non-contiguous
    subset of steps AND reorder the ones that remain, before generation.
    Invariant L1 must keep holding against whatever order the user picked --
    not just the manifest's original order -- for any permutation of any
    nonempty subset of step ids."""
    manifest = load_manifest(manifest_data)
    all_ids = manifest.step_ids()
    subset = data.draw(st.lists(st.sampled_from(all_ids), min_size=1, unique=True))
    ordered_ids = data.draw(st.permutations(subset))

    selected = select_manifest_steps(manifest, ordered_ids)
    assert selected.step_ids() == list(ordered_ids)

    doc_steps = assemble_steps(selected, render_step_template)
    assert [d["step_id"] for d in doc_steps] == list(ordered_ids)
    assert check_1to1_mapping(selected, doc_steps) is True
