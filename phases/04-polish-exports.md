# Phase 4 — Extending Polish to All Six Export Formats

## The problem

`polish.py`'s stage-4 pass (`generate_polish_pass`) runs today, but only
`doc.md` reflects it. `server.py`'s `_write_all_exports` renders the flat
markdown string via `render_markdown`, polishes *that string*, gates it, and
writes `doc.md`. Every other export renders independently, straight from the
pre-polish structured inputs:

```python
# server.py _write_all_exports (current)
md = render_markdown(manifest, step_results, annotated_paths,
                      narrative_text=narrative_text, base_dir=annotated_dir)
# ... polish + gate `md`, write doc.md ...

html_doc = render_html(manifest, step_results, annotated_paths,
                        narrative_text=narrative_text, base_dir=annotated_dir)   # unpolished
assemble_docx(manifest, step_results, annotated_dir, docx_path,
              ..., narrative_text=narrative_text)                               # unpolished
render_pdf(manifest, step_results, annotated_paths, pdf_path,
           narrative_text=narrative_text, ...)                                  # unpolished
render_single_file_html(manifest, step_results, annotated_paths,
                         narrative_text=narrative_text)                         # unpolished
export_markdown_bundle(manifest, step_results, annotated_paths, md_bundle_dir,
                        narrative_text=narrative_text)                          # unpolished
```

I read all six render/export call sites directly (not the docstrings) to
confirm their actual signatures:

| Function | File | Signature (structured inputs) |
|---|---|---|
| `render_markdown` | `render.py:91` | `(manifest, step_results, annotated_paths, narrative_text=None, base_dir=None)` |
| `render_html` | `render.py:139` | same shape |
| `assemble_docx` | `docx_assembler.py:86` | `(manifest, step_results, annotated_dir, output_path, revision, date, author, doc_no, narrative_text=None)` |
| `render_pdf` | `export_pdf.py:150` | `(manifest, step_results, annotated_paths, output_path, narrative_text=None, revision, date, author, doc_no)` |
| `render_single_file_html` | `export_html.py:30` | `(manifest, step_results, annotated_paths, narrative_text=None)` |
| `export_markdown_bundle` | `export_md.py:18` | `(manifest, step_results, annotated_paths, output_dir, narrative_text=None)` — internally just calls `render_markdown` and rewrites image links |

Every one of the six takes the **same two structured inputs** —
`step_results` (a list of `{"step_id", "text", ...}` dicts, one per manifest
step, joined via `zip(..., strict=True)`) and `narrative_text` (a single
string, with `docx_assembler.py`/`export_pdf.py` both scanning it line-by-line
for `> [verify] (id): ...` blockquotes via `claim_coverage.parse_verify_line`).
None of them accepts a flat markdown string. That's the architectural blocker
the Arbiter named, confirmed by direct reading rather than assumption.

### A third per-step field: `narration`

`step_results[i]` can also carry an optional third prose field,
`"narration"` — populated at `server.py:800` (`result["narration"] =
narration`) from either an uploaded transcript placed verbatim under each
step, or `narration_polish.py`'s `polish_narration` (stage 2 of the
audio-narration pipeline). When present, it is rendered — separately from
`result["text"]` — by four of the six call sites: `render_markdown`
(`render.py:106-107`, as a `> **Narration:** ...` blockquote line in the
`md` string), `render_html` (`render.py:156-159`, as a
`<blockquote class="narration">`), `assemble_docx` (`docx_assembler.py:140-
141`, as a sub-bullet under each step), and `export_pdf` (`export_pdf.py:254-
261`, as an italic line under each step's bullet). `export_markdown_bundle`
inherits it transitively, since it just calls `render_markdown` internally.
Only `render_single_file_html` (`export_html.py`) never renders it — it has
no per-step narration handling at all, unlike its structural twin
`render_html`.

So "same two structured inputs" above describes the two fields every one of
the six functions' *signatures* accepts identically (`step_results`,
`narrative_text`); it is not a claim that `step_results[i]["text"]` is the
only prose `step_results` carries. `narration` is a third, distinct,
separately-rendered prose field, present in most but not all of the six
outputs. Whether it is in scope for the field-level polish this doc plans
is resolved below, in "Next cycle's implementation plan."

## The three options

### (a) Relocate polish to run on structured fields, pre-render

Polish `narrative_text` and each `step_results[i]["text"]` as separate
fields, *before* any of the six renderers run. All six then consume
already-polished `step_results`/`narrative_text` — no exporter changes
needed, because they already all take exactly this shape.

### (b) Reparse polished flat markdown back into structured data

Keep polishing the assembled `md` string (as today), but write a parser that
reverses `render_markdown`'s format — split on `## {heading}` boundaries,
recover each step's text, strip the trailing `![...](...)` image line and
`> **Narration:**` line back out, recover `narrative_text` as the preamble
before the first `##`. Feed the recovered structured data to the other five
renderers.

### (c) Render docx/pdf/html/single-html/md-bundle from polished markdown via a markdown-to-format converter

Replace the current renderers' consumption of structured fields with a
generic markdown → docx/pdf/html converter fed the polished flat `md`.

## Decision: (a)

**Rejecting (c) first, decisively:** `docx_assembler.py` drives the external
SOP Factory 2 `SOPBuilder` engine — title page, a real Word TOC field
(`add_toc_field`), per-step bullets with the clicked element's name bolded
inline (`_step_bullet` — only possible because it has the manifest's
`step.element.name`, which a markdown string doesn't carry), styled red
`[verify]` callouts (`_narrative_body`), captioned images keyed by
`step.screenshot`, and a revision-history table. `export_pdf.py` mirrors all
of this by hand in fpdf2. None of that is recoverable from a markdown string
— a converter would have to either drop this fidelity or reimplement the
entire SOPBuilder-driving logic from parsed markdown, which is a rewrite of
the assembler, not an extension of it. CLAUDE.md's architecture section is
explicit: "Contains the SOP Factory 2 engine as the docx baseline — extend
it, do not rewrite it." (c) violates that directly. Rejected.

**Rejecting (b):** it doesn't remove the fragility the Arbiter's WHY already
flagged — it *adds* to it. Recovering structured fields from a freely
rewritten flat string is only reliable if the polish LLM preserves exact
heading text and paragraph boundaries; `polish.py`'s own docstring says the
prompt permits "fix grammar, punctuation, and phrasing" freely, and nothing
in `_gate` checks that a `## {heading}` line survived byte-for-byte. A
reparse keyed on heading text breaks silently the first time a heading gets
re-punctuated; keying on position/order instead is more robust but still
requires writing and maintaining a parser that must be re-synced by hand
every time `render_markdown`'s format changes (adding a field, changing
image-line placement, etc.) — two representations of the same format that
can drift apart. (a) needs no such inverse function at all: it operates
before the single forward transform (`render_markdown`) that already exists,
instead of undoing it after the fact. Rejected.

**Choosing (a):** it requires no changes to any of the five untouched
renderers (`render_html`, `assemble_docx`, `render_pdf`,
`render_single_file_html`, `export_markdown_bundle`) — they already accept
polished-or-not `step_results`/`narrative_text` interchangeably, because
nothing in their signatures or bodies distinguishes the two. The only new
code is a polish function that operates on the same field granularity
`narration_polish.py` (stage 2 of the audio-narration pipeline) already
polishes at — a pattern that's already proven in this codebase, not a new
one being introduced.

## How cycle 3's claim-coverage net + rejection-fallback is preserved

Two things from cycle 3 must survive, and both do, at *finer* granularity
than today rather than coarser:

1. **The safety net.** `claim_coverage.validate_claim_coverage(final_text,
   claims)` (`claim_coverage.py:77`) is a pure function of a text string and
   a claim list — it doesn't care whether that string is the whole assembled
   `doc.md` or just `narrative_text` in isolation. I traced where claims can
   actually appear: `generate_narrative` (`narrative.py:33`) is the *only*
   producer of claim-bearing text, and it already runs every claim through
   `ensure_claim_coverage` (`claim_coverage.py:56`) before returning
   `narrative_text` — appending `> [verify] (claim-id): ...` blockquotes for
   anything the draft didn't cover. `step_results[i]["text"]` is never
   claim-bearing (steps are template/LLM round-trip-gated per invariant
   L2/L3, an entirely separate mechanism with no claim IDs involved). So
   today's whole-`md` claim-coverage check is *already* only meaningfully
   checking `narrative_text`'s content — running
   `validate_claim_coverage(polished_narrative_text, claims)` against just
   the polished narrative field is not a weaker check, it's the same check
   with the irrelevant step text no longer diluting it.
2. **The rejection-fallback.** Today, a failed check discards the *entire*
   polish pass and reverts *all* of `doc.md`. Under (a), the fallback moves
   to field granularity: if `polished_narrative_text` fails
   `validate_claim_coverage`, only the narrative field reverts to its
   pre-polish value — the polished step texts (which never had claims to
   drop) are kept. This is strictly no weaker than today (a dropped claim
   still always reverts its field to the known-good original, never ships
   silently) and is more useful (a bad narrative rewrite no longer forces
   perfectly good step-text polish to be thrown away too). `report["polish_rejected_claim_coverage"]`
   keeps recording the missing claim IDs exactly as it does today, from the
   same call site shape.

## Gate-scope resolution: per-field, not whole-document

**Move to a per-field gate, modeled on `narration_polish.py`'s approach —
not `polish.py`'s current whole-document gate.** Reasons, grounded in what I
read in both files:

- Option (a) *requires* it mechanically: once polish operates on
  `narrative_text` and each step's text as separate values (not one flat
  string), there is no longer a single "document" for a whole-document gate
  to run against. The gate has to key off whichever field it's evaluating.
- `narration_polish.py` already solves the exact problem cycle 4 introduces
  — polishing N short, independent text units in one LLM call via a
  JSON-array request (`_build_prompt`/`polish_narration`,
  `narration_polish.py:109-175`), gating each unit's rewrite independently,
  and falling back to that *one unit's* verbatim original on a gate failure
  or a missing/malformed reply (`meta["steps_kept_verbatim"]`) — without
  discarding the other units' successful rewrites. That is precisely the
  finer-grained fallback behavior the claim-coverage section above needs for
  `narrative_text` vs. `step_results`. Reusing this pattern for `polish.py`
  means one proven mechanism serves both the audio-narration path and the
  stage-4 polish path, instead of the codebase carrying two different
  gating philosophies (whole-doc reject-everything vs. per-segment
  reject-one) side by side.
- Per-field gating is a genuine quality improvement over today's
  whole-document gate, not just an artifact of the refactor: today, if the
  polish LLM corrupts *one* step's phrasing badly enough to trip
  `_gate`'s invented-content or dropped-fact check, the entire document's
  polish is discarded — narrative and every other step revert to unpolished
  too, even though they were fine. Per-field gating isolates the damage to
  the one field that actually failed.
- One real gap to close while making this move, not to carry forward
  silently: `polish.py`'s current `_gate` has a denylist check
  (`_DENYLIST_WORDS`, `_DENYLIST_INFRA_NOUNS` — rejects unconditionally on
  a newly introduced destructive verb like "format"/"delete"/"restart the
  server", regardless of how small the novel-word cluster is) that
  `narration_polish.py`'s `_gate` does **not** have. The next cycle's
  per-field gate must carry `polish.py`'s denylist check forward — it exists
  specifically because a fraction/absolute novel-word-count threshold alone
  is provably evadable by an attacker reusing the document's own vocabulary
  (see `polish.py`'s own comment above `_DENYLIST_WORDS`). Silently reverting
  to `narration_polish.py`'s weaker gate would be a real regression in the
  safety net, not a neutral refactor.
- One thing the whole-document gate happened to catch "for free" that a
  naive per-field gate could miss: a field silently *absent* from the
  model's JSON reply. `narration_polish.py` already handles this
  (`rewrite_by_id.get(sid)` returning `None` → keep verbatim,
  `narration_polish.py:150-154`) — the next cycle's field-polish function
  must apply the same "missing reply key ⇒ keep this field's original text"
  rule, not assume the reply always contains every field.

## Narration's fate: in scope, because leaving it out is a silent regression

Option (a), as originally scoped in this doc (`narrative_text` +
`step_results[i]["text"]` only), has a real gap against *today's* behavior,
not just against a hypothetical: today, `_write_all_exports` polishes the
whole assembled `md` string (`generate_polish_pass(md, polish_llm)`,
`server.py:656`), and `render_markdown` puts each present
`result["narration"]` into that same `md` string as a `> **Narration:**
...` line (`render.py:106-107`) *before* the polish call runs. So today,
`doc.md`'s narration text already receives the tone/grammar pass — it isn't
carved out. If cycle 5 implements `generate_polish_fields` against only
`narrative_text` and `step_results[i]["text"]`, narration stops being
polished not just in the five formats that never touched it before, but in
`doc.md` too — a silent regression from current, shipped behavior, in the
one format where polish already works today.

**Resolution: bring narration into scope.** `generate_polish_fields` must
also submit each present `step_results[i]["narration"]` as its own
JSON-array item, gated and falling back exactly like `narrative_text` and
`step_results[i]["text"]`. This isn't new complexity bolted on — it's the
same per-field mechanism (`narration_polish.py`'s proven JSON-array-of-
short-units pattern, already cited above for the other two fields) applied
to a third field of the same shape (a short, independent prose string keyed
by `step_id`). The alternative — deliberately excluding narration and
documenting it as a disclosed limitation — was considered and rejected
specifically because it would *ship* a regression (fewer formats polished
than today, for a field that already works) rather than merely *withhold*
an enhancement; that bar is higher than this doc's other narrowing
decisions (e.g. rejecting options b/c), which is why narration gets pulled
in rather than deferred.

Net effect once this lands: narration goes from being polished in 1 of 6
formats today (`doc.md` only, via the whole-blob pass) to being polished in
5 of 6 (every format that renders it: `doc.md`, `doc.html`, `doc.docx`,
`doc.pdf`, and the markdown bundle) — `render_single_file_html` still won't
render narration at all, per-field polish or not, since it has no narration
handling to begin with (see the "third per-step field" subsection above).
That gap is pre-existing and orthogonal to this cycle's polish work, not
introduced or worsened by it.

## Next cycle's implementation plan (files/functions)

1. **`src/pipeline/polish.py`** — replace `generate_polish_pass(document_text,
   llm)` with a field-level function, e.g.
   `generate_polish_fields(narrative_text, step_results, llm) ->
   (polished_narrative_text, polished_step_results, meta)`:
   - Build one JSON-array prompt covering every field in a single LLM call
     (one item for `narrative_text` when present, one per
     `step_results[i]["text"]`, plus one more per *present*
     `step_results[i]["narration"]` — skip the item entirely for steps
     where `"narration"` is absent/empty, matching `render_markdown`'s own
     `result.get("narration")` truthiness check — each tagged with a stable
     field id: the step's `step_id` for the text item, `f"{step_id}:narration"`
     for the narration item (distinct from the text item's id, since a step
     can have both), plus a reserved id like `"narrative"`), mirroring
     `narration_polish._build_prompt`/`polish_narration`'s JSON-array
     request/response shape.
   - Factor a new `_field_gate(original, rewrite)` out of the union of
     `polish.py`'s current `_gate` (degenerate-shape check, dropped-literal-
     fact check, invented-content check *including* the denylist, length-
     ratio check) — i.e., keep every one of `polish.py`'s existing checks,
     just scoped to one field's text instead of a whole document. Applies
     identically to narration items — same gate, same fallback rule, no
     narration-specific carve-out. Drop the whole-document-only framing
     from the docstring/module comment.
   - Any field missing from the reply, or failing `_field_gate`, keeps its
     original text (matching `narration_polish.polish_narration`'s per-step
     fallback) — recorded in `meta` for the sidecar report. This applies to
     `"narration"` items exactly as it does to `"text"` and `"narrative"`
     items: a rejected or absent narration rewrite reverts that one step's
     narration to its pre-polish value, without touching that step's `text`
     or any other step's fields.
   - Never raises: any exception during the call/parse returns all fields
     unchanged, matching the existing "polish can never corrupt or block a
     document" discipline.
2. **`src/pipeline/server.py`** (`_write_all_exports`, ~line 603-729) — call
   `generate_polish_fields` once, right after `step_results`/`narrative_text`
   are available and before any of the six render/export calls, producing
   `polished_narrative_text`/`polished_step_results`. `polished_step_results`
   carries both each step's polished `"text"` and, for steps that had one,
   its polished `"narration"` — the function returns whole updated
   `step_results` dicts, not a text-only shadow structure, so no separate
   merge step is needed to get polished narration back onto
   `step_results[i]["narration"]` before rendering. Run
   `validate_claim_coverage(polished_narrative_text, claims)`
   (`claim_coverage.py:77`, already imported) against just the narrative
   field; on failure, revert only `polished_narrative_text` to the original
   `narrative_text` and keep recording `report["polish_rejected_claim_coverage"]`
   — narration fields are never claim-bearing (same reasoning as
   `step_results[i]["text"]` above) so they're never subject to this check;
   a rejected/missing narration reply is handled entirely inside
   `generate_polish_fields`'s own per-field fallback, upstream of this call.
   Then pass `polished_step_results`/`polished_narrative_text` (instead of
   the pre-polish `step_results`/`narrative_text`) into all six calls:
   `render_markdown`, `render_html`, `assemble_docx`, `render_pdf`,
   `render_single_file_html`, `export_markdown_bundle`. Remove the old
   flat-`md`-polish block (lines ~638-676) entirely — `doc.md` is written
   from `render_markdown` using the already-polished fields like every other
   format, not re-polished separately.
3. **`tests/pipeline/test_polish.py`** — rewrite against the new
   `generate_polish_fields` signature (the existing whole-document gate
   tests, e.g. `test_gate_rejects_a_denylisted_word_smuggled_in_via_reused_vocabulary`,
   `test_gate_rejects_a_fabricated_extra_step`, need field-scoped
   equivalents proving the denylist/fraction/absolute checks still hold at
   field granularity). Add coverage specific to the narration item: a step
   with a present `"narration"` gets a `f"{step_id}:narration"` prompt item
   and its own gated rewrite; a step with no `"narration"` key contributes
   no item and comes back unchanged; a rejected/missing narration reply
   reverts only that step's `"narration"`, leaving that same step's `"text"`
   (and every other field) untouched.
4. **`tests/pipeline/test_server_exports.py`** — add coverage asserting all
   six export formats (not just `doc.md`) reflect a successful polish pass,
   including that a step's rendered narration (in `doc.md`, `doc.html`,
   `doc.docx`, `doc.pdf`, and the markdown bundle) reflects the polished
   text, not the pre-polish original; and that a rejected narrative-claim-
   coverage check reverts only the narrative section while step-text and
   step-narration polish elsewhere in the same doc are still kept.

Not touched: `render.py`, `docx_assembler.py`, `export_pdf.py`,
`export_html.py`, `export_md.py` — under option (a) none of the five
existing renderers change at all, confirming this actually is the smaller
diff of the three options, not just the architecturally cleaner one.
