"""Live proof that the polish stage's local backend (whatever model/host is
configured under [polish] in config/models.toml -- printed at runtime below,
not hardcoded here) genuinely rewrites a document -- not a stub, not a
template fallback. Loads [polish] via load_models_config, builds a real
LLMClient(cfg.polish), and:

  1. Confirms the Ollama host is reachable (GET /api/tags) before anything
     else, so an unreachable host fails with a clear message instead of a
     generic connection traceback.
  2. Calls llm.chat() directly on a realistic 19-step SOP document and runs
     the raw reply through polish._gate() -- this is the actual mechanical
     acceptance check generate_polish_pass() uses internally.
  3. Calls generate_polish_pass() on the same document and asserts it
     returns exactly that gate-accepted reply.

Success requires ALL of:
  - _gate(original, reply) == (True, None)
  - _normalize(reply) != _normalize(original) (a genuine rewrite happened --
    not a no-op echo, and not a whitespace-only no-op that raw string
    equality would miss, e.g. a few appended blank lines with zero actual
    content change; _normalize is the same whitespace-collapsing/lowercasing
    helper _gate itself uses to decide what counts as "the same content")
  - generate_polish_pass(original, llm) == reply (the pass used the
    gate-accepted rewrite, not some other value)
  - generate_polish_pass(original, llm) != original (the pass didn't fall
    back to the input -- fallback here is FAILURE of this proof, not a
    tolerated outcome, even though generate_polish_pass()'s own contract
    treats fallback as safe)

If the gate legitimately rejects the live reply, OR the reply passes the
gate but is a content-free echo (identical to the original once
whitespace/case-normalized -- the gate alone can't catch this, since an
unchanged document trivially has no dropped/invented facts and a length
ratio of ~1.0), this is reported as a FAILED proof (not silently
downgraded to "ok, fallback used") with the reason and both texts printed
for inspection. One retry is attempted before giving up, per the plan's
risk note -- never more than once, and _gate's/_normalize's logic is never
touched. Both failure modes (gate rejection and normalize-identical echo)
route through that same single retry; a whitespace-only echo that passes
_gate trivially must not slip past the retry mechanism just because it
failed a different check than _gate's.

The retry runs at a substantially *higher* temperature (0.85, plus
top_p=0.95) than the first attempt (the section's configured default,
unset here), not lower. Live runs against the configured [polish] model at
low temperature were observed to reproduce byte-identical echo output across independent
process runs -- consistent with near-greedy decoding collapsing onto a
single high-probability "return the input" completion for this prompt.
Escaping that requires *more* sampling entropy, not less, so the retry
deliberately raises temperature/top_p rather than lowering them.

Usage: python scripts/proof_polish_live.py
Exit code 0 = proof succeeded. Exit code 1 = proof failed (reported loudly).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import httpx  # noqa: E402

from pipeline.config import load_models_config  # noqa: E402
from pipeline.llm_client import LLMClient  # noqa: E402
from pipeline.polish import _gate, _normalize, generate_polish_pass  # noqa: E402


class _CapturingClient:
    """Wraps a real LLMClient so generate_polish_pass() is driven through
    exactly one live chat() call, whose raw reply is recorded here for a
    separate _gate() check afterwards. Deliberately does NOT make a second,
    independent llm.chat() call to compare against -- a real model samples
    stochastically, so two independent calls would almost never produce
    byte-identical text even when both are valid, gate-accepted rewrites;
    comparing against a second call would make the equality check
    meaningless. `extra_kwargs` lets the one retry (see main()/_attempt(),
    which raises temperature/top_p to escape a suspected near-greedy echo
    collapse) inject sampling kwargs without generate_polish_pass's fixed
    two-argument signature needing to expose any.

    If the wrapped chat() call raises, that exception propagates unchanged
    (generate_polish_pass swallows it and falls back) and `last_reply` stays
    None -- main() uses that None to tell "the gate rejected a real reply"
    apart from "the call itself failed/threw"."""

    def __init__(self, inner, extra_kwargs=None):
        self._inner = inner
        self._extra_kwargs = extra_kwargs or {}
        self.last_reply = None

    def chat(self, messages, **kwargs):
        reply = self._inner.chat(messages, **{**self._extra_kwargs, **kwargs})
        self.last_reply = reply
        return reply


# A realistic 19-step SOP document -- the kind of whole-assembled-document
# text generate_polish_pass() actually runs on in production. Deliberately
# includes literal facts (numbers, a file path, a drive letter) that _gate's
# check 1 requires to survive verbatim in any accepted rewrite.
DOCUMENT_TEXT = """Standard Operating Procedure: Weekly Sales Report Export

Step 1. Open the Finance Reporting Tool from the Start menu.
Step 2. Click the "Reports" tab in the top navigation bar.
Step 3. Select "Weekly Sales Summary" from the report list.
Step 4. Set the date range to the last 7 days using the calendar picker.
Step 5. Click the "Generate" button and wait for the report to render.
Step 6. Review the totals row at the bottom of the report for accuracy.
Step 7. Click "Export" and choose the CSV format from the dropdown.
Step 8. Save the file as sales_report_week32.csv in C:\\Reports\\Weekly.
Step 9. Open File Explorer and navigate to the D:\\Shared\\Finance folder.
Step 10. Copy the exported CSV file into the D:\\Shared\\Finance folder.
Step 11. Open Outlook and start a new email addressed to finance-team@example.com.
Step 12. Attach the sales_report_week32.csv file to the email.
Step 13. In the subject line, type "Weekly Sales Report - Week 32".
Step 14. In the body, note that 214 transactions were processed this week.
Step 15. Click "Send" to deliver the report to the finance team.
Step 16. Open the shared tracking spreadsheet at \\\\fileserver\\tracking\\log.xlsx.
Step 17. Add a new row with today's date and the number 214 in the count column.
Step 18. Save the spreadsheet and close it.
Step 19. Log the export as complete in the ticketing system, ticket #4471.
"""


def check_ollama_reachable(base_url, timeout=5.0):
    """GET /api/tags against the Ollama host root (not the /v1 OpenAI-compat
    path LLMClient itself uses). Returns the parsed tag list on success,
    raises SystemExit with a clear message on any failure."""
    root = base_url.split("/v1")[0].rstrip("/")
    url = f"{root}/api/tags"
    try:
        resp = httpx.get(url, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - report and abort, not a silent pass
        print(f"FAIL: Ollama host unreachable at {url}: {exc}")
        sys.exit(1)
    return resp.json()


def main():
    cfg = load_models_config()
    polish_cfg = cfg.polish
    print(f"=== proof_polish_live: local polish backend against {polish_cfg.model!r} ===")
    print(
        f"[polish] config: provider={polish_cfg.provider!r} "
        f"endpoint={polish_cfg.endpoint!r} model={polish_cfg.model!r}"
    )

    print(f"Checking Ollama reachability at {polish_cfg.endpoint} ...")
    tags = check_ollama_reachable(polish_cfg.endpoint)
    names = [m.get("name") for m in tags.get("models", [])]
    print(f"OK: host reachable, {len(names)} models present.")
    if polish_cfg.model not in names:
        print(
            f"FAIL: configured model {polish_cfg.model!r} not found in /api/tags response: {names}"
        )
        sys.exit(1)
    print(f"OK: {polish_cfg.model!r} confirmed present.")

    llm = LLMClient(polish_cfg)
    try:
        attempts = [
            ("first attempt (section default settings, no override)", {}),
            ("retry (temperature=0.85, top_p=0.95)", {"temperature": 0.85, "top_p": 0.95}),
        ]

        outcome = None
        for i, (label, extra_kwargs) in enumerate(attempts):
            is_last = i == len(attempts) - 1
            print(f"--- {label} ---")
            outcome = _attempt(llm, extra_kwargs)
            if outcome["success"]:
                break
            _print_attempt_failure(outcome)
            if not is_last:
                print("Retrying once, per the one-retry policy above ...")

        if outcome is None or not outcome["success"]:
            print()
            print(
                f"FAIL: proof did not succeed after {len(attempts)} attempt(s) -- see the "
                "per-attempt failure reasons above."
            )
            sys.exit(1)

        pass_result, reply = outcome["pass_result"], outcome["reply"]

        print("OK: _gate(original, reply) == (True, None).")
        print("OK: _normalize(reply) != _normalize(original) -- genuine rewrite occurred.")

        if pass_result == DOCUMENT_TEXT:
            print(
                "FAIL: generate_polish_pass() fell back to the original document even "
                "though the captured reply it received passed _gate(). This is "
                "fallback behavior, which is NOT proof of a working live backend -- "
                "treating this as a failed proof, not a pass."
            )
            sys.exit(1)

        if pass_result != reply:
            print(
                "FAIL: generate_polish_pass() returned text that differs from the "
                "exact reply its own internal chat() call produced -- unexpected "
                "divergence inside generate_polish_pass() itself."
            )
            print("---- captured reply ----")
            print(reply)
            print("---- generate_polish_pass() result ----")
            print(pass_result)
            sys.exit(1)

        print("OK: generate_polish_pass(original, llm) == reply != original.")
        print()
        print(f"=== PROOF SUCCEEDED: live {polish_cfg.model!r} polish pass produced a genuine, ===")
        print("=== gate-accepted, non-degenerate rewrite via generate_polish_pass(). ===")
        print()
        print("---- ORIGINAL DOCUMENT ----")
        print(DOCUMENT_TEXT)
        print("---- POLISHED OUTPUT ----")
        print(pass_result)
    finally:
        llm.close()


def _run_once(llm, extra_kwargs):
    """Runs generate_polish_pass() through a _CapturingClient wrapping the
    real llm, so the exact raw reply generate_polish_pass() used internally
    is available for a separate _gate() check. Returns (pass_result,
    captured_reply) -- captured_reply is None if the wrapped chat() call
    raised (generate_polish_pass swallows the exception and falls back)."""
    wrapped = _CapturingClient(llm, extra_kwargs=extra_kwargs)
    pass_result = generate_polish_pass(DOCUMENT_TEXT, wrapped)
    return pass_result, wrapped.last_reply


def _attempt(llm, extra_kwargs):
    """Runs one full attempt (one live chat() call via _run_once) and
    evaluates ALL of this proof's failure modes against it, so the caller's
    retry loop only has to check a single `success` flag rather than
    re-deriving which check failed. Three distinct failure modes are
    evaluated, all treated identically by the retry loop above:

      - the wrapped chat() call raised (reply is None) -- a
        connectivity/provider failure, not a gate rejection
      - _gate() rejected the reply -- content-safety rejection
      - the reply passed _gate() but is a content-free echo of the
        original once normalized -- _gate() alone can't see this, since an
        unchanged document trivially has no dropped/invented facts and a
        length ratio of ~1.0; this was the dominant failure mode observed
        in prior live runs and previously escaped the retry entirely
        because it was checked after the retry block instead of inside it

    Returns a dict with enough detail for both the retry loop's own
    pass/fail decision and, on success, main()'s remaining checks
    (pass_result actually reflects the gate-accepted reply, not a
    fallback)."""
    pass_result, reply = _run_once(llm, extra_kwargs)

    if reply is None:
        return {
            "success": False,
            "mode": "exception",
            "reply": None,
            "pass_result": pass_result,
            "reason": None,
        }

    ok, reason = _gate(DOCUMENT_TEXT, reply)
    if not ok:
        return {
            "success": False,
            "mode": "gate_reject",
            "reply": reply,
            "pass_result": pass_result,
            "reason": reason,
        }

    if _normalize(reply) == _normalize(DOCUMENT_TEXT):
        return {
            "success": False,
            "mode": "identical_echo",
            "reply": reply,
            "pass_result": pass_result,
            "reason": None,
        }

    return {
        "success": True,
        "mode": "ok",
        "reply": reply,
        "pass_result": pass_result,
        "reason": None,
    }


def _print_attempt_failure(outcome):
    """Prints a diagnostic for one failed _attempt() outcome, distinguishing
    all three failure modes _attempt() can report."""
    if outcome["mode"] == "exception":
        print(
            "Live chat() call raised inside generate_polish_pass() -- see fallback "
            "behavior; this is a connectivity/provider failure, not a gate rejection."
        )
        return

    if outcome["mode"] == "gate_reject":
        print(f"Gate rejected this attempt's reply: {outcome['reason']}")
        print("---- ORIGINAL ----")
        print(DOCUMENT_TEXT)
        print("---- REJECTED REPLY ----")
        print(outcome["reply"])
        return

    # identical_echo
    print(
        "Model returned the document unchanged (post-normalization) -- not a "
        "genuine rewrite, only whitespace/case noise (e.g. appended blank "
        "lines) or a byte-identical echo. _gate() considers this "
        "content-identical to the original, so it passes _gate() trivially "
        "while still not being proof of a working rewrite."
    )
    print("---- ORIGINAL (repr) ----")
    print(repr(DOCUMENT_TEXT))
    print("---- REPLY (repr) ----")
    print(repr(outcome["reply"]))


if __name__ == "__main__":
    main()
