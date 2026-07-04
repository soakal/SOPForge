"""Unit tests for the pure/deterministic parts of the self-test harness
(result formatting, PID-set parsing). The actual 3-app scripted run is
launch-and-kill-heavy and slow (~60-90s: Notepad++/Chrome/VS Code cold
starts) and is already exercised for real by this task's own verify command,
`.venv\\Scripts\\python.exe -m capture.selftest --all` — duplicating that
inside the standard fast pytest suite would just double the cost without
adding coverage, so it's deliberately not repeated here."""

from capture.selftest import _running_pids, write_results


def test_running_pids_empty_for_an_image_that_is_not_running():
    assert _running_pids("definitely-not-a-real-process-name.exe") == set()


def test_running_pids_finds_a_process_known_to_always_be_running():
    # explorer.exe is the Windows shell; if this is empty, the underlying
    # tasklist parsing itself is broken, not just "process not found".
    assert _running_pids("explorer.exe") != set()


def test_write_results_computes_overall_percentage_and_appends(tmp_path):
    results_path = tmp_path / "01-results.md"
    per_app = {"notepadpp": (4, 5), "chrome": (5, 5), "vscode": (5, 5)}

    overall = write_results(per_app, threshold=0.9, results_path=results_path)

    assert overall == 14 / 15
    text = results_path.read_text(encoding="utf-8")
    assert "notepadpp**: 4/5 (80.0%)" in text
    assert "chrome**: 5/5 (100.0%)" in text
    assert "Overall: 14/15 (93.3%)" in text
    assert "threshold 90%" in text


def test_write_results_below_threshold_reports_correct_percentage(tmp_path):
    results_path = tmp_path / "01-results.md"
    per_app = {"notepadpp": (1, 5), "chrome": (1, 5), "vscode": (1, 5)}

    overall = write_results(per_app, threshold=0.9, results_path=results_path)

    assert overall == 3 / 15
    assert overall < 0.9


def test_write_results_appends_rather_than_overwriting(tmp_path):
    results_path = tmp_path / "01-results.md"
    write_results({"a": (1, 1)}, results_path=results_path)
    write_results({"b": (1, 1)}, results_path=results_path)
    text = results_path.read_text(encoding="utf-8")
    assert text.count("Criterion 1") == 2
