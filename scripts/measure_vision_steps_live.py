"""Live before/after measurement of generate_step_text() with vision OFF vs
vision ON, against the two real captured sessions on disk
(%USERPROFILE%\\SOPForge\\sessions\\{58fbb2b5-...,e7ce9b62-...}), each 10
steps -- NOT a fixture, NOT a synthesized manifest.

WHY A SHARED, OVERRIDDEN MODEL FOR BOTH ARMS
---------------------------------------------
config/models.toml's default [steps] model is qwen3:32b, which is TEXT-ONLY.
Comparing "qwen3:32b text-only" against "qwen2.5vl:7b + image" would conflate
two independent variables (model capability AND image attachment) into one
number. So this script loads [steps] via load_models_config(), then
overrides ONLY the model to qwen2.5vl:7b (config/models.toml's own [vision]
model -- the one local model on this host actually trained to look at
images) via model_copy(), keeping [steps]'s provider/endpoint otherwise
intact. A single LLMClient built from that overridden section is reused for
BOTH arms of every step, so the only thing that varies between "off" and
"on" is whether an image_url content block is attached -- exactly what the
plan requires.

WHAT "OFF" AND "ON" MEAN
-------------------------
Two direct generate_step_text() calls per step, per session:
  - vision OFF: generate_step_text(step, llm, use_vision=False)
  - vision ON:  generate_step_text(step, llm, use_vision=True,
                screenshot_dir=<session>/screenshots)
Both go through the exact same production code path (generation.py) --
same prompt builder, same round-trip gate, same template-fallback rule --
the only difference is the `use_vision`/`screenshot_dir` arguments.

MEASUREMENT INTEGRITY -- WHY THIS SCRIPT CAN STILL "FAIL" AT EXIT 0
----------------------------------------------------------------------
The round-trip gate (round_trip_ok) can reject a vision-model reply just
like it can reject a text-model reply, in which case generate_step_text()
silently returns the deterministic template with used_fallback=True. A run
where EVERY vision-ON step fell back to template would produce a JSON file
that "looks" complete (right row count, no exceptions) while proving
NOTHING about vision's actual effect -- every text_on would just be the
template, indistinguishable from text_off in the fallback case. That is a
FAILED measurement, not a successful one, so this script computes an
explicit measurement-quality verdict after the live run and prints it in a
loud, impossible-to-miss banner: it does NOT let a clean process exit imply
a meaningful measurement. See _summarize()/main() below.

Usage: python scripts/measure_vision_steps_live.py
Exit code 0 = host reachable, live run completed AND produced a meaningful
              measurement (>=1 non-fallback vision-on row with text_on !=
              text_off).
Exit code 1 = host unreachable, configured model missing, OR the live run
              completed but every vision-on row fell back to template
              (measurement failure -- reported loudly, not glossed over).
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

import httpx  # noqa: E402

from pipeline.config import load_models_config  # noqa: E402
from pipeline.generation import generate_step_text  # noqa: E402
from pipeline.llm_client import LLMClient  # noqa: E402
from pipeline.manifest import load_manifest  # noqa: E402

# The model both arms use -- overrides [steps]'s default (qwen3:32b,
# text-only) to config/models.toml's own [vision] model, the one local
# model on this host actually trained on image input.
VISION_MODEL = "qwen2.5vl:7b"

SESSIONS_ROOT = Path.home() / "SOPForge" / "sessions"
SESSION_IDS = [
    "58fbb2b5-bc04-4fd4-99e3-9099f446c0b5",
    "e7ce9b62-6a80-4b15-9154-01bf378b81b6",
]

OUTPUT_DIR = REPO_ROOT / "scripts" / "vision_measurements"


def check_ollama_reachable(base_url, timeout=5.0):
    """GET /api/tags against the Ollama host root (not the /v1 OpenAI-compat
    path LLMClient itself uses). Returns the parsed tag list on success,
    raises SystemExit with a clear message on any failure -- mirrors
    proof_polish_live.py's identical helper."""
    root = base_url.split("/v1")[0].rstrip("/")
    url = f"{root}/api/tags"
    try:
        resp = httpx.get(url, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - report and abort, not a silent pass
        print(f"FAIL: Ollama host unreachable at {url}: {exc}")
        sys.exit(1)
    return resp.json()


def _measure_step(step, llm, screenshot_dir):
    """Runs both arms for one step and returns its measurement row.
    Sequential (off then on) -- this is a measurement script, not a
    throughput benchmark, so simplicity/order-independence of results
    matters more than wall-clock time."""
    t0 = time.perf_counter()
    text_off, used_fallback_off = generate_step_text(step, llm, use_vision=False)
    latency_off = time.perf_counter() - t0

    screenshot_exists = (screenshot_dir / step.screenshot).exists()

    t0 = time.perf_counter()
    text_on, used_fallback_on = generate_step_text(
        step, llm, use_vision=True, screenshot_dir=screenshot_dir
    )
    latency_on = time.perf_counter() - t0

    return {
        "step_id": step.id,
        "action": step.action,
        "element_name": step.element.name,
        "window_title": step.window.title,
        "screenshot": step.screenshot,
        "screenshot_exists": screenshot_exists,
        "text_off": text_off,
        "used_fallback_off": used_fallback_off,
        "latency_off_s": round(latency_off, 3),
        "text_on": text_on,
        "used_fallback_on": used_fallback_on,
        "latency_on_s": round(latency_on, 3),
        "text_changed": text_on != text_off,
    }


def _summarize(rows):
    """Computes the measurement-quality verdict this script's exit code and
    banner are based on -- see module docstring's "MEASUREMENT INTEGRITY"
    section. A row only counts as meaningful evidence that vision changed
    anything if vision-ON did NOT fall back to template (a fallback row's
    text_on is just the deterministic template, so a text_off/text_on
    difference there proves nothing about vision)."""
    total = len(rows)
    fallback_off = sum(1 for r in rows if r["used_fallback_off"])
    fallback_on = sum(1 for r in rows if r["used_fallback_on"])
    non_fallback_on = [r for r in rows if not r["used_fallback_on"]]
    changed_meaningful = [r for r in non_fallback_on if r["text_changed"]]
    all_vision_on_fell_back = total > 0 and fallback_on == total
    measurement_meaningful = (not all_vision_on_fell_back) and len(changed_meaningful) > 0
    return {
        "total_rows": total,
        "fallback_off_count": fallback_off,
        "fallback_on_count": fallback_on,
        "non_fallback_on_count": len(non_fallback_on),
        "changed_meaningful_count": len(changed_meaningful),
        "all_vision_on_fell_back": all_vision_on_fell_back,
        "measurement_meaningful": measurement_meaningful,
    }


def main():
    cfg = load_models_config(REPO_ROOT / "config" / "models.toml")
    steps_cfg = cfg.steps.model_copy(update={"model": VISION_MODEL})
    print(f"=== measure_vision_steps_live: {VISION_MODEL!r} vision OFF vs ON ===")
    print(
        f"[steps override] provider={steps_cfg.provider!r} "
        f"endpoint={steps_cfg.endpoint!r} model={steps_cfg.model!r}"
    )

    print(f"Checking Ollama reachability at {steps_cfg.endpoint} ...")
    tags = check_ollama_reachable(steps_cfg.endpoint)
    names = [m.get("name") for m in tags.get("models", [])]
    print(f"OK: host reachable, {len(names)} models present.")
    if VISION_MODEL not in names:
        print(f"FAIL: {VISION_MODEL!r} not found in /api/tags response: {names}")
        sys.exit(1)
    print(f"OK: {VISION_MODEL!r} confirmed present.")

    llm = LLMClient(steps_cfg)
    rows = []
    try:
        for session_id in SESSION_IDS:
            session_dir = SESSIONS_ROOT / session_id
            manifest_path = session_dir / "manifest.json"
            screenshot_dir = session_dir / "screenshots"
            print()
            print(f"--- session {session_id} ---")
            print(f"manifest: {manifest_path}")
            if not manifest_path.exists():
                print(f"FAIL: manifest not found: {manifest_path}")
                sys.exit(1)

            manifest = load_manifest(manifest_path)
            print(f"loaded {len(manifest.steps)} steps")

            for i, step in enumerate(manifest.steps, start=1):
                print(
                    f"  [{i}/{len(manifest.steps)}] {step.id} "
                    f"({step.action} {step.element.name!r}) -- off then on ...",
                    flush=True,
                )
                row = _measure_step(step, llm, screenshot_dir)
                row["session_id"] = session_id
                rows.append(row)
                print(
                    f"    off: fallback={row['used_fallback_off']} "
                    f"latency={row['latency_off_s']}s"
                )
                print(
                    f"    on:  fallback={row['used_fallback_on']} "
                    f"latency={row['latency_on_s']}s changed={row['text_changed']}"
                )
    finally:
        llm.close()

    summary = _summarize(rows)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = OUTPUT_DIR / f"vision_step_measurement_{timestamp}.json"
    dump = {
        "timestamp_utc": timestamp,
        "vision_model": VISION_MODEL,
        "steps_endpoint": steps_cfg.endpoint,
        "session_ids": SESSION_IDS,
        "summary": summary,
        "rows": rows,
    }
    out_path.write_text(json.dumps(dump, indent=2), encoding="utf-8")

    print()
    print(f"=== wrote {len(rows)} rows to {out_path} ===")
    print(f"summary: {json.dumps(summary, indent=2)}")

    if summary["all_vision_on_fell_back"]:
        print()
        print("!" * 70)
        print("FAILED MEASUREMENT: every single vision-ON row fell back to the")
        print("deterministic template (round-trip gate rejected every live vision")
        print("reply). This run produced NO evidence about vision's effect on step")
        print("text -- text_on is just the template in every row, indistinguishable")
        print("from text_off's fallback case. Do NOT treat this as a successful")
        print("measurement even though the script completed without exceptions.")
        print("!" * 70)
        sys.exit(1)

    if not summary["measurement_meaningful"]:
        print()
        print("!" * 70)
        print("FAILED MEASUREMENT: no vision-ON row both avoided template fallback")
        print("AND produced text different from its vision-OFF counterpart. This")
        print("run does not demonstrate vision changed step text in any case.")
        print("!" * 70)
        sys.exit(1)

    print()
    print(
        f"OK: measurement meaningful -- {summary['changed_meaningful_count']} of "
        f"{summary['non_fallback_on_count']} non-fallback vision-ON rows differ "
        "from their vision-OFF counterpart."
    )


if __name__ == "__main__":
    main()
