"""Unit tests for the DEVIATIONS.md writer and the live injection probe. The
actual elevation/hotkey attempt is not testable in this environment for the
reasons the script itself documents (see scripts/check_elevated_hotkey.py's
module docstring) — this only proves the idempotent-write logic and that the
probe runs cleanly (its result is inherently non-deterministic here — see
.claude/skills/uia-notes.md — so only its type/shape is asserted)."""

from scripts.check_elevated_hotkey import (
    ensure_uipi_section,
    probe_injection_works_this_run,
)


def test_writes_section_when_file_does_not_exist(tmp_path):
    path = tmp_path / "DEVIATIONS.md"
    assert not path.exists()

    assert ensure_uipi_section(path) is True
    text = path.read_text(encoding="utf-8")
    assert "## UIPI" in text
    assert "ERROR_ACCESS_DENIED" in text


def test_appends_section_to_existing_file_without_disturbing_it(tmp_path):
    path = tmp_path / "DEVIATIONS.md"
    path.write_text("# Deviations\n\n## Some other deviation\n\nDetails.\n", encoding="utf-8")

    assert ensure_uipi_section(path) is True
    text = path.read_text(encoding="utf-8")
    assert "## Some other deviation" in text
    assert "## UIPI" in text


def test_running_twice_does_not_duplicate_the_section(tmp_path):
    path = tmp_path / "DEVIATIONS.md"

    ensure_uipi_section(path)
    first = path.read_text(encoding="utf-8")
    ensure_uipi_section(path)
    second = path.read_text(encoding="utf-8")

    assert first == second
    assert second.count("## UIPI") == 1


def test_appending_to_file_without_trailing_newline_does_not_glue_lines(tmp_path):
    path = tmp_path / "DEVIATIONS.md"
    path.write_text(
        "# Deviations\n\n## Some other deviation\n\nNo trailing newline.", encoding="utf-8"
    )

    ensure_uipi_section(path)
    text = path.read_text(encoding="utf-8")
    assert "newline.## UIPI" not in text
    assert "\n## UIPI" in text


def test_probe_injection_runs_cleanly_and_returns_a_bool():
    # Result varies run to run in this environment (see uia-notes.md) — only
    # the type/shape is a meaningful assertion here.
    result = probe_injection_works_this_run()
    assert isinstance(result, bool)
