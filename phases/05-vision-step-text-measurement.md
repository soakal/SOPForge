# Phase 5 — Vision-in-Step-Text: Measurement Report

This is a measurement report, not a recommendation. It presents what the live
comparison run actually produced. The decision whether `use_vision` for step
text is worth shipping, defaulting on, or dropping is left to a human.

Source data: `scripts/vision_measurements/vision_step_measurement_20260711T220712Z.json`
(committed alongside this report). Every number and quoted string below is
copied verbatim from that file — nothing here is paraphrased or re-derived
from a prior cycle's summary.

## Methodology

- **Script:** `scripts/measure_vision_steps_live.py`.
- **What was compared:** for each step, two direct calls to the production
  `generate_step_text()` function (`src/pipeline/generation.py`) — one with
  `use_vision=False` ("off"), one with `use_vision=True` plus the step's real
  screenshot ("on"). Both calls go through the identical prompt builder,
  round-trip validation gate, and template-fallback rule; the only variable
  that changes between the two calls is whether an `image_url` content block
  is attached to the request.
- **Model:** `qwen2.5vl:7b` for **both** arms (off and on), against
  `http://192.168.200.60:11434/v1`. This is a deliberate override — the
  `[steps]` section's default model is `qwen3:32b`, which is text-only, so
  comparing it against a vision model would have conflated "different model"
  with "vision vs. no vision." Using the same vision-capable model for both
  arms isolates the image attachment as the only variable.
- **Data:** 20 steps total, drawn from two real captured sessions on disk
  (not fixtures, not a synthesized manifest) — 10 steps each from session
  `58fbb2b5-bc04-4fd4-99e3-9099f446c0b5` and session
  `e7ce9b62-6a80-4b15-9154-01bf378b81b6`.
- **Timestamp:** `20260711T220712Z` (UTC).
- **Pass/fail signal used below:** the production round-trip gate. If the
  model's reply fails round-trip validation, `generate_step_text()` silently
  substitutes the deterministic, manifest-interpolated template string
  (`used_fallback_*=true` in the data). A row where `used_fallback=false`
  means the model's own generated text passed validation and was used as-is.

## Outcome categories

Categorizing all 20 rows by whether the off arm and on arm each passed the
round-trip gate (`used_fallback_off`/`used_fallback_on`) gives:

| Category | Definition | Count |
|---|---|---|
| Fixed | off fell back to template, on passed | 4 |
| Broke | off passed, on fell back to template | 4 |
| Both failed | both arms fell back to template | 2 |
| Both passed | both arms passed | 10 |

That's 4 fixed + 4 broke + 2 both-failed + 10 both-passed = 20/20 rows, an
even split between "vision changed the pass/fail outcome for the better" and
"vision changed it for the worse."

Full per-row breakdown:

| Session | Step | Action | Element | Category | off fallback | on fallback | text changed |
|---|---|---|---|---|---|---|---|
| 58fbb2b5… | step-001 | click | Show Hidden Icons Hide | Broke | false | true | true |
| 58fbb2b5… | step-002 | click | (none) | Both passed | false | false | false |
| 58fbb2b5… | step-003 | click | Double Driver Backup | Both passed | false | false | true |
| 58fbb2b5… | step-004 | click | Items View | Fixed | true | false | true |
| 58fbb2b5… | step-005 | click | (none) | Both passed | false | false | true |
| 58fbb2b5… | step-006 | click | Name | Broke | false | true | true |
| 58fbb2b5… | step-007 | click | Double Driver Backup | Fixed | true | false | true |
| 58fbb2b5… | step-008 | click | Address Bar | Broke | false | true | true |
| 58fbb2b5… | step-009 | click | Items View | Both passed | false | false | false |
| 58fbb2b5… | step-010 | type | Items View | Both passed | false | false | true |
| e7ce9b62… | step-001 | click | Show Hidden Icons Hide | Both passed | false | false | true |
| e7ce9b62… | step-002 | click | (none) | Both passed | false | false | true |
| e7ce9b62… | step-003 | click | Double Driver Backup | Both failed | true | true | false |
| e7ce9b62… | step-004 | click | Items View | Fixed | true | false | true |
| e7ce9b62… | step-005 | click | (none) | Both passed | false | false | true |
| e7ce9b62… | step-006 | click | Name | Broke | false | true | true |
| e7ce9b62… | step-007 | click | Double Driver Backup | Both failed | true | true | false |
| e7ce9b62… | step-008 | click | Address Bar | Both passed | false | false | true |
| e7ce9b62… | step-009 | click | Items View | Both passed | false | false | true |
| e7ce9b62… | step-010 | type | Items View | Fixed | true | false | true |

Source file's own `summary` block (a different, narrower metric than the
table above — see "A note on the source file's own summary metric" below):

```json
{
  "total_rows": 20,
  "fallback_off_count": 6,
  "fallback_on_count": 6,
  "non_fallback_on_count": 14,
  "changed_meaningful_count": 12,
  "all_vision_on_fell_back": false,
  "measurement_meaningful": true
}
```

`fallback_off_count` (6) = Fixed (4) + Both failed (2). `fallback_on_count`
(6) = Broke (4) + Both failed (2). Both check out against the table above.

## Verbatim before/after examples

**Fixed** — session `58fbb2b5…`, step-004 (click, element "Items View",
window "Double Driver Backup - File Explorer"):
- `text_off` (fell back to template): `"Click the 'Items View' List in the 'Double Driver Backup - File Explorer' window."`
- `text_on` (vision, passed): `"Select 'Items View' in the Double Driver Backup - File Explorer window."`

**Broke** — session `58fbb2b5…`, step-001 (click, element "Show Hidden Icons
Hide", window "Taskbar"):
- `text_off` (passed): `"Click 'Show Hidden Icons Hide' in 'Taskbar'."`
- `text_on` (fell back to template): `"Click the 'Show Hidden Icons Hide' Button in the 'Taskbar' window."`

**Both failed** — session `e7ce9b62…`, step-003 (click, element "Double
Driver Backup", window "Documents - File Explorer"):
- `text_off` (fell back to template): `"Click the 'Double Driver Backup' ListItem in the 'Documents - File Explorer' window."`
- `text_on` (fell back to template): `"Click the 'Double Driver Backup' ListItem in the 'Documents - File Explorer' window."`
- Identical text in both arms, as expected — the template fallback is
  deterministic string interpolation from the manifest, independent of
  vision, so both arms landing on fallback necessarily produces the same
  string (`text_changed: false`).

**Both passed** (for contrast — an example where vision changed nothing
material) — session `58fbb2b5…`, step-009 (click, element "Items View",
window "Double Driver Backup - File Explorer"):
- `text_off`: `"Click 'Items View' in the 'Double Driver Backup - File Explorer' window."`
- `text_on`: `"Click 'Items View' in the 'Double Driver Backup - File Explorer' window."`
- Same string, `text_changed: false`.

### A note on the source file's own summary metric

The JSON's `summary.changed_meaningful_count` (12) is *not* the same
partition as the Fixed/Broke/Both-failed/Both-passed table above. It's
defined in `measure_vision_steps_live.py::_summarize()` as: among the 14 rows
where vision-ON did **not** fall back (`non_fallback_on_count`), how many had
`text_on != text_off`. That's 12 of 14 — i.e. even within the "Both passed"
and "Fixed" categories (the 14 rows where vision-on validated successfully),
vision-on's wording differed from vision-off's wording in all but 2 of them
(the two "Both passed, text unchanged" rows shown above, `58fbb2b5…` step-002
and step-009). Most of that textual difference is stylistic rewording
("Click X" vs. "Select X", "in the Y window" vs. "in Y window") rather than a
change in what element or window is being described — the round-trip gate
independently confirms `{action, element, window}` still matches the
manifest in both arms whenever `used_fallback=false`.

## Latency

Every row recorded wall-clock latency for each arm's `generate_step_text()`
call (`latency_off_s` / `latency_on_s` in the source file).

| | off (vision disabled) | on (vision enabled) |
|---|---|---|
| Min | 0.202s | 10.694s |
| Max | 4.303s (one outlier — see below) / 0.877s (all other 19 rows) | 11.126s |
| Mean (all 20 rows) | 0.585s | 10.916s |
| Mean (excluding the one outlier) | 0.389s (19 rows) | 10.916s |

19 of the 20 off-arm rows fall between 0.202s and 0.877s. The one exception
is session `58fbb2b5…` step-001, the very first row processed in the run,
at `latency_off_s: 4.303` — noticeably higher than every other off-arm
measurement (next highest is 0.86s) and consistent with a cold-start/first-
request cost rather than a per-row pattern; every subsequent off-arm call in
the run (including the immediately following on-arm call for that same step,
`latency_on_s: 10.955`, in line with all other on-arm calls) returned to the
normal range.

Every on-arm row, regardless of session, step, or pass/fail outcome, landed
in a tight 10.694s–11.126s band — the vision call's cost is dominated by
fixed image-inference latency, not by which category (fixed/broke/both-
failed/both-passed) the row ended up in.

Row-level slowdown ratios (on ÷ off, excluding the one off-arm outlier) range
from about 12.5x (session `e7ce9b62…` step-006: 0.877s → 10.997s) to about
53x (session `58fbb2b5…` step-005: 0.202s → 10.768s), averaging roughly 28x
(mean on 10.916s ÷ mean off 0.389s) across the 19 non-outlier rows.

## Summary

Across 20 real steps from 2 real captured sessions, attaching the step's
screenshot and enabling vision (`qwen2.5vl:7b`, same model used for both
arms) changed the round-trip pass/fail outcome for 8 of 20 steps — 4 for the
better (Fixed) and 4 for the worse (Broke) — with 2 steps failing in both
arms and 10 passing in both arms. On correctness, as measured by the
production round-trip gate, this run is a net wash: equal counts of
newly-passing and newly-failing steps, no directional signal either way.

On latency, the picture is unambiguous and one-directional: every vision-on
call took roughly 10.7–11.1 seconds versus roughly 0.2–0.9 seconds without
vision (one cold-start outlier aside) — on the order of 25–50x slower per
step, consistently, regardless of whether that step's outcome improved,
worsened, or stayed the same.

This report makes no adopt/reject recommendation. It is presented for a
human to weigh the wash on correctness against the consistent, substantial
latency cost and decide.
