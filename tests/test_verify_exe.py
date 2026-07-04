"""Unit tests for verify_exe.py's results-writing logic. The actual EXE
launch/timing is exercised for real by this task's own verify command,
`.venv\\Scripts\\python.exe scripts\\verify_exe.py` (needs a built EXE, not
repeated here)."""

import pytest

import scripts.verify_exe as verify_exe_module


def test_write_results_appends_with_blank_line_separator(tmp_path):
    path = tmp_path / "01-results.md"
    path.write_text("## Criterion 1: something\n\nsome text\n", encoding="utf-8")

    verify_exe_module.write_results(1.0, [0.9, 0.8, 0.85], [0, 0, 0, 0], results_path=path)
    text = path.read_text(encoding="utf-8")
    assert "some text\n\n## Criterion 4" in text


def test_write_results_no_glue_when_file_lacks_trailing_newline(tmp_path):
    path = tmp_path / "01-results.md"
    path.write_text("## Criterion 1: something\n\nNo trailing newline.", encoding="utf-8")

    verify_exe_module.write_results(1.0, [0.9], [0, 0], results_path=path)
    text = path.read_text(encoding="utf-8")
    assert "newline.## Criterion 4" not in text
    assert "\n## Criterion 4" in text


def test_write_results_reports_steady_state_average_and_returns_it(tmp_path):
    path = tmp_path / "01-results.md"
    avg = verify_exe_module.write_results(2.5, [1.0, 1.2, 1.1], [0, 0, 0, 0], results_path=path)
    assert avg == pytest.approx((1.0 + 1.2 + 1.1) / 3)
    text = path.read_text(encoding="utf-8")
    assert "First launch after build: 2.500s" in text
    assert "1.000s, 1.200s, 1.100s" in text
    assert "[0, 0, 0, 0]" in text
