"""Sidecar review report (invariant L5, AC7): every template-fallback step,
every [verify] claim, and every empty-UIA-metadata step must show up in the
report. `fixtures/review-report-manifest.json` + `fixtures/review-report-
transcript.json` are crafted so that running them through the real
task-06/task-08/task-09 pipeline stages naturally produces all three
categories at once, in a single fixture pair."""

import json
from pathlib import Path

from pipeline.claims import extract_claims
from pipeline.generation import generate_all_steps
from pipeline.manifest import Manifest, load_manifest
from pipeline.narrative import generate_narrative
from pipeline.sidecar import build_sidecar_report

FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures"


class _ScriptedClient:
    """Returns replies in order, one per .chat() call — a fixed script, not
    a realistic model, so each fixture step's outcome is exactly controlled."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.replies.pop(0)


def _load_segments(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["segments"]


def test_sidecar_report_captures_all_three_categories_from_crafted_fixtures():
    manifest = load_manifest(FIXTURES / "review-report-manifest.json")

    step_client = _ScriptedClient(
        [
            "Click Save in Answer File Editor.",  # step-001: correct, round-trips
            "Click somewhere in Answer File Editor.",  # step-002: empty element, correct verb+window
            "This reply mentions nothing about the computer name field at all.",  # step-003: wrong, forces fallback
        ]
    )
    step_results = generate_all_steps(manifest, step_client)
    assert [r["used_fallback"] for r in step_results] == [False, False, True]

    claims = extract_claims(_load_segments("review-report-transcript.json"))
    narrative_client = _ScriptedClient([claims[0]["text"]])  # covers claim-001 only
    _final_text, covered, verify_ids = generate_narrative(claims, narrative_client, passes=1)
    assert covered == ["claim-001"]
    assert verify_ids == ["claim-002"]

    claims_by_id = {c["claim_id"]: c for c in claims}
    report = build_sidecar_report(manifest, step_results, verify_ids, claims_by_id)

    assert report["template_fallback_steps"] == ["step-003"]
    assert report["empty_metadata_steps"] == ["step-002"]
    assert [c["claim_id"] for c in report["verify_claims"]] == ["claim-002"]
    assert report["verify_claims"][0]["text"] == claims[1]["text"]


def test_sidecar_report_is_json_serializable():
    manifest = load_manifest(FIXTURES / "review-report-manifest.json")
    report = build_sidecar_report(manifest, [], [], {})
    json.dumps(report)  # must not raise


def test_sidecar_report_empty_when_nothing_flagged():
    manifest = Manifest.model_validate(
        {
            "schema_version": "1.0",
            "session": {
                "id": "s",
                "title": "",
                "started_utc": "2026-01-01T00:00:00Z",
                "ended_utc": "2026-01-01T00:00:01Z",
                "machine": "m",
                "os_build": "1",
                "narration_wav": None,
            },
            "steps": [
                {
                    "id": "step-001",
                    "ts_utc": "2026-01-01T00:00:00Z",
                    "action": "click",
                    "button": "left",
                    "screen": {"x": 10, "y": 10, "monitor": 1},
                    "screenshot": "001.png",
                    "window": {"title": "App", "process": "app.exe", "class": "win32"},
                    "element": {
                        "name": "OK",
                        "control_type": "Button",
                        "automation_id": "",
                        "framework": "Win32",
                        "bounding_rect": None,
                    },
                    "redactions": [],
                }
            ],
        }
    )
    step_results = [{"step_id": "step-001", "text": "Click OK.", "used_fallback": False}]
    report = build_sidecar_report(manifest, step_results, [], {})
    assert report == {
        "template_fallback_steps": [],
        "verify_claims": [],
        "empty_metadata_steps": [],
    }


def test_verify_claims_include_text_only_when_claim_known():
    manifest = load_manifest(FIXTURES / "review-report-manifest.json")
    report = build_sidecar_report(manifest, [], ["claim-999"], {})
    assert report["verify_claims"] == [{"claim_id": "claim-999", "text": None}]
