from capture.settings import CaptureSettings, load_settings, save_settings


def test_missing_file_defaults_on(tmp_path):
    settings = load_settings(tmp_path / "does-not-exist.toml")
    assert settings == CaptureSettings(record_narration=True)


def test_corrupt_file_defaults_on(tmp_path):
    path = tmp_path / "capture_settings.toml"
    path.write_text("this is not valid { toml", encoding="utf-8")
    settings = load_settings(path)
    assert settings == CaptureSettings(record_narration=True)


def test_save_then_load_round_trips_true(tmp_path):
    path = tmp_path / "sub" / "capture_settings.toml"
    save_settings(CaptureSettings(record_narration=True), path)
    assert load_settings(path) == CaptureSettings(record_narration=True)


def test_save_then_load_round_trips_false(tmp_path):
    path = tmp_path / "capture_settings.toml"
    save_settings(CaptureSettings(record_narration=True), path)
    save_settings(CaptureSettings(record_narration=False), path)
    assert load_settings(path) == CaptureSettings(record_narration=False)


def test_save_creates_parent_directory(tmp_path):
    path = tmp_path / "nested" / "dir" / "capture_settings.toml"
    save_settings(CaptureSettings(record_narration=True), path)
    assert path.exists()


def test_save_is_atomic_no_leftover_tmp_file(tmp_path):
    path = tmp_path / "capture_settings.toml"
    save_settings(CaptureSettings(record_narration=True), path)
    leftovers = list(tmp_path.glob(".capture_settings-*.tmp"))
    assert leftovers == []
