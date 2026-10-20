# SOPForge audit — CONSENSUS

Base: `41b70ef4fb45c82a5242d27c063f52099df8e72f` · rounds: 3 · agreed: 18 · unresolved: 1

## [HIGH] A-01 — Round-trip validation accepts contradictory and additional destructive instructions

Location: `src/pipeline/roundtrip.py:33` · checklist #6

```
The gate checks verb presence and element/window substrings at :33-41. Round-3 probes returned `(True, [])` for both `Do not click Save in the Editor window.` and `Click Save in the Editor window, then delete all records.` generation.py:97 uses this gate to accept generated text.
```

**Fix:** Reject negation and additional action clauses; use the recorded-action template when semantic fidelity cannot be established. Add regression cases for both examples.

## [HIGH] A-03 — Reachable narrative pipeline checks omissions but accepts invented instructions

Location: `src/pipeline/narrative.py:40` · checklist #1

```
server.py:407 invokes generate_narrative. narrative.py:40-45 runs draft/critique/revise then ensure_claim_coverage; critique/revision prompts at :19-29 omit source claims. The round-3 probe returned `('Open settings. Disable all security controls.', ['claim-001'], [])` for a source claim containing only `Open settings.`.
```

**Fix:** Include source claims in critique/revision and reject unsupported final instructions. Until reliable validation exists, assemble narrative content from source claims.

## [HIGH] A-12 — Environment proxies can route local-only capture and model traffic outside the LAN

Location: `src/pipeline/llm_client.py:49` · checklist #8

```
Reverified llm_client.py:49-53 creates `httpx.Client` with base_url, transport and timeout but no trust_env=False. vision.py:62 and capture/upload.py:62 likewise omit it. With HTTPX's default environment handling, an external proxy setting without a matching bypass can redirect LAN-bound prompts, screenshots and uploads. No actual proxy configuration or egress was established. Round-3 runtime inspection again returned `ModuleNotFoundError: No module named 'httpx'`.
```

**Fix:** Disable environment proxy inheritance for local-only clients and make proxy use explicit. Add a regression verifying that proxy environment variables cannot redirect local traffic.

## [HIGH] F-01 — [narrative] draft->critique->revise is live only for the capture flow; photo builds never call it and ship an ungated LLM overview from the [steps] model

Location: `src/pipeline/server.py:544` · checklist #1

```
Capture flow: server.py:405-411 `narrative_llm = make_narrative_llm_client()` ... `generate_narrative(claims, narrative_llm, passes=models_cfg.narrative.passes)`. Photo flow: server.py:542-546 `llm = make_llm_client()` ... `gen_title, narrative_text = generate_title_and_overview(narration, llm, on_screen_texts=on_screen_texts)` -- the [steps] client, no claims extracted. `_write_all_exports` call at :595-605 has no `claims=`, so :617 `claims=()` and :680 `validate_claim_coverage(md_narrative_text, claims)` is a no-op. Peer A-04 confirms.
```

**Fix:** In `_generate_photo`, build claims from the transcript via `_claims_for_narrative(matches[0] if matches else None, narration)` and produce the overview with `generate_narrative(claims, make_narrative_llm_client(), passes=cfg.narrative.passes)` (keep `generate_title_and_overview` for the title only); pass `claims=claims` to `_write_all_exports` and merge the returned verify ids into `report['verify_claims']`.

## [HIGH] F-02 (= A-05) — Claim-coverage gate silently drops every claim when narrative generation fails, and the narrative stage's [verify] ids never reach the sidecar report

Location: `src/pipeline/server.py:412` · checklist #6

```
server.py:407 `narrative_text, _covered, _verify_ids = generate_narrative(` (`_verify_ids` never read). :412-413 `except Exception:  # noqa: BLE001 - narrative is best-effort, never blocks the doc` / `narrative_text = None`. :423-426 `verify_claims = (placement_meta or {}).get("verify_claims", [])` ... `build_sidecar_report(manifest, step_results, verify_claim_ids, claims_by_id)` -- only placement/narration_polish claims reach the sidecar. Scope: when a transcript was placed under steps (:816-819) the source text survives in the doc; what is lost is the narrative-stage accounting and the claims' [verify] rendering. Peer A-05 confirms. See F-16 for the polish-enabled crash on the same path.
```

**Fix:** In `_generate`: on exception set `narrative_text, _covered, _verify_ids = ensure_claim_coverage("", claims)` so every claim is emitted as a [verify] blockquote; keep `_verify_ids` and extend `verify_claim_ids`/`claims_by_id` with them before `build_sidecar_report`. Add a test with a raising narrative stub asserting `md.count('[verify]') == len(claims)` and `report['verify_claims']` lists them.

## [HIGH] F-03 (= A-04) — Photo build with the committed default [vision].enabled=true replaces the uploaded transcript with captions; transcript text is never placed verbatim under steps

Location: `src/pipeline/server.py:530` · checklist #2

```
config/models.toml:31-32 `[vision]` / `enabled = true`. server.py:526-533 `"text": caption or per_step.get(step.id) or "(no description provided)"`, `"used_fallback": caption is None` -- no `"narration"` key is set in photo mode (contrast `_apply_transcript` server.py:816-819 `result["narration"] = narration`), so render.py's `result.get("narration")` prints nothing. Holds even after `_run_semantic_pipeline` (:521-523) placed the transcript correctly. CLAUDE.md: transcripts 'are placed verbatim under each step.' Peer A-04 confirms.
```

**Fix:** In `_generate_photo`, after computing `per_step`, set `result["narration"] = per_step[step.id]` for every step with transcript text (mirroring `_apply_transcript`) regardless of caption success; add a test uploading a labelled transcript with vision enabled asserting each step's transcript sentence appears in doc.md.

## [HIGH] F-10 (= A-02) — Polish stage rewrites step text after the round-trip gate without re-running it (a rewrite that drops the element name passes), and also rewrites template-fallback steps

Location: `src/pipeline/server.py:659` · checklist #6

```
server.py:659-661 `generate_polish_fields(narrative_text, step_results, polish_llm)`; polish.py:380-382 `items.append((step_id, step["text"]))` with no `used_fallback` check. `_field_gate` polish.py:221-266 checks degenerate shape, digits/paths (:233-236), novel tokens (:250-259), length ratio (:263) -- never `round_trip_ok`. Trace 'Click Save in the Editor window.' -> 'Click in the Editor window.': `novel_tokens == set()`, ratio 0.84 -> `(True, None)`; :473 installs it. server.py:678-680 comment 'Step text/narration polish ... is kept either way' + `validate_claim_coverage(md_narrative_text, claims)` only. Peer A-02's probe returned `(True, None)`.
```

**Fix:** In `_write_all_exports`, after `generate_polish_fields`, loop `zip(manifest.steps, step_results, md_step_results)` and revert `new["text"] = old["text"]` when `old["used_fallback"]` or `not round_trip_ok(new["text"], step)[0]`, recording ids in `report['polish_reverted_steps']`; add a test where the polish stub drops the element name.

## [HIGH] F-16 — With polish enabled, a failed narrative stage crashes the whole generation job: validate_claim_coverage(None, claims) raises AttributeError and no document is written

Location: `src/pipeline/server.py:680` · checklist #6

```
server.py:412-413 sets `narrative_text = None` when `generate_narrative` raises (e.g. Ollama unreachable -- ollama_probe.txt:5), while :444 still passes `claims=claims` (non-empty: synthetic narration always yields claims). :646-652 selects `polish_section` when `[polish].enabled` or a `?polish=local|haiku` rerender override; :654 `md_narrative_text = narrative_text`; :659 `generate_polish_fields(None, step_results, polish_llm)` -> polish.py:421-422 `(narrative_text, [])` for falsy input, returns None either via :446-448 (LLM failure) or :465-467 `_splice_verify_lines(None, [])` -> :361-362 `return body_text`. server.py:680 `ok, missing = validate_claim_coverage(md_narrative_text, claims)` -> claim_coverage.py:84 `_claim_covered(claim, final_text)` -> :23 `stripped.lower() in text.lower()` with `text=None` -> AttributeError. jobs.py:69-72 catches it and marks the job `error`; `doc.md` (:692) is never written. Contradicts the :412 comment 'narrative is best-effort, never blocks the doc'. Static trace (shell denied both rounds); peer A-05's independent probe reproduced `AttributeError 'NoneType' object has no attribute 'lower'`.
```

**Fix:** Guard at server.py:680: `ok, missing = validate_claim_coverage(md_narrative_text or "", claims)` (or skip the check when `narrative_text is None`), and combine with F-02's fix so a failed narrative still emits [verify] blockquotes; add a test with polish enabled and a raising narrative stub asserting the job finishes and doc.md exists.

## [MED] F-04 (= A-13) — Capture flow re-dumps paraphrased transcript lines at the top of the document as [verify] blockquotes even though the same lines are already placed under steps

Location: `src/pipeline/server.py:401` · checklist #2

```
server.py:401 `claims = _claims_for_narrative(transcript_path, synthetic_narration)` builds one claim per transcript line from the same file `_apply_transcript` (:801-819) already placed under steps. Coverage is exact substring: claim_coverage.py:23 `return stripped.lower() in text.lower()`; :69-71 appends every uncovered claim verbatim. Scope: every claim the narrative paraphrases is duplicated; if all are paraphrased the entire transcript is repeated. tests/pipeline/test_server.py:1034-1036 `assert md.count("[verify]") == 3` for 3 transcript lines. Peer A-13 confirms.
```

**Fix:** Treat step-level placements as coverage: run `ensure_claim_coverage` against `narrative_text + "\n" + "\n".join(r.get("narration","") for r in step_results)` (or build claims only from `synthetic_narration` when a transcript was placed), so only genuinely unplaced claims become [verify] blockquotes.

## [MED] F-05 (= A-06) — Single-block transcript still collapses onto step 1 unless an LLM rescue succeeds; with Ollama unreachable the bug is present in both flows

Location: `src/pipeline/transcript.py:216` · checklist #2

```
transcript.py:95 `if len(paragraphs) >= 2` fails for one paragraph; :99-100 yields one block; :124-125 `add(i, text)` places it on step 1; :216-223 appends only a WARNING to the note. Rescue is LLM-only: server.py:769-776 `semantic_align(content, manifest, step_contexts, narrative_llm)` ... `if aligned:` else :792 returns the original mapping. .audit/evidence/ollama_probe.txt:5 `TcpTestSucceeded False`. Peer A-06 confirms.
```

**Fix:** Add a deterministic last-resort in `_place_text_blocks`: when `len(blocks)==1` and `len(step_ids)>1`, hold the block as unplaced content with a visible review flag (peer's alternative, which avoids inventing correspondence) rather than attaching it to step 1; keep the WARNING in the report.

## [MED] F-06 (= A-14) — Step text uses qwen3:32b (text-only) with a manifest-fields-only prompt; use_vision attaches a base64 PNG per step with no model-capability guard, and config.py still documents the flag as inert

Location: `src/pipeline/generation.py:82` · checklist #3

```
config/models.toml:19-22 `[steps] ... model = "qwen3:32b"`. Prompt generation.py:57-63 built from manifest fields only. generation.py:82-88 `if use_vision and screenshot_dir is not None: ... {"type": "image_url", ...}`; wired at server.py:353 `use_vision=models_cfg.steps.use_vision`; server.py:1245 `"use_vision": form.get("steps_use_vision") == "on"` accepts any model; pages.py:471-472 'the default qwen3:32b can't see images'. Stale: config.py:82-84 'generate_step_text does not yet branch on this flag, so it currently has no runtime effect'. Peer A-14 confirms.
```

**Fix:** Add a `ModelsConfig` validator (or /ui/config save-time warning) when `steps.use_vision=True` and `steps.model` is a known text-only tag, with an explicit override for custom models; delete the stale sentence at config.py:82-84.

## [MED] F-07 (= A-07) — Every model tag is unverified; narrative default disagrees across CLAUDE.md/USER_MANUAL (qwen3:32b) vs config/pages/tests (qwen3.6:27b), and qwen3.6:27b has no pull/test record in the repo

Location: `config/models.toml:28` · checklist #4

```
.audit/evidence/ollama_probe.txt:6 'model tags CANNOT be verified live. Treat every tag as UNVERIFIED.' config/models.toml:22 `qwen3:32b`, :28 `qwen3.6:27b`, :35 `qwen2.5vl:7b`, :68 `gemma3:12b` (dated note :47-49, 2026-07-11, covers gemma3:12b only). CLAUDE.md:67-68 and USER_MANUAL.md:641 say narrative = `qwen3:32b`; pages.py:304,339 and tests/pipeline/test_models_config.py:24 say `qwen3.6:27b`. No dated record for `qwen3.6:27b` anywhere in the repo. Peer A-07 confirms.
```

**Fix:** Pick one narrative default and update CLAUDE.md:68, USER_MANUAL.md:641, config/models.toml:28 and pages.py:304/339 together; add a dated comment once `qwen3.6:27b` is actually pulled, or revert to `qwen3:32b`; add `scripts/verify_models.py` that GETs `/api/tags` and diffs against every tag in config.py/pages.py.

## [MED] F-09 — Photo build bypasses build_sidecar_report and hardcodes template_fallback_steps=[] even though failed captions set used_fallback=True

Location: `src/pipeline/server.py:578` · checklist #6

```
server.py:531 `"used_fallback": caption is None,` then :577-584 `report = {"template_fallback_steps": [], "verify_claims": [...], "empty_metadata_steps": [], "docx_warnings": []}`. sidecar.py:26 `[r["step_id"] for r in step_results if r["used_fallback"]]` is only reached from the capture path (server.py:426). Peer A-05 confirms and suggests a distinct category name for photo fallbacks.
```

**Fix:** Build the photo report via `build_sidecar_report(manifest, step_results, verify_claim_ids, claims_by_id)` (or add a `caption_fallback_steps` list derived from `used_fallback`); add a test with one caption returning None asserting that step id appears in the report.

## [MED] F-11 (= A-09) — Capture agent declares no DPI awareness; click, screenshot and UIA-rect coordinate frames depend on whichever third-party library initializes first

Location: `src/capture/recorder.py:141` · checklist #7

```
Grep `DPI|dpi|SetProcess|SetThread|shcore|GetDpiForWindow` over all *.py in the repo -> no matches; sopforge.spec:29 `runtime_hooks=[]`. recorder.py:141-153 `element, window = resolve_at(x, y)` / `self._shots.capture(x, y)` / `screen_x, screen_y = x - origin_x, y - origin_y` and the same subtraction on `bounding_rect` -- pixel offsets only, no scale factor; pywinauto is imported lazily at uia.py:71 on the first click, after the pynput hook is installed. Whether pywinauto changes the process DPI context on import could not be checked (shell denied), so the 'frame depends on import order' part remains an unverified risk, as peer A-09 notes. tests/test_recorder.py:152-156 cover 100%-scale, non-negative origins only.
```

**Fix:** At the top of `capture/__main__.py` call `ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)` (PER_MONITOR_AWARE_V2) with `shcore.SetProcessDpiAwareness(2)` fallback so hooks, mss and pywinauto share one physical-pixel frame; record `screen.scale` in the manifest; add negative-origin and mixed-DPI cases to test_recorder.py.

## [LOW] F-08 (= A-08) — Within one job the pipeline alternates steps and narrative models up to five times with no keep_alive control; residency/eviction behaviour on the 40 GB host is unmeasured

Location: `src/pipeline/server.py:346` · checklist #5

```
jobs.py:60-66 single worker. Stage order in `_generate`: steps model :344-354, narrative model :769 and :778 (when placement rescue runs), steps model again :380 (title), narrative again :405, polish model :657 (opt-in; models.toml:65 `enabled = false`). No request sets `keep_alive` (grep over *.py -> none); llm_client.py:80 `payload = {"model": ..., "messages": messages, **kwargs}`; :104-105 `close()` only closes HTTP. Co-residency/eviction NOT measured -- host down (ollama_probe.txt). Peer A-08 confirms.
```

**Fix:** Reorder `_generate` so all [steps]-model work (steps + title) precedes [narrative]-model work (5 switches -> 2); optionally pass `keep_alive` via `LLMClient.chat(**kwargs)` (already forwarded at llm_client.py:80); measure peak VRAM once the host is back.

## [LOW] F-13 (= A-10) — Dependency drift: pyproject declares no dependencies; requirements pins build-only pyinstaller, test-only numpy, and faster-whisper for unreachable code

Location: `requirements.txt:7` · checklist #8

```
pyproject.toml:5-8 `[project]` has only `name`, `version = "1.6.1"`, `requires-python`. requirements.txt:7 `numpy==2.5.0`, :9 `pyinstaller==6.21.0`, :17 `faster-whisper==1.2.1`. Grep `import numpy|from numpy|Transcriber` over *.py: src -> only `transcription.py:7:class Transcriber:` (no production caller); tests -> `tests/test_redaction.py:6:import numpy as np`, `tests/pipeline/test_transcription.py:17`. sopforge.spec:44 and sopforge-server.spec:82 exclude `"numpy"`; sopforge-server.spec:90 excludes `"faster_whisper"` -- so neither ships in the EXEs. Peer A-10's correction accepted: numpy is test-used, not wholly unused.
```

**Fix:** Add `dependencies = [...]` to pyproject.toml mirroring the runtime subset of requirements.txt; move `pyinstaller` and `numpy` to a requirements-dev.txt (numpy stays for tests/test_redaction.py); delete transcription.py + faster-whisper or wire it with `local_files_only=True`.

## [LOW] F-14 (= A-11) — Public repo commits internal LAN host IP as code defaults, personal Windows user paths, a private repo name, and real-capture session metadata

Location: `src/pipeline/config.py:65` · checklist #8

```
.audit/evidence/repo_visibility.txt:3 `{"isPrivate":false,"visibility":"PUBLIC"}`. config.py:65 and :153 `"http://192.168.200.60:11434/v1"`; config/models.toml:21,27,34,67; CLAUDE.md:31; USER_MANUAL.md:192,667. docx_assembler.py:21 `Path(r"C:\Users\Brian\Documents\SOP_Factory_2\template")`; src/capture/selftest.py:34 `C:\Users\Brian\AppData\Local\Programs\Microsoft VS Code\Code.exe`; README.md:58-59 and USER_MANUAL.md:187-189 `soakal/SOP-Factory`. scripts/vision_measurements/vision_step_measurement_20260711T220712Z.json:4 endpoint, :5-8 session ids, :103 real window title (tracked per `git ls-files`, confirmed by peer A-11). No credentials found.
```

**Fix:** Change code defaults to `http://127.0.0.1:11434/v1` (config.py:65,153) and `Path.home() / "Documents" / "SOP_Factory_2" / "template"` (docx_assembler.py:21); derive the VS Code path from `%LOCALAPPDATA%` in selftest.py; keep the LAN address only in the untracked per-user `~/SOPForge/models.toml`; redact or untrack the measurement JSON.

## [LOW] F-15 (= A-15) — Stale code and docs: generate_polish_pass has no production caller, key_status ignores the polish provider, and create_app's docstring says the narrative factory is placement-only

Location: `src/pipeline/config.py:200` · checklist #8

```
config.py:200 `used = {cfg.steps.provider, cfg.narrative.provider, cfg.vision.provider}` omits `cfg.polish.provider`, so a polish section on openrouter/openai/anthropic never shows a key indicator. Grep `generate_polish_pass` over *.py: definition polish.py:277, no caller in src; callers are scripts/proof_polish_live.py:66,233 and tests/pipeline/test_polish.py:21,276-339 (peer A-15's correction accepted -- not globally dead). server.py:227-230 says `[narrative]` is 'used only for the semantic transcript-placement/polish path', contradicting :405-411 `make_narrative_llm_client()` -> `generate_narrative`.
```

**Fix:** Add `cfg.polish.provider` to `used` in `key_status`; migrate scripts/proof_polish_live.py and test_polish.py to `generate_polish_fields` then delete `generate_polish_pass`/`_gate`; rewrite the `create_app` docstring paragraph at server.py:227-230.

