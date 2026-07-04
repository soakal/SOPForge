# Phase 2 — Task list

Rationale: Structural invariants L1–L3 carry the hard correctness guarantees and
are attacked first on top of a minimal manifest/config layer; the mocked LLM
client and generation orchestrator follow, then narration, annotation, sidecar,
and server. Everything runs from `fixtures/` with a mock LLM (real-endpoint test
skips unless `SOPFORGE_OLLAMA_URL` is set), and the sole SOP-Factory-2-dependent
task (docx assembler, AC3/AC5) is last and isolated so Phase 2 is coherent and
reviewable without it.

Environment facts noted at plan time:
- No network route to the real Ollama endpoint (`http://192.168.200.60:11434/v1`)
  from this build VM (confirmed: connection timeout). Not a blocker — every task
  uses a mocked LLM client by default; the one opt-in real-endpoint test skips
  (never fails) when `SOPFORGE_OLLAMA_URL` is unset or unreachable.
- The SOP Factory 2 engine source does not exist on this machine
  (`C:\Users\Brian\Documents\SOP_Factory_2` is absent, and was already absent
  when Phase 1 started). Every task that doesn't need it is scheduled first;
  task-15 (the docx assembler) is last and self-contained. If SOP_Factory_2 is
  still absent when task-15 is reached, stop and escalate per CLAUDE.md prime
  directive 1 — do not fabricate a substitute engine under that name.

- [x] task-01: `src/pipeline/` package scaffold — schema-validating manifest loader (pydantic models over `fixtures/manifest.schema.json`) + `config/models.toml` (per-section endpoint/model, defaults steps=`qwen3:14b` narrative=`qwen3:32b`, `anthropic` flag per section default off) with typed loader — verify: `pytest -q tests/pipeline/test_manifest_loader.py tests/pipeline/test_models_config.py`
- [x] task-02: Template fallback renderer (invariant L3) — pure string interpolation from a manifest record, factually correct for click/type, empty-element, and empty-window-class steps, proven against `fixtures/empty-elements-manifest.json` and `fixtures/sample-manifest.json` — verify: `pytest -q tests/pipeline/test_template_fallback.py`
- [x] task-03: Round-trip validator (invariant L2) — extract `{action, element, window}` back from generated step text, diff against manifest record; includes proof every template-renderer output passes its own round-trip, plus negative tests for wrong element/action/window — verify: `pytest -q tests/pipeline/test_roundtrip.py`
- [x] task-04: Step assembler (invariant L1) — per-record ID-attached generation slots reassembled by code; hypothesis property test over randomly generated schema-valid manifests proving zero-drop/zero-invent/order-preserved for 1,000 cases (AC1) — verify: `pytest -q tests/pipeline/test_property_mapping.py`
- [x] task-05: LLM client — OpenAI-compatible chat-completions over httpx, per-section endpoint/model/anthropic-flag routing from `config/models.toml`, injectable transport for mocking; opt-in integration test that `pytest.skip`s (never fails) when `SOPFORGE_OLLAMA_URL` is unset or unreachable — verify: `pytest -q tests/pipeline/test_llm_client.py`
- [ ] task-06: Step generation orchestrator — per-record prompt → round-trip gate → template fallback, hard cap of 1 generation attempt per step (assert on mock call count); AC2 test: ≥95% round-trip pass rate on `fixtures/` manifests with realistic mock step text, and injected-mismatch steps demonstrably fall back with no retry — verify: `pytest -q tests/pipeline/test_step_generation.py`
- [ ] task-07: Transcription wrapper (faster-whisper behind an interface, unit-tested with a stubbed model; opt-in real-model test skips if weights absent) + committed `fixtures/sample-transcript.json`; atomic claim extraction with timestamps and stable claim IDs (invariant L4 front half) — verify: `pytest -q tests/pipeline/test_transcription.py tests/pipeline/test_claims.py`
- [ ] task-08: Claim-coverage validator + `[verify]` blockquote rendering (invariant L4 back half) — every claim ID present in output or rendered as `[verify]` blockquote; negative test proving the validator fails a doc with a dropped claim (AC4) — verify: `pytest -q tests/pipeline/test_claim_coverage.py`
- [ ] task-09: Multi-pass narrative mode — draft → critique → revise via mock LLM, pass count configurable from `config/models.toml`, output still subject to the task-08 claim-coverage gate — verify: `pytest -q tests/pipeline/test_narrative_multipass.py`
- [ ] task-10: Screenshot annotation (Pillow) — red circle/arrow at manifest click coords on synthetic PNGs; pixel assertion that the click coordinate lies inside the drawn marker, including near-edge coords (AC6) — verify: `pytest -q tests/pipeline/test_annotate.py`
- [ ] task-11: Sidecar review report (invariant L5) — JSON listing every template-fallback step, every `[verify]` claim, every empty-UIA-metadata step; commit `fixtures/review-report-manifest.json` + transcript crafted to contain all three (AC7) — verify: `pytest -q tests/pipeline/test_sidecar_report.py`
- [ ] task-12: Intermediate document renderers (md + html) assembled by code from per-step outputs, annotated screenshots, and `[verify]` blockquotes — template-mode end-to-end from fixture manifest with an assertion the mock LLM endpoint received zero requests (docx-independent forerunner of AC3) — verify: `pytest -q tests/pipeline/test_render_md_html.py`
- [ ] task-13: `sopforge-server` FastAPI app — `POST /sessions` (manifest + PNGs), `GET /sessions/{id}/status`, `GET /sessions/{id}/report`, doc download endpoints; `src/pipeline/webui/` plain-HTML review page rendering the sidecar report section — verify: `pytest -q tests/pipeline/test_server.py`
- [ ] task-14: Golden-file test infrastructure — docx unzip + `word/document.xml` byte-compare helper with timestamp/rsid normalizer, unit-tested against synthetic docx zips (engine-independent, AC5 plumbing) — verify: `pytest -q tests/pipeline/test_golden_infra.py`
- [ ] task-15: SOP Factory 2 engine wired in as the docx assembler (extend, don't rewrite) — template-mode fixture manifest → complete docx with zero mock-LLM requests (AC3); commit golden `word/document.xml` and byte-compare via task-14 infra (AC5) — verify: `pytest -q tests/pipeline/test_docx_e2e.py tests/pipeline/test_golden_docx.py`
  <!-- If C:\Users\Brian\Documents\SOP_Factory_2 is still absent when this task is reached: STOP and escalate per CLAUDE.md prime directive 1 — do not write a from-scratch docx engine under the SOP Factory 2 name. -->
