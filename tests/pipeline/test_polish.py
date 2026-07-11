"""polish.py: the optional 4th-stage formatting/tone pass over an
assembled document. A rewrite is only trusted if a mechanical gate confirms
it didn't invent or drop a fact; any failure at all -- an LLM exception, a
gate rejection, a degenerate/empty reply -- must return the ORIGINAL
document text byte-identical. generate_polish_pass must never raise."""

from pipeline.polish import _gate, generate_polish_pass

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
    original = "click save to store the file"
    rewrite = "Click save to store the file, then restart the print spooler service."
    ok, reason = _gate(original, rewrite)
    assert not ok
    assert reason == "introduced unsupported content"


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
