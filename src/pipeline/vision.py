"""Vision-model captioning + element localization for the screenshots build
mode. For each screenshot a vision model (qwen2.5vl on the local Ollama by
default) is shown the image plus the overall narration and asked, in one call,
for BOTH that step's instruction AND the pixel bounding box of the UI element
to interact with. The box is used to draw a highlight on the screenshot.

This does NOT decide the steps -- the steps are the uploaded images, one each,
in order (still ground truth). The model only PHRASES each step and POINTS at
the element. Best-effort and parallel: any per-image failure yields (None,
None) for that image so the caller falls back to the transcript text and skips
the highlight, never crashing generation."""

import base64
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import httpx

logger = logging.getLogger(__name__)

_SYSTEM = "You write clear, concise, imperative steps for a Standard Operating Procedure."
_JSON_RE = re.compile(r"\{.*\}", re.S)


def _prompt(narration, index, total, width, height):
    context = (
        f"Here is the narrator's description of the whole procedure, for context:\n\n{narration}\n\n"
        if narration
        else ""
    )
    return (
        f"{context}This is step {index} of {total} in the procedure. The screenshot is "
        f"{width}x{height} pixels. Respond with ONLY a JSON object of the form "
        '{"instruction": "...", "box": [x1, y1, x2, y2]} where "instruction" is 1-2 short '
        "imperative sentences telling the reader exactly what to do at this step (specific to "
        "what is shown; do not mention 'screenshot' or 'step', do not number it), and "
        '"box" is the pixel bounding box of the single UI element the user should click or '
        "interact with, in this image's pixel coordinates. If there is no single element, use "
        "null for box."
    )


def _parse(content):
    match = _JSON_RE.search(content)
    if not match:
        return (content.strip() or None), None
    try:
        obj = json.loads(match.group(0))
    except (ValueError, TypeError):
        return (content.strip() or None), None
    text = (obj.get("instruction") or "").strip() or None
    box = obj.get("box")
    if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
        box = [float(v) for v in box]
    else:
        box = None
    return text, box


def _caption_one(path, narration, index, total, endpoint, model, timeout, transport):
    """Returns (instruction_or_None, box_or_None). Never raises."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            width, height = im.size
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        with httpx.Client(transport=transport, timeout=timeout) as client:
            resp = client.post(
                f"{endpoint.rstrip('/')}/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM},
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": _prompt(narration, index, total, width, height),
                                },
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
        return _parse(resp.json()["choices"][0]["message"]["content"])
    except Exception:  # noqa: BLE001 - captioning is best-effort; caller falls back
        logger.warning("vision caption failed for %s", path, exc_info=True)
        return None, None


def caption_images(
    image_paths, narration, endpoint, model, timeout=120.0, max_workers=4, transport=None
):
    """Caption + locate each image, in parallel. Returns a list the same length
    as image_paths; each entry is a (instruction_or_None, box_or_None) tuple.
    Order is preserved. `transport` is injectable for tests (no network)."""
    total = len(image_paths)
    if total == 0:
        return []

    def work(item):
        index, path = item
        return _caption_one(path, narration, index, total, endpoint, model, timeout, transport)

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, total))) as pool:
        return list(pool.map(work, [(i + 1, p) for i, p in enumerate(image_paths)]))
