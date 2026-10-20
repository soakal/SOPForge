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

