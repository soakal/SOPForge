# Phase 2 — Acceptance Results

All 15 tasks complete, each independently reviewed (reviewer subagent) and, where
findings surfaced, fixed and re-reviewed to PASS before moving on. Full suite:
**160 passed, 4 skipped** (skips are all correctly gated: 2 opt-in real-endpoint
tests with no env var set, 1 opt-in real-whisper-model test, 1 Phase 1
hardware-dependent narration test). `ruff check` / `ruff format --check` clean.

## Criterion 1 — Structural validation suite (invariant L1)

**Requirement:** property test over randomly generated manifests (hypothesis)
proves zero-drop / zero-invent / order-preserved for 1,000 cases.

**Evidence:** `tests/pipeline/test_property_mapping.py::test_assemble_steps_never_drops_invents_or_reorders`

```
tests/pipeline/test_property_mapping.py::test_assemble_steps_never_drops_invents_or_reorders:
  - during generate phase (28.86 seconds):
    - Typical runtimes: ~ 3-45 ms, of which ~ 2-30 ms in data generation
    - 1000 passing examples, 0 failing examples, 31 invalid examples
  - Stopped because settings.max_examples=1000
1 passed in 29.85s
```

The strategy varies step count (1–20), action (click/type), and window/element
field combinations across 1,000 generated manifests; each is round-tripped
through `load_manifest` (real jsonschema + pydantic validation, nothing bypassed)
and `assemble_steps`. Reviewer mutation-tested this: reversed order, dropped-last,
and duplicated-id mutants were all caught (3/3), confirming the test is not
tautological despite `assemble_steps` being a simple list comprehension.

**PASS.**

## Criterion 2 — Round-trip validator (invariant L2)

**Requirement:** ≥95% pass rate on `fixtures/` manifests with the mock LLM
returning realistic step text; failures fall back to template, never a retry
loop (max 1 generation attempt per step, asserted on mock call count).

**Evidence:** `tests/pipeline/test_step_generation.py` — 6 passed.

- `test_realistic_mock_achieves_at_least_95_percent_round_trip`: 6/6 steps across
  `sample-manifest.json` + `empty-elements-manifest.json` round-trip correctly with
  realistic mock replies (100%, comfortably above the 95% bar), one LLM call per step.
- `test_injected_mismatch_falls_back_with_exactly_one_attempt_and_no_retry`: a
  deliberately wrong mock reply falls back to the exact template output, with
  `len(client.calls) == 1` — never retried.
- `test_llm_exception_falls_back_with_exactly_one_attempt` /
  `test_malformed_response_falls_back_with_exactly_one_attempt`: any generation
  failure (exception, malformed response) falls back the same way, one attempt only.

**PASS.**

## Criterion 3 — Template mode end-to-end (docx + LLM-free)

**Requirement:** fixture manifest → complete docx with zero LLM calls (mock
endpoint asserted to receive zero requests).

**Evidence:** `tests/pipeline/test_docx_e2e.py` (5 passed) +
`tests/pipeline/test_render_md_html.py` (8 passed) — 13 passed total.

- `test_docx_assembler_signature_has_no_llm_client_parameter` /
  `test_template_mode_signature_has_no_llm_client_parameter`: neither
  `assemble_docx` nor `render_steps_template_mode` even accept an LLM client.
- `test_template_mode_docx_end_to_end_makes_zero_llm_requests` /
  `test_template_mode_end_to_end_makes_zero_llm_requests`: patches
  `httpx.Client.send` (the transport-level chokepoint every real LLM request must
  cross, regardless of how/where a client gets constructed) for the duration of a
  full manifest → annotated screenshots → docx/md/html run; asserts the call count
  stays at 0. Verified live (reviewer's second round) that this actually catches a
  regression: a scratch-injected real `LLMClient.chat()` call made the test fail
  with `assert 1 == 0`.
- Docx assembler uses SOP Factory 2's real `SOPBuilder` engine
  (`C:\Users\Brian\Documents\SOP_Factory_2\template\sop_lib.py`, cloned from the
  private GitHub repo `soakal/SOP-Factory`) — extended (new call order), not rewritten.

**PASS.**

## Criterion 4 — Claim coverage (invariant L4)

**Requirement:** fixture transcript → every extracted claim ID present in output
or rendered as `[verify]` blockquote; validator fails a doc with a dropped claim
(negative test included).

**Evidence:** `tests/pipeline/test_claim_coverage.py` — 9 passed.

- `test_validator_fails_when_a_claim_is_dropped_entirely`: the explicit negative
  test — a doc that neither covers nor flags a claim fails validation
  (`ok is False`, `missing == ["claim-003"]`).
- `test_flagged_marker_alone_satisfies_validation_without_content_coverage`: proves
  the `[verify]`-flagged branch is independently load-bearing (not shadowed by
  content-coverage always being true for `ensure_claim_coverage`'s own output).
- `test_whitespace_only_claim_text_is_never_treated_as_covered`: an empty/blank
  claim text can never trivially "cover itself" via substring-of-everything.
- End-to-end: `tests/pipeline/test_sidecar_report.py` and
  `tests/pipeline/test_render_md_html.py` exercise real claim extraction →
  narrative generation → `[verify]` blockquote rendering (as `<blockquote>` in
  HTML, not flattened into escaped inline text) against crafted fixtures.

**PASS.**

## Criterion 5 — Golden-file test (docx byte-compare)

**Requirement:** reference fixture session → docx; unzip and byte-compare
`word/document.xml` against the committed golden copy (normalize
timestamps/rsids before compare).

**Evidence:** `tests/pipeline/test_golden_docx.py` (2 passed) +
`tests/pipeline/test_golden_infra.py` (7 passed) — 9 passed total.

- `fixtures/golden-document.xml` committed (generated from `sample-manifest.json`
  via the real `assemble_docx` → SOP Factory 2 path), pinned against `core.autocrlf`
  corruption via `.gitattributes` (`-text`).
- `test_docx_matches_committed_golden_document_xml`: fresh build byte-matches the
  golden after rsid/timestamp normalization. Verified live that mutating the golden
  fixture makes this test fail (10252 vs 10260 bytes), then restored correctly.
- `test_two_independent_builds_produce_byte_identical_document_xml`: two
  independent builds from the same inputs are byte-identical *before*
  normalization even — this docx path has no inherent rsid/timestamp volatility
  for this fixture, so normalization is defense-in-depth, not a requirement being
  relied on to paper over nondeterminism.
- `test_golden_infra.py`: the normalizer/comparator plumbing (task-14) proven
  against synthetic docx zips — strips volatile fields without ever silently
  erasing genuine content differences (verified both directions).

**PASS.**

## Criterion 6 — Annotated screenshots (click coord inside marker)

**Requirement:** click coordinates land inside the drawn marker (pixel assert).

**Evidence:** `tests/pipeline/test_annotate.py` — 6 passed.

- The marker is always centered exactly on the click coordinate (no
  clamping/shifting near edges — PIL clips any off-canvas portion on its own),
  verified via on-canvas ring-point pixel sampling at exactly `radius` distance
  from the click point, for center, near-corner, and exact-corner (0,0) cases.
- Reviewer mutation-tested this (round 2): deliberately offset centers by
  4–8px were caught by every test case; a 3px offset false-passed 3/4 individual
  cases but was caught by the suite as a whole (near-bottom-right test), with the
  residual tolerance window still geometrically inside the marker's radius either way.

**PASS.**

## Criterion 7 — Sidecar review report (invariant L5)

**Requirement:** correctly lists every template-fallback step, every `[verify]`
claim, every empty-metadata step, for a fixture crafted to contain all three.

**Evidence:** `tests/pipeline/test_sidecar_report.py` — 4 passed.

- `fixtures/review-report-manifest.json` + `fixtures/review-report-transcript.json`
  committed, crafted so that running them through the real
  generation/narrative/coverage pipeline (not synthetic/forced results) naturally
  produces all three categories simultaneously:
  `test_sidecar_report_captures_all_three_categories_from_crafted_fixtures` traces
  through real `round_trip_ok`/`_claim_covered` logic (verified by hand in review)
  to land exactly on `template_fallback_steps == ["step-003"]`,
  `empty_metadata_steps == ["step-002"]`, `verify_claims == [claim-002]`.
- `_verify_marker` is a single source of truth shared between the renderer and the
  validator (task-08 fix), so the sidecar report and rendered docs can't drift
  out of sync on the marker format.

**PASS.**

## Summary

All 7 acceptance criteria verified explicitly against real test runs (not just
"tests exist" — mutation-tested and live-regression-tested where a claim
warranted it). Phase 2 is green. Proceeding to Phase 3.
