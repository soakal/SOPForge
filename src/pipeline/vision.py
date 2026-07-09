"""Vision-model captioning for the screenshots build mode. For each screenshot
a vision model (qwen2.5vl on the local Ollama by default) is shown the image
plus the overall narration and asked to write that step's instruction.

This does NOT decide the steps -- the steps are the uploaded images, one each,
in order (still ground truth). The model only PHRASES each step. Best-effort
and parallel: any per-image failure yields None so the caller falls back to the
transcript text, never crashing generation."""

import base64
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

logger = logging.getLogger(__name__)

_SYSTEM = "You write clear, concise, imperative steps for a Standard Operating Procedure."


def _prompt(narration, index, total):
    context = (
        f"Here is the narrator's description of the whole procedure, for context:\n\n{narration}\n\n"
        if narration
        else ""
    )
    return (
        f"{context}This is step {index} of {total} in the procedure. Looking at the "
        "screenshot, write 1-2 short imperative sentences telling the reader exactly what to "
        "do at this step. Be specific to what is shown on screen. Do not mention the words "
        "'screenshot' or 'step', and do not number it."
    )


def _caption_one(path, narration, index, total, endpoint, model, timeout, transport, api_key):
    """Returns the caption text, or None on any failure. Never raises."""
    try:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # Cap the CONNECT phase short (like LLMClient) so an unreachable/
        # firewalled endpoint fails fast per image instead of stalling the whole
        # build ~timeout seconds each.
        client_timeout = httpx.Timeout(timeout, connect=5.0)
        with httpx.Client(transport=transport, timeout=client_timeout) as client:
            resp = client.post(
                f"{endpoint.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": _prompt(narration, index, total)},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{data}"},
                                },
                            ],
                        },
                    ],
                    "stream": False,
                },
            )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip() or None
    except Exception:  # noqa: BLE001 - captioning is best-effort; caller falls back
        logger.warning("vision caption failed for %s", path, exc_info=True)
        return None


def caption_images(
    image_paths,
    narration,
    endpoint,
    model,
    api_key=None,
    timeout=120.0,
    max_workers=4,
    transport=None,
    on_progress=None,
):
    """Caption each image, in parallel. Returns a list the same length as
    image_paths; each entry is the caption string or None on failure. Order is
    preserved regardless of completion order. `api_key` (Bearer) is used for
    non-ollama providers; `transport` is injectable for tests (no network).
    `on_progress`, if given, is called as `on_progress(completed, total)` as
    each image's caption finishes (mirroring generate_all_steps' callback) --
    a plain count, so out-of-order completion doesn't matter."""
    total = len(image_paths)
    if total == 0:
        return []

    def work(item):
        index, path = item
        return _caption_one(
            path, narration, index, total, endpoint, model, timeout, transport, api_key
        )

    results = [None] * total
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, total))) as pool:
        futures = {pool.submit(work, (i + 1, p)): i for i, p in enumerate(image_paths)}
        done = 0
        for future in as_completed(futures):
            results[futures[future]] = future.result()
            done += 1
            if on_progress:
                on_progress(done, total)
    return results
