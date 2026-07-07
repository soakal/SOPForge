"""Vision-model captioning for the screenshots+transcript build mode. For each
screenshot, a vision LLM (qwen2.5vl on the local Ollama by default, via the
OpenAI-compatible /chat/completions endpoint with a base64 image) is shown the
image plus the overall narration and asked to write that step's instruction.

This does NOT decide the steps -- the steps are the uploaded images, one each,
in order (still ground truth). The model only PHRASES each step, exactly like
the capture flow's step generation. Best-effort: any per-image failure returns
None for that image so the caller can fall back to the transcript text, never
crashing generation."""

import base64
import logging

import httpx

logger = logging.getLogger(__name__)

_SYSTEM = "You write clear, concise, imperative steps for a Standard Operating Procedure."


def _prompt(narration, index, total):
    context = (
        f"Here is the narrator's description of the whole procedure, for context:\n\n"
        f"{narration}\n\n"
        if narration
        else ""
    )
    return (
        f"{context}This is step {index} of {total} in the procedure. Looking at the "
        "screenshot, write 1-2 short imperative sentences telling the reader exactly "
        "what to do at this step. Be specific to what is shown on screen. Do not "
        "mention the words 'screenshot' or 'step', and do not number it."
    )


def caption_image(image_path, narration, index, total, endpoint, model, client, timeout=120.0):
    """Caption a single image via the vision model. Returns the caption text, or
    None on any failure (unreachable endpoint, bad response, empty reply)."""
    try:
        data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        resp = client.post(
            f"{endpoint.rstrip('/')}/chat/completions",
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
            timeout=timeout,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        return text or None
    except Exception:  # noqa: BLE001 - captioning is best-effort; caller falls back
        logger.warning("vision caption failed for %s", image_path, exc_info=True)
        return None


def caption_images(image_paths, narration, endpoint, model, timeout=120.0, transport=None):
    """Caption each image in order. Returns a list the same length as
    image_paths; each entry is the caption string or None on failure.
    `transport` is injectable for tests so they never make a real network call."""
    captions = []
    total = len(image_paths)
    with httpx.Client(transport=transport, timeout=timeout) as client:
        for i, path in enumerate(image_paths, start=1):
            captions.append(
                caption_image(path, narration, i, total, endpoint, model, client, timeout)
            )
    return captions
