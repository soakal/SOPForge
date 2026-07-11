"""polish.py: the optional 4th-stage formatting/tone pass over an
assembled document. A rewrite is only trusted if a mechanical gate confirms
it didn't invent or drop a fact; any failure at all -- an LLM exception, a
gate rejection, a degenerate/empty reply -- must return the ORIGINAL
document text byte-identical. generate_polish_pass must never raise.

generate_polish_fields is the field-level sibling entry point: one
JSON-array call covering narrative_text and each step's "text"/present
"narration" as separate items, each independently gated by _field_gate (the
same checks _gate delegates to) and independently falling back to its own
original text -- a bad rewrite of one field must never discard a good
rewrite of another, and must never raise."""

import json

from pipeline.polish import (
    _build_field_items,
    _field_gate,
    _gate,
    generate_polish_fields,
    generate_polish_pass,
)

# A realistic ~1.5KB, 19-step SOP document -- well over the old 600-char
# _LENGTH_CAP (built for single captions/narration segments), while the
# 0.4-1.8 length-RATIO band this module's own gate uses still leaves room
# for punctuation fixes. Regression fixture for the bug where _gate rejected
# every real-sized document via textgate.degenerate_reason's absolute length
# cap, making the whole-document polish pass a permanent no-op.
_REALISTIC_STEPS = [
    "open file explorer from the taskbar and wait for the window to appear",
    "navigate to the c:\\users\\demo\\documents\\sopforge folder in the left pane",
    "double click the captures folder to open it and confirm 19 files are listed",
    "right click session_042.json and select open with notepad",
    "verify the manifest field step_count equals 19 before continuing",
    "open a new browser tab and go to http://192.168.200.60:11434 to confirm ollama is running",
    "click the settings gear icon in the top right corner of the sopforge tray app",
    "select the models tab and confirm qwen3:32b is listed as the steps provider",
    "click save to store the configuration change to disk",
    "close the settings window and return to the main tray menu",
    "start a new capture session by clicking the record button",
    "perform the five clicks that make up the demo workflow in order",
    "stop the recording and wait for the upload progress bar to reach 100 percent",
    "open the review ui at http://localhost:8420/ui/review to inspect the draft",
    "confirm each of the 19 steps has a screenshot attached and no step is blank",
    "click generate document to produce the docx and pdf exports",
    "open the exported report.docx file and scroll through all 19 steps",
    "check that port 8420 is referenced correctly in step 6 of the document",
    "click approve to mark the sop as final and archive the session folder",
]
_REALISTIC_DOCUMENT = "\n\n".join(
    f"Step {i}: {text}" for i, text in enumerate(_REALISTIC_STEPS, start=1)
)


def _faithful_rewrite(document):
    """A purely faithful, non-inventive rephrase: capitalizes each line and
    ensures terminal punctuation, exactly the kind of harmless formatting
    pass `generate_polish_pass` is meant to accept -- no words added or
    removed, no facts dropped or altered."""
    lines = []
    for line in document.split("\n"):
        if not line.strip():
            lines.append(line)
            continue
        fixed = line[0].upper() + line[1:]
        if not fixed.rstrip().endswith((".", ":", "!", "?")):
            fixed = fixed.rstrip() + "."
        lines.append(fixed)
    return "\n".join(lines)


class _StubClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.reply


class _RaisingClient:
    def chat(self, messages, **kwargs):
        raise RuntimeError("simulated outage")


class _NoneReplyClient:
    def chat(self, messages, **kwargs):
        return None


def test_gate_accepts_a_faithful_formatting_pass():
    original = "click save then go to the c drive and open port 8420"
    rewrite = "Click Save, then go to the C drive and open port 8420."
    ok, reason = _gate(original, rewrite)
    assert ok, reason


def test_gate_rejects_dropped_numeric_fact():
    original = "open the c drive and go to port 8420 for the server"
    rewrite = "Open the C drive and go to the server's port."
    ok, reason = _gate(original, rewrite)
    assert not ok
    assert reason == "dropped or altered a literal fact"


def test_gate_rejects_invented_content():
    # Tests the count-based mechanism specifically: enough novel words
    # (4 of 8, 50%, of the rewrite's distinct content tokens) to fail on
    # cluster size alone, using non-denylisted vocabulary so this stays
    # isolated from test_gate_rejects_a_denylisted_word_smuggled_in_via_
    # reused_vocabulary below, which tests the separate content-aware check.
    original = "click save to store the file"
    rewrite = "Click save to store the file, then synchronize the archived telemetry cache."
    ok, reason = _gate(original, rewrite)
    assert not ok
    assert reason == "introduced unsupported content"


def test_gate_accepts_benign_paraphrase_with_a_few_new_words():
    # A realistic LLM paraphrase of a realistic-sized document: no facts
    # dropped, but a couple of incidental new words ("carefully",
    # "successfully") that weren't in the original -- 2 of 134 (~1.5%) of
    # the rewrite's distinct content tokens. This must be tolerated (it's
    # the kind of benign rephrase the polish prompt explicitly invites),
    # unlike test_gate_rejects_invented_content's clustered fabrication
    # (4 of 8, i.e. 50%, of the rewrite's distinct content tokens), which
    # must keep failing.
    rewrite = _faithful_rewrite(_REALISTIC_DOCUMENT)
    rewrite = rewrite.replace("verify the manifest", "carefully verify the manifest").replace(
        "click approve to mark", "successfully click approve to mark"
    )
    assert rewrite != _faithful_rewrite(_REALISTIC_DOCUMENT)  # sanity: words were added
    ok, reason = _gate(_REALISTIC_DOCUMENT, rewrite)
    assert ok, reason


def test_gate_rejects_a_fabricated_extra_step():
    # Attack scenario: a rewrite that is a faithful rephrase of every
    # existing step (no facts dropped or altered) but has an entire extra
    # SOP step appended -- plausible-sounding, actionable, and completely
    # invented; nothing in _REALISTIC_DOCUMENT told anyone to touch a
    # network switch. On this ~132-content-token document this adds only
    # 12 novel content tokens, ~8% of the rewrite's total -- comfortably
    # under _MAX_NOVEL_CONTENT_FRACTION (30%) on its own. Before the
    # absolute cap (_MAX_NOVEL_CONTENT_ABSOLUTE) was added, this attack was
    # ACCEPTED by the gate: a fractional-only rule gives larger documents a
    # proportionally larger budget for fabricated content, which is exactly
    # backwards -- the amount of invented, actionable content a reader
    # could act on must not scale with document size. This is the CLAUDE.md
    # violation the old exact-membership rule prevented and the fraction-
    # only replacement newly permitted. Uses non-denylisted vocabulary
    # (a fabricated sensor-recalibration step rather than a destructive one)
    # so this test stays isolated from
    # test_gate_rejects_a_denylisted_word_smuggled_in_via_reused_vocabulary
    # below, which tests the separate content-aware denylist check.
    fabricated_step = (
        "Step 20: Recalibrate the auxiliary telemetry sensor mounted in the "
        "equipment closet and confirm all diagnostic indicators read nominal."
    )
    rewrite = _faithful_rewrite(_REALISTIC_DOCUMENT) + "\n\n" + fabricated_step
    ok, reason = _gate(_REALISTIC_DOCUMENT, rewrite)
    assert not ok
    assert reason == "introduced unsupported content"


def test_gate_rejects_a_denylisted_word_smuggled_in_via_reused_vocabulary():
    # Attack scenario (the bypass the count-based checks alone cannot catch):
    # append a fabricated destructive step built almost entirely out of the
    # document's OWN vocabulary -- stopwords plus structural words like
    # "step"/"session" that are already content tokens elsewhere in
    # _REALISTIC_DOCUMENT (e.g. "session_042.json", "capture session",
    # "session folder") -- so only 4 words are genuinely novel: 20, complete,
    # drive, format. That's 2.9% of the rewrite's 136 distinct content
    # tokens: well under _MAX_NOVEL_CONTENT_FRACTION (30%) AND well under
    # _MAX_NOVEL_CONTENT_ABSOLUTE (8). Before the denylist check was added,
    # this fully fabricated, destructive instruction (format a drive) was
    # ACCEPTED by the gate -- no absolute-cap or fraction-cap value can
    # separate this attack from a legitimate few-new-words paraphrase,
    # because a bare novel-word-count heuristic can't see what the novel
    # words mean. Only a content-aware check on the novel words themselves
    # (_DENYLIST_WORDS) closes this.
    fabricated_step = "Step 20: Format the c drive to complete the session."
    rewrite = _faithful_rewrite(_REALISTIC_DOCUMENT) + "\n\n" + fabricated_step
    ok, reason = _gate(_REALISTIC_DOCUMENT, rewrite)
    assert not ok
    assert reason == "introduced a high-risk or destructive instruction"


def test_gate_rejects_an_inflected_denylisted_word_reformatting():
    # Stemming-evasion regression: "reformatting" is a morphological variant
    # of the already-denylisted "reformat"/"format" family, built almost
    # entirely out of the document's own vocabulary (same shape as
    # test_gate_rejects_a_denylisted_word_smuggled_in_via_reused_vocabulary
    # above). Before stemming was added to _denylisted_word, the exact-match
    # check let this straight through -- "reformatting" is not a member of
    # _DENYLIST_WORDS even though "format"/"reformat" are.
    fabricated_step = "Step 20: Reformatting the c drive to complete the session."
    rewrite = _faithful_rewrite(_REALISTIC_DOCUMENT) + "\n\n" + fabricated_step
    ok, reason = _gate(_REALISTIC_DOCUMENT, rewrite)
    assert not ok
    assert reason == "introduced a high-risk or destructive instruction"


def test_gate_rejects_an_inflected_denylisted_word_deletes():
    fabricated_step = "Step 20: This deletes all files in the session folder."
    rewrite = _faithful_rewrite(_REALISTIC_DOCUMENT) + "\n\n" + fabricated_step
    ok, reason = _gate(_REALISTIC_DOCUMENT, rewrite)
    assert not ok
    assert reason == "introduced a high-risk or destructive instruction"


def test_gate_rejects_an_inflected_denylisted_word_wiping():
    fabricated_step = "Step 20: Finish by wiping the disk before archiving the session."
    rewrite = _faithful_rewrite(_REALISTIC_DOCUMENT) + "\n\n" + fabricated_step
    ok, reason = _gate(_REALISTIC_DOCUMENT, rewrite)
    assert not ok
    assert reason == "introduced a high-risk or destructive instruction"


def test_gate_accepts_a_benign_novel_word_that_shares_a_prefix_with_a_denylisted_word():
    # False-positive guard for the stemming fix: "dropdown" shares a prefix
    # with the denylisted word "drop" but is a completely unrelated, benign
    # UI term. _stem must produce an EQUALITY comparison, not a
    # substring/prefix match -- "dropdown" must never collapse onto "drop".
    original = "click the settings icon to continue"
    rewrite = "Click the settings dropdown to continue."
    ok, reason = _gate(original, rewrite)
    assert ok, reason


def test_stem_does_not_collapse_unrelated_words_onto_denylisted_stems():
    # Direct unit check on the stemmer itself (not just through the gate):
    # words that merely share a prefix with a denylisted word must stem to
    # something different from that denylisted word's stem.
    from pipeline.polish import _DENYLIST_STEMS, _stem

    for benign, denylisted in [
        ("dropdown", "drop"),
        ("skillet", "kill"),
        ("terminal", "terminate"),
    ]:
        assert _stem(benign) not in _DENYLIST_STEMS, (benign, _stem(benign))
        assert _stem(benign) != _stem(denylisted), (benign, denylisted)


def test_gate_rejects_wildly_short_rewrite():
    original = (
        "open file explorer then open the c drive then goto users then select "
        "vrsi then goto sopforge then captures folder"
    )
    rewrite = "Open Explorer."
    ok, reason = _gate(original, rewrite)
    assert not ok
    assert reason == "length ratio out of bounds"


def test_gate_rejects_degenerate_repetition():
    original = "open the settings menu then confirm the change"
    rewrite = "settings settings settings settings settings settings settings"
    ok, reason = _gate(original, rewrite)
    assert not ok
    assert reason is not None


def test_gate_rejects_empty_rewrite():
    ok, reason = _gate("click save", "   ")
    assert not ok
    assert reason == "empty rewrite"


def test_generate_polish_pass_returns_polished_text_on_success():
    document = "click save to store the file"
    client = _StubClient("Click Save to store the file.")
    result = generate_polish_pass(document, client)
    assert result == "Click Save to store the file."
    assert len(client.calls) == 1


def test_generate_polish_pass_keeps_original_on_llm_exception():
    document = "click save to store the file"
    result = generate_polish_pass(document, _RaisingClient())
    assert result == document


def test_generate_polish_pass_keeps_original_on_invented_content():
    document = "click save to store the file"
    client = _StubClient("Click save, then restart the print spooler service.")
    result = generate_polish_pass(document, client)
    assert result == document


def test_generate_polish_pass_keeps_original_on_degenerate_reply():
    document = "open the settings menu then confirm the change"
    client = _StubClient("settings settings settings settings settings settings settings")
    result = generate_polish_pass(document, client)
    assert result == document


def test_generate_polish_pass_keeps_original_on_none_reply():
    document = "click save to store the file"
    result = generate_polish_pass(document, _NoneReplyClient())
    assert result == document


def test_generate_polish_pass_is_a_noop_on_empty_document():
    result = generate_polish_pass("", _RaisingClient())
    assert result == ""


def test_generate_polish_pass_is_a_noop_on_whitespace_only_document():
    result = generate_polish_pass("   \n  ", _RaisingClient())
    assert result == "   \n  "


def test_realistic_document_is_well_over_the_old_narration_length_cap():
    # Sanity check on the fixture itself: this must be well past the 600-char
    # cap that used to be applied (via degenerate_reason) to whole documents,
    # or this regression test wouldn't actually exercise the bug.
    assert len(_REALISTIC_DOCUMENT) > 1000


def test_gate_accepts_a_faithful_rewrite_of_a_realistic_multistep_document():
    rewrite = _faithful_rewrite(_REALISTIC_DOCUMENT)
    ok, reason = _gate(_REALISTIC_DOCUMENT, rewrite)
    assert ok, reason


def test_generate_polish_pass_returns_polished_text_for_a_realistic_multistep_document():
    # Regression test: a real, ~2KB, 19-step document with a purely faithful,
    # non-inventive rewrite must come back from generate_polish_pass as the
    # POLISHED text -- previously _gate rejected it via degenerate_reason's
    # hardcoded 600-char length cap (sized for single captions/narration
    # segments, not whole documents), silently discarding every legitimate
    # rewrite of any real-sized document.
    rewrite = _faithful_rewrite(_REALISTIC_DOCUMENT)
    client = _StubClient(rewrite)
    result = generate_polish_pass(_REALISTIC_DOCUMENT, client)
    assert result == rewrite
    assert result != _REALISTIC_DOCUMENT
    assert len(client.calls) == 1


# --- generate_polish_fields / _field_gate ----------------------------------


def test_field_gate_is_the_same_check_gate_delegates_to():
    # _gate is now a thin whole-document alias of _field_gate (factored out
    # so generate_polish_pass and generate_polish_fields share one
    # implementation) -- both must agree on the same original/rewrite pair.
    original = "click save then go to the c drive and open port 8420"
    rewrite = "Click Save, then go to the C drive and open port 8420."
    assert _field_gate(original, rewrite) == _gate(original, rewrite)


def test_build_field_items_includes_narration_only_when_present_and_truthy():
    step_results = [
        {"step_id": "step-001", "text": "a", "narration": "b"},
        {"step_id": "step-002", "text": "c", "narration": ""},  # falsy -- excluded
        {"step_id": "step-003", "text": "d"},  # absent key -- excluded
    ]
    items = _build_field_items("narrative text", step_results)
    assert [field_id for field_id, _ in items] == [
        "narrative",
        "step-001",
        "step-001:narration",
        "step-002",
        "step-003",
    ]


def test_build_field_items_skips_empty_or_missing_narrative_text():
    items_none = _build_field_items(None, [])
    items_blank = _build_field_items("   ", [])
    assert items_none == []
    assert items_blank == []


def test_generate_polish_fields_polishes_narrative_step_text_and_narration_together():
    narrative_text = "Save the file to the reports folder."
    step_results = [
        {
            "step_id": "step-001",
            "text": "click save then store the file",
            "narration": "click save then store the file",
        }
    ]
    reply = json.dumps(
        [
            {"field_id": "narrative", "text": "Save the file to the reports folder promptly."},
            {"field_id": "step-001", "text": "Click save, then store the file."},
            {
                "field_id": "step-001:narration",
                "text": "Click save, then store the file carefully.",
            },
        ]
    )
    client = _StubClient(reply)
    polished_narrative, polished_steps, meta = generate_polish_fields(
        narrative_text, step_results, client
    )

    assert polished_narrative == "Save the file to the reports folder promptly."
    assert polished_steps[0]["text"] == "Click save, then store the file."
    assert polished_steps[0]["narration"] == "Click save, then store the file carefully."
    assert set(meta["fields_polished"]) == {"narrative", "step-001", "step-001:narration"}
    assert meta["fields_kept_verbatim"] == {}
    assert len(client.calls) == 1


def test_generate_polish_fields_leaves_step_without_narration_untouched():
    step_results = [{"step_id": "step-001", "text": "click save"}]
    reply = json.dumps(
        [
            {"field_id": "step-001", "text": "Click save."},
            # A field the model was never asked for -- must be ignored, not
            # applied, since this step never had a "narration" key.
            {"field_id": "step-001:narration", "text": "This should be ignored."},
        ]
    )
    client = _StubClient(reply)
    polished_narrative, polished_steps, meta = generate_polish_fields(None, step_results, client)

    sent_prompt = client.calls[0][0]["content"]
    assert "step-001:narration" not in sent_prompt
    assert polished_narrative is None
    assert polished_steps[0]["text"] == "Click save."
    assert "narration" not in polished_steps[0]
    assert "step-001:narration" not in meta["fields_polished"]
    assert "step-001:narration" not in meta["fields_kept_verbatim"]


def test_generate_polish_fields_rejects_a_denylisted_word_in_one_field_but_keeps_others():
    # Field-granularity version of test_gate_rejects_a_denylisted_word_
    # smuggled_in_via_reused_vocabulary: a rewrite of ONE field (the step
    # text) smuggles in a destructive instruction; that field alone must
    # revert to its original while a good rewrite of the OTHER field
    # (narrative_text) in the same call is still kept.
    narrative_text = "Save the file to the reports folder using the workstation."
    step_results = [{"step_id": "step-001", "text": "click save then store the file"}]
    good_narrative_rewrite = (
        "Save the file to the reports folder using the workstation, ensuring accuracy."
    )
    bad_step_rewrite = "Click save, then format the c drive to store the file."
    reply = json.dumps(
        [
            {"field_id": "narrative", "text": good_narrative_rewrite},
            {"field_id": "step-001", "text": bad_step_rewrite},
        ]
    )
    client = _StubClient(reply)
    polished_narrative, polished_steps, meta = generate_polish_fields(
        narrative_text, step_results, client
    )

    assert polished_narrative == good_narrative_rewrite
    assert polished_steps[0]["text"] == step_results[0]["text"]  # reverted, not bad_step_rewrite
    assert (
        meta["fields_kept_verbatim"]["step-001"]
        == "introduced a high-risk or destructive instruction"
    )
    assert "step-001" not in meta["fields_polished"]
    assert "narrative" in meta["fields_polished"]


def test_generate_polish_fields_rejects_a_fabricated_cluster_in_one_field_but_keeps_others():
    # Field-granularity version of test_gate_rejects_a_fabricated_extra_step:
    # a rewrite of ONE field (a step's text, using the realistic multi-step
    # fixture) appends a plausible-sounding but fully invented extra step;
    # that field alone must revert while a good rewrite of narrative_text in
    # the same call is still kept.
    narrative_text = "This document walks through configuring the demo workstation."
    step_results = [{"step_id": "step-001", "text": _REALISTIC_DOCUMENT}]
    fabricated_step = (
        "Step 20: Recalibrate the auxiliary telemetry sensor mounted in the "
        "equipment closet and confirm all diagnostic indicators read nominal."
    )
    bad_step_rewrite = _faithful_rewrite(_REALISTIC_DOCUMENT) + "\n\n" + fabricated_step
    good_narrative_rewrite = (
        "This document walks through configuring the demo workstation carefully."
    )
    reply = json.dumps(
        [
            {"field_id": "narrative", "text": good_narrative_rewrite},
            {"field_id": "step-001", "text": bad_step_rewrite},
        ]
    )
    client = _StubClient(reply)
    polished_narrative, polished_steps, meta = generate_polish_fields(
        narrative_text, step_results, client
    )

    assert polished_narrative == good_narrative_rewrite
    assert polished_steps[0]["text"] == _REALISTIC_DOCUMENT  # reverted, not bad_step_rewrite
    assert meta["fields_kept_verbatim"]["step-001"] == "introduced unsupported content"
    assert "step-001" not in meta["fields_polished"]
    assert "narrative" in meta["fields_polished"]


def test_generate_polish_fields_reverts_only_the_rejected_narration_field():
    # A step's "narration" is a separate item/id from that same step's
    # "text" -- a gate rejection on the narration rewrite must revert only
    # that step's narration, leaving that step's text polish, and every
    # other field, untouched.
    narrative_text = "Save the report to the shared drive."
    step_results = [
        {
            "step_id": "step-001",
            "text": "click save then store the file",
            "narration": "click save then store the file",
        },
        {"step_id": "step-002", "text": "click cancel"},
    ]
    good_narrative_rewrite = "Save the report to the shared drive promptly."
    good_text_rewrite = "Click save, then store the file."
    bad_narration_rewrite = "Click save, then format the c drive to store the file."
    reply = json.dumps(
        [
            {"field_id": "narrative", "text": good_narrative_rewrite},
            {"field_id": "step-001", "text": good_text_rewrite},
            {"field_id": "step-001:narration", "text": bad_narration_rewrite},
            {"field_id": "step-002", "text": "Click cancel."},
        ]
    )
    client = _StubClient(reply)
    polished_narrative, polished_steps, meta = generate_polish_fields(
        narrative_text, step_results, client
    )

    assert polished_narrative == good_narrative_rewrite
    step1 = next(s for s in polished_steps if s["step_id"] == "step-001")
    step2 = next(s for s in polished_steps if s["step_id"] == "step-002")
    assert step1["text"] == good_text_rewrite  # text polish kept
    assert step1["narration"] == step_results[0]["narration"]  # narration reverted
    assert step2["text"] == "Click cancel."
    assert (
        meta["fields_kept_verbatim"]["step-001:narration"]
        == "introduced a high-risk or destructive instruction"
    )
    assert "step-001" in meta["fields_polished"]
    assert "step-001:narration" not in meta["fields_polished"]


def test_generate_polish_fields_keeps_original_for_field_omitted_from_reply():
    narrative_text = "Save the file."
    step_results = [
        {"step_id": "step-001", "text": "click save"},
        {"step_id": "step-002", "text": "click cancel"},
    ]
    reply = json.dumps(
        [
            {"field_id": "narrative", "text": "Save the file."},
            {"field_id": "step-001", "text": "Click save."},
            # step-002 omitted entirely from the reply
        ]
    )
    client = _StubClient(reply)
    _polished_narrative, polished_steps, meta = generate_polish_fields(
        narrative_text, step_results, client
    )

    step2 = next(s for s in polished_steps if s["step_id"] == "step-002")
    assert step2["text"] == "click cancel"
    assert meta["fields_kept_verbatim"]["step-002"] == "not returned by model"
    assert "step-002" not in meta["fields_polished"]


def test_generate_polish_fields_keeps_everything_on_llm_exception():
    narrative_text = "Save the file."
    step_results = [
        {"step_id": "step-001", "text": "click save", "narration": "click save"},
    ]
    polished_narrative, polished_steps, meta = generate_polish_fields(
        narrative_text, step_results, _RaisingClient()
    )
    assert polished_narrative == narrative_text
    assert polished_steps == step_results
    assert meta["attempted"] is True
    assert meta["fields_kept_verbatim"] == {
        "narrative": "polish call failed",
        "step-001": "polish call failed",
        "step-001:narration": "polish call failed",
    }


def test_generate_polish_fields_keeps_everything_on_malformed_json_reply():
    narrative_text = "Save the file."
    step_results = [{"step_id": "step-001", "text": "click save"}]
    client = _StubClient("not json")
    polished_narrative, polished_steps, meta = generate_polish_fields(
        narrative_text, step_results, client
    )
    assert polished_narrative == narrative_text
    assert polished_steps == step_results
    assert meta["fields_kept_verbatim"] == {
        "narrative": "polish call failed",
        "step-001": "polish call failed",
    }


def test_generate_polish_fields_keeps_everything_on_none_reply():
    narrative_text = "Save the file."
    step_results = [{"step_id": "step-001", "text": "click save"}]
    polished_narrative, polished_steps, meta = generate_polish_fields(
        narrative_text, step_results, _NoneReplyClient()
    )
    assert polished_narrative == narrative_text
    assert polished_steps == step_results
    assert meta["fields_kept_verbatim"]["narrative"] == "polish call failed"


def test_generate_polish_fields_is_a_noop_when_nothing_to_polish():
    polished_narrative, polished_steps, meta = generate_polish_fields(None, [], _RaisingClient())
    assert polished_narrative is None
    assert polished_steps == []
    assert meta["attempted"] is False
