"""SOP library store: a persistent JSON index updated on every completed
job, searchable by title/date substring."""

import json
from pathlib import Path

from pipeline.library import load_index, search, upsert_entry
from pipeline.manifest import load_manifest

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


class _FakeSession:
    def __init__(self, id_, title, started_utc):
        self.id = id_
        self.title = title
        self.started_utc = started_utc


class _FakeManifest:
    def __init__(self, id_, title, started_utc):
        self.session = _FakeSession(id_, title, started_utc)


_REPORT = {
    "template_fallback_steps": ["step-003"],
    "verify_claims": [{"claim_id": "claim-002", "text": "x"}],
    "empty_metadata_steps": ["step-002"],
}


def test_upsert_creates_a_new_entry(tmp_path):
    manifest = _FakeManifest("s1", "Answer File Setup", "2026-01-01T00:00:00Z")
    entry = upsert_entry(tmp_path, "session-1", manifest, _REPORT)

    assert entry["session_id"] == "session-1"
    assert entry["title"] == "Answer File Setup"
    assert entry["date"] == "2026-01-01T00:00:00Z"
    assert entry["template_fallback_count"] == 1
    assert entry["verify_claims_count"] == 1
    assert entry["empty_metadata_count"] == 1

    index = load_index(tmp_path)
    assert index == [entry]


def test_upsert_overwrites_existing_entry_for_the_same_session_not_duplicate(tmp_path):
    manifest_v1 = _FakeManifest("s1", "Old Title", "2026-01-01T00:00:00Z")
    upsert_entry(tmp_path, "session-1", manifest_v1, _REPORT)

    manifest_v2 = _FakeManifest("s1", "New Title", "2026-01-01T00:00:00Z")
    upsert_entry(tmp_path, "session-1", manifest_v2, _REPORT)

    index = load_index(tmp_path)
    assert len(index) == 1
    assert index[0]["title"] == "New Title"


def test_load_index_returns_empty_list_when_no_index_file_exists(tmp_path):
    assert load_index(tmp_path) == []


def test_search_by_title_substring_case_insensitive(tmp_path):
    upsert_entry(tmp_path, "s1", _FakeManifest("s1", "Answer File Setup", "2026-01-01"), _REPORT)
    upsert_entry(tmp_path, "s2", _FakeManifest("s2", "Backup Procedure", "2026-02-01"), _REPORT)

    results = search(tmp_path, "answer")
    assert [r["session_id"] for r in results] == ["s1"]


def test_search_by_date_substring(tmp_path):
    upsert_entry(tmp_path, "s1", _FakeManifest("s1", "Answer File Setup", "2026-01-01"), _REPORT)
    upsert_entry(tmp_path, "s2", _FakeManifest("s2", "Backup Procedure", "2026-02-01"), _REPORT)

    results = search(tmp_path, "2026-02")
    assert [r["session_id"] for r in results] == ["s2"]


def test_search_with_no_query_returns_everything(tmp_path):
    upsert_entry(tmp_path, "s1", _FakeManifest("s1", "Answer File Setup", "2026-01-01"), _REPORT)
    upsert_entry(tmp_path, "s2", _FakeManifest("s2", "Backup Procedure", "2026-02-01"), _REPORT)

    assert len(search(tmp_path, None)) == 2
    assert len(search(tmp_path, "")) == 2


def test_search_with_no_match_returns_empty_list(tmp_path):
    upsert_entry(tmp_path, "s1", _FakeManifest("s1", "Answer File Setup", "2026-01-01"), _REPORT)
    assert search(tmp_path, "nonexistent-query") == []


def test_upsert_with_real_loaded_manifest(tmp_path):
    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    entry = upsert_entry(tmp_path, "session-1", manifest, _REPORT)
    assert entry["title"] == manifest.session.id  # fixture's title is empty, id is the fallback

    on_disk = json.loads((tmp_path / "library.json").read_text(encoding="utf-8"))
    assert on_disk == [entry]


def test_concurrent_reads_never_see_a_partial_or_empty_file_during_writes(tmp_path):
    """Regression: _save_index used to write_text() directly, which
    truncates the file before writing new content — a reader racing a
    writer could observe an empty or partially-written library.json and
    raise JSONDecodeError. The temp-file + os.replace() write must make
    every read see either the old complete content or the new complete
    content, never something in between."""
    import threading
    import time

    manifest = load_manifest(FIXTURES / "sample-manifest.json")
    upsert_entry(tmp_path, "session-0", manifest, _REPORT)  # seed a non-empty starting file

    stop = threading.Event()
    errors = []

    def writer():
        i = 0
        while not stop.is_set():
            upsert_entry(tmp_path, f"session-{i % 5}", manifest, _REPORT)
            i += 1
            time.sleep(0.005)  # a real job completion, not an artificial busy-loop worst case

    def reader():
        while not stop.is_set():
            try:
                result = load_index(tmp_path)
                if not result:
                    errors.append("read an empty index mid-write")
            except Exception as exc:  # noqa: BLE001 - any read failure is the bug under test
                errors.append(repr(exc))
            time.sleep(0.002)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    threads.append(threading.Thread(target=reader))
    for t in threads:
        t.start()
    time.sleep(0.5)
    stop.set()
    for t in threads:
        t.join(timeout=5)

    assert errors == []
