F-10 (v0): FAILED gate twice
No files were changed.

regressions (pv0): FAILED gate twice
........................................................................ [ 39%]
................F
================================== FAILURES ===================================
_________ test_polish_end_to_end_real_prompt_claim_coverage_rejection _________

tmp_path = WindowsPath('C:/Users/briank/AppData/Local/Temp/pytest-of-briank/pytest-184/test_polish_end_to_end_real_pr1')

    def test_polish_end_to_end_real_prompt_claim_coverage_rejection(tmp_path):
        """Companion to the happy-path test above, at claim-coverage-rejection
        granularity: a real, gate-passing narrative REPHRASE (drops claim-002's
        literal wording -- "enter" -> "type in", well under _field_gate's
        novel-content thresholds, so _field_gate alone lets it through) must
        still get caught by _write_all_exports' validate_claim_coverage safety
        net and reverted to the original narrative_text -- while step
        text/narration polish (which claim coverage never governs) is kept,
        proving the revert is scoped to just the narrative field. This also
        reaches doc.html: since doc.html now renders from the same md_step_
        results/md_narrative_text as doc.md, the kept (uppercased) step fields
        show up polished in doc.html too. Uses the same real-prompt-parsing
        stub as the happy-path test, with only the narrative field's rewrite
        overridden to this specific claim-dropping rephrase; every other field
        still gets the default (uppercase) transform, so this also proves
        per-field independence end to end."""
        import html as _html
    
        _target = "Then enter the computer name."
        _replacement = "Then type in the computer name."
    
        def _drop_claim_002(text):
            assert _target in text, (
                "fixture assumption: claim-002's exact text must be present "
                "(content-covered, not [verify]-flagged) in the unpolished narrative"
            )
            return text.replace(_target, _replacement)
    
        stub = _RealPromptParsingPolishStub(overrides={"narrative": _drop_claim_002})
    
        cfg = tmp_path / "models.toml"
        shutil.copyfile(default_config_path(), cfg)
        models_cfg = load_models_config(cfg)
        models_cfg.polish.enabled = True
        save_models_config(models_cfg, cfg)
    
        app = create_app(
            sessions_root=tmp_path / "sessions",
            llm_client_factory=_round_trip_passing_stub_factory,
            narrative_llm_client_factory=lambda: _VerbatimNarrativeStub(),
            polish_llm_client_factory=lambda section: stub,
            config_path=cfg,
        )
        client = TestClient(app)
    
        manifest_json, files = _manifest_and_files(tmp_path)
        files.append(("transcript_file", ("narration.md", _E2E_TRANSCRIPT.encode(), "text/markdown")))
    
        resp = client.post("/sessions", data={"manifest_json": manifest_json}, files=files)
        session_id = resp.json()["session_id"]
        status = _wait_for_terminal_status(client, session_id)
        assert status["status"] == "done"
        assert stub.calls, "the polish LLM client was never called"
    
        items = _PROMPT_ITEM_RE.findall(stub.calls[0])
        step_items = [(field_id, text) for field_id, text in items if field_id != "narrative"]
        assert step_items, "sanity: expected at least one non-narrative field"
        assert any(":narration" in field_id for field_id, _ in step_items), (
            "sanity: expected at least one narrated step"
        )
    
        md = client.get(f"/sessions/{session_id}/doc.md").text
        # The narrative rephrase was rejected -- doc.md's narrative section fell
        # back to the unpolished (claim-complete) text, still containing
        # claim-002's exact original wording, no [verify] blockquote needed.
>       assert _target in md
E       AssertionError: assert 'Then enter the computer name.' in '# 20260703-142200-a1b2\n\nFirst, open the console. Then type in the computer name. Finally, check the downloads.\n\n#...ENT IN THE DOWNLOADS - GOOGLE CHROME WINDOW.\n\n> **Narration:** FINALLY, CHECK THE DOWNLOADS.\n\n![Step 3](003.png)\n'

tests\pipeline\test_server.py:1156: AssertionError
=========================== short test summary info ===========================
FAILED tests/pipeline/test_server.py::test_polish_end_to_end_real_prompt_claim_coverage_rejection
!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!
1 failed, 302 passed, 2 skipped, 3 deselected in 55.77s

F-06 (p2v0): FAILED gate twice
No files were changed.

F-10 (p2v1): FAILED gate twice
All checks passed!
54 files already formatted
........................................................................ [  9%]
.....................................................................ss. [ 19%]
........................................................................ [ 29%]
........................................................................ [ 39%]
........................................................................ [ 49%]
........................................................................ [ 59%]
........................................................................ [ 69%]
........................................ss.............................. [ 79%]
....................................................................s... [ 89%]
........F
================================== FAILURES ===================================
______ test_hook_callback_returns_instantly_even_when_processing_is_slow ______

tmp_path = WindowsPath('C:/Users/briank/AppData/Local/Temp/pytest-of-briank/pytest-216/test_hook_callback_returns_ins0')
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x0000022510273350>

    def test_hook_callback_returns_instantly_even_when_processing_is_slow(tmp_path, monkeypatch):
        """`_enqueue_event` is the actual pynput hook callback (wired via
        InputRecorder(on_event=self._enqueue_event)) — it must return almost
        instantly regardless of how slow resolve_at/screenshot/redaction are,
        or Windows will silently detach the low-level hook. This is why event
        ingestion (fast, hook thread) is decoupled from processing (slow, one
        dedicated worker thread) via a queue instead of processing inline."""
    
        def slow_resolve_at(x, y):
            time.sleep(1.0)
            return {
                "name": "",
                "control_type": "",
                "automation_id": "",
                "framework": "",
                "bounding_rect": None,
            }, {"title": "", "process": "", "class": ""}
    
        monkeypatch.setattr(recorder_module, "resolve_at", slow_resolve_at)
    
        recorder = Recorder(tmp_path)
        recorder.start()
        try:
            t0 = time.time()
            recorder._enqueue_event(
                {"action": "click", "button": "left", "x": 1, "y": 1, "ts": time.time()}
            )
            elapsed = time.time() - t0
            assert elapsed < 0.2, f"hook callback took {elapsed:.3f}s — must be near-instant"
    
            deadline = time.time() + 5.0
            while time.time() < deadline and len(recorder._builder.step_ids()) == 0:
                time.sleep(0.05)
            assert len(recorder._builder.step_ids()) == 1  # worker thread did process it
        finally:
            manifest_path = recorder.stop()
    
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
>       assert len(data["steps"]) == 1
E       AssertionError: assert 2 == 1
E        +  where 2 = len([{'id': 'step-001', 'ts_utc': '2026-10-21T14:01:38.522Z', 'action': 'click', 'screen': {'x': 1, 'y': 1, 'monitor': 1, ...tc': '2026-10-21T14:01:39.474Z', 'action': 'click', 'screen': {'x': 906, 'y': 1167, 'monitor': 1, 'scale': 1.25}, ...}])

tests\test_recorder.py:397: AssertionError
============================== warnings summary ===============================
tests/test_hooks_shots.py::test_capture_against_real_gdi_never_raises
tests/test_recorder.py::test_hook_callback_returns_instantly_even_when_processing_is_slow
tests/test_recorder.py::test_hook_callback_returns_instantly_even_when_processing_is_slow
  C:\Users\briank\Documents\AI\SOP & Labeling\SOPForge\src\capture\shots.py:29: DeprecationWarning: mss.mss is deprecated and will be removed in a future release; use mss.MSS instead
    with mss.mss() as sct:

tests/test_hooks_shots.py::test_capture_against_real_gdi_never_raises
tests/test_recorder.py::test_hook_callback_returns_instantly_even_when_processing_is_slow
tests/test_recorder.py::test_hook_callback_returns_instantly_even_when_processing_is_slow
  C:\Users\briank\Documents\AI\SOP & Labeling\SOPForge\src\capture\shots.py:55: DeprecationWarning: mss.mss is deprecated and will be removed in a future release; use mss.MSS instead
    with mss.mss() as sct:

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ===========================
FAILED tests/test_recorder.py::test_hook_callback_returns_instantly_even_when_processing_is_slow
!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!
1 failed, 651 passed, 5 skipped, 3 deselected, 6 warnings in 197.74s (0:03:17)

F-11 (p2v1): FAILED gate twice
All checks passed!
54 files already formatted
........................................................................ [  9%]
.....................................................................ss. [ 19%]
........................................................................ [ 29%]
........................................................................ [ 39%]
........................................................................ [ 49%]
........................................................................ [ 59%]
........................................................................ [ 69%]
........................................ss.............................. [ 79%]
....................................................................s... [ 89%]
........F
================================== FAILURES ===================================
______ test_hook_callback_returns_instantly_even_when_processing_is_slow ______

tmp_path = WindowsPath('C:/Users/briank/AppData/Local/Temp/pytest-of-briank/pytest-224/test_hook_callback_returns_ins0')
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x000002D6413314F0>

    def test_hook_callback_returns_instantly_even_when_processing_is_slow(tmp_path, monkeypatch):
        """`_enqueue_event` is the actual pynput hook callback (wired via
        InputRecorder(on_event=self._enqueue_event)) — it must return almost
        instantly regardless of how slow resolve_at/screenshot/redaction are,
        or Windows will silently detach the low-level hook. This is why event
        ingestion (fast, hook thread) is decoupled from processing (slow, one
        dedicated worker thread) via a queue instead of processing inline."""
    
        def slow_resolve_at(x, y):
            time.sleep(1.0)
            return {
                "name": "",
                "control_type": "",
                "automation_id": "",
                "framework": "",
                "bounding_rect": None,
            }, {"title": "", "process": "", "class": ""}
    
        monkeypatch.setattr(recorder_module, "resolve_at", slow_resolve_at)
    
        recorder = Recorder(tmp_path)
        recorder.start()
        try:
            t0 = time.time()
            recorder._enqueue_event(
                {"action": "click", "button": "left", "x": 1, "y": 1, "ts": time.time()}
            )
            elapsed = time.time() - t0
            assert elapsed < 0.2, f"hook callback took {elapsed:.3f}s — must be near-instant"
    
            deadline = time.time() + 5.0
            while time.time() < deadline and len(recorder._builder.step_ids()) == 0:
                time.sleep(0.05)
            assert len(recorder._builder.step_ids()) == 1  # worker thread did process it
        finally:
            manifest_path = recorder.stop()
    
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
>       assert len(data["steps"]) == 1
E       AssertionError: assert 2 == 1
E        +  where 2 = len([{'id': 'step-001', 'ts_utc': '2026-10-21T14:23:23.875Z', 'action': 'click', 'screen': {'x': 1, 'y': 1, 'monitor': 1, ...utc': '2026-10-21T14:23:24.173Z', 'action': 'click', 'screen': {'x': 567, 'y': 198, 'monitor': 1, 'scale': 1.25}, ...}])

tests\test_recorder.py:400: AssertionError
============================== warnings summary ===============================
tests/test_hooks_shots.py::test_capture_against_real_gdi_never_raises
tests/test_recorder.py::test_hook_callback_returns_instantly_even_when_processing_is_slow
tests/test_recorder.py::test_hook_callback_returns_instantly_even_when_processing_is_slow
  C:\Users\briank\Documents\AI\SOP & Labeling\SOPForge\src\capture\shots.py:29: DeprecationWarning: mss.mss is deprecated and will be removed in a future release; use mss.MSS instead
    with mss.mss() as sct:

tests/test_hooks_shots.py::test_capture_against_real_gdi_never_raises
tests/test_recorder.py::test_hook_callback_returns_instantly_even_when_processing_is_slow
tests/test_recorder.py::test_hook_callback_returns_instantly_even_when_processing_is_slow
  C:\Users\briank\Documents\AI\SOP & Labeling\SOPForge\src\capture\shots.py:55: DeprecationWarning: mss.mss is deprecated and will be removed in a future release; use mss.MSS instead
    with mss.mss() as sct:

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ===========================
FAILED tests/test_recorder.py::test_hook_callback_returns_instantly_even_when_processing_is_slow
!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!
1 failed, 651 passed, 5 skipped, 3 deselected, 6 warnings in 197.74s (0:03:17)

