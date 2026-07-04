# Phase 2 — Generation Pipeline

FastAPI server that turns a capture session into a validated SOP document. Fully
verifiable from `fixtures/` — no interactive session, no real Ollama required in CI
(mock the endpoint; one opt-in integration test may hit a real endpoint if
`SOPFORGE_OLLAMA_URL` is set).

## Deliverables

- `sopforge-server`: FastAPI app. `POST /sessions` (manifest + PNGs), `GET
  /sessions/{id}/status`, `GET /sessions/{id}/report`, doc download endpoints.
- SOP Factory 2 engine wired in as the docx assembler (extend, don't rewrite).
- LLM client: OpenAI-compatible, endpoint + model per section from
  `config/models.toml`. Anthropic routing flag per section, default off.
- Step generation: per-record, ID-attached, code reassembly (invariant L1).
- Round-trip validator per step (invariant L2); template fallback (L3).
- Narration path: whisper transcription (local, faster-whisper), claim extraction
  with timestamps, claim-coverage validator, `[verify]` blockquote rendering (L4).
- Multi-pass narrative mode: draft → critique → revise, pass count configurable.
- Screenshot annotation: red circle/arrow at click coords (Pillow).
- Sidecar review report (L5): JSON + rendered section in the web UI.

## Acceptance criteria (record in phases/02-results.md)

1. Structural validation suite green: property test over randomly generated
   manifests (hypothesis) proves zero-drop / zero-invent / order-preserved for
   1,000 cases.
2. Round-trip validator: ≥95% pass rate on `fixtures/` manifests with the mock
   LLM returning realistic step text; failures demonstrably fall back to template,
   never to a retry loop (assert max 1 generation attempt per step).
3. Template mode end-to-end: fixture manifest → complete docx with zero LLM calls
   (assert the mock endpoint received no requests).
4. Claim coverage: fixture transcript → every extracted claim ID present in output
   or rendered as `[verify]` blockquote; validator fails a doc with a dropped claim
   (negative test included).
5. Golden-file test: reference fixture session → docx; unzip and byte-compare
   `word/document.xml` against the committed golden copy (normalize timestamps/rsids
   before compare).
6. Annotated screenshots: click coords land inside the drawn marker (pixel assert).
7. Sidecar report correctly lists: every template-fallback step, every `[verify]`
   claim, every empty-metadata step, for a fixture crafted to contain all three.
