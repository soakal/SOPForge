"""Unit tests for build_exe.py's size-check logic. The actual PyInstaller
build is slow (~45s) and is exercised for real by this task's own verify
command, `.venv\\Scripts\\python.exe scripts\\build_exe.py --assert-size 40`
— not repeated here."""

import scripts.build_exe as build_exe_module


def test_fails_when_exe_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(build_exe_module, "DIST_EXE", tmp_path / "sopforge.exe")
    code = build_exe_module.main(["--skip-build"])
    assert code == 1
    assert "does not exist" in capsys.readouterr().err


def test_passes_under_size_threshold(tmp_path, monkeypatch, capsys):
    exe = tmp_path / "sopforge.exe"
    exe.write_bytes(b"0" * (10 * 1024 * 1024))  # 10 MB
    monkeypatch.setattr(build_exe_module, "DIST_EXE", exe)

    code = build_exe_module.main(["--skip-build", "--assert-size", "40"])
    assert code == 0
    assert "OK" in capsys.readouterr().out


def test_fails_over_size_threshold(tmp_path, monkeypatch, capsys):
    exe = tmp_path / "sopforge.exe"
    exe.write_bytes(b"0" * (41 * 1024 * 1024))  # 41 MB
    monkeypatch.setattr(build_exe_module, "DIST_EXE", exe)

    code = build_exe_module.main(["--skip-build", "--assert-size", "40"])
    assert code == 1
    assert "threshold" in capsys.readouterr().err


def test_no_threshold_means_any_size_passes(tmp_path, monkeypatch):
    exe = tmp_path / "sopforge.exe"
    exe.write_bytes(b"0" * (100 * 1024 * 1024))  # 100 MB, no --assert-size given
    monkeypatch.setattr(build_exe_module, "DIST_EXE", exe)

    code = build_exe_module.main(["--skip-build"])
    assert code == 0
