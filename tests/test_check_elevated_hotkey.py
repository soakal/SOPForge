"""Unit test for the DEVIATIONS.md writer — the actual elevation/hotkey
attempt is not testable in this environment for the reasons the script
itself documents (see scripts/check_elevated_hotkey.py's module docstring),
so this only proves the idempotent-write logic."""

from scripts.check_elevated_hotkey import ensure_uipi_section


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
