"""Unit tests for build_exe.py's size-check logic. The actual PyInstaller
build is slow (~45s) and is exercised for real by this task's own verify
command, `.venv\\Scripts\\python.exe scripts\\build_exe.py --assert-size 40`
— not repeated here."""

import scripts.build_exe as build_exe_module


def _make_dist(tmp_path, *file_sizes_mb):
    """A dist/sopforge/ dir containing sopforge.exe plus any extra files,
    each file_sizes_mb bytes — mimics the one-folder build's footprint
    being spread across sopforge.exe and its sibling support files."""
    dist_dir = tmp_path / "sopforge"
    dist_dir.mkdir()
    (dist_dir / "sopforge.exe").write_bytes(b"0" * int(file_sizes_mb[0] * 1024 * 1024))
    for i, size_mb in enumerate(file_sizes_mb[1:], start=1):
        (dist_dir / f"support{i}.dll").write_bytes(b"0" * int(size_mb * 1024 * 1024))
    return dist_dir


def test_fails_when_exe_missing(tmp_path, monkeypatch, capsys):
    dist_dir = tmp_path / "sopforge"
    monkeypatch.setattr(build_exe_module, "DIST_DIR", dist_dir)
    monkeypatch.setattr(build_exe_module, "DIST_EXE", dist_dir / "sopforge.exe")

    code = build_exe_module.main(["--skip-build"])
    assert code == 1
    assert "does not exist" in capsys.readouterr().err


def test_passes_under_size_threshold_summing_whole_folder(tmp_path, monkeypatch, capsys):
    dist_dir = _make_dist(tmp_path, 2, 3, 4)  # 9 MB total across 3 files
    monkeypatch.setattr(build_exe_module, "DIST_DIR", dist_dir)
    monkeypatch.setattr(build_exe_module, "DIST_EXE", dist_dir / "sopforge.exe")

    code = build_exe_module.main(["--skip-build", "--assert-size", "40"])
    assert code == 0
    assert "OK" in capsys.readouterr().out


def test_fails_over_size_threshold_summing_whole_folder(tmp_path, monkeypatch, capsys):
    dist_dir = _make_dist(tmp_path, 2, 20, 20)  # 42 MB total across 3 files
    monkeypatch.setattr(build_exe_module, "DIST_DIR", dist_dir)
    monkeypatch.setattr(build_exe_module, "DIST_EXE", dist_dir / "sopforge.exe")

    code = build_exe_module.main(["--skip-build", "--assert-size", "40"])
    assert code == 1
    assert "threshold" in capsys.readouterr().err


def test_no_threshold_means_any_size_passes(tmp_path, monkeypatch):
    dist_dir = _make_dist(tmp_path, 50, 50)  # 100 MB, no --assert-size given
    monkeypatch.setattr(build_exe_module, "DIST_DIR", dist_dir)
    monkeypatch.setattr(build_exe_module, "DIST_EXE", dist_dir / "sopforge.exe")

    code = build_exe_module.main(["--skip-build"])
    assert code == 0


def test_dir_size_mb_sums_all_files_recursively(tmp_path):
    dist_dir = tmp_path / "sopforge"
    (dist_dir / "sub").mkdir(parents=True)
    (dist_dir / "a.exe").write_bytes(b"0" * (1024 * 1024))
    (dist_dir / "sub" / "b.dll").write_bytes(b"0" * (1024 * 1024))

    assert build_exe_module.dir_size_mb(dist_dir) == 2.0
