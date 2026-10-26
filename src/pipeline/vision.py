"""Vision-model captioning for the screenshots build mode. For each screenshot
a vision model (qwen2.5vl on the local Ollama by default) is shown the image
plus the overall narration and asked to write that step's instruction.

This does NOT decide the steps -- the steps are the uploaded images, one each,
in order (still ground truth). The model only PHRASES each step. Best-effort
and parallel: any per-image failure yields None so the caller falls back to the
transcript text, never crashing generation."""

import base64
import re
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from pipeline.llm_client import (
    ANTHROPIC_DEFAULT_MAX_TOKENS,
    ANTHROPIC_MESSAGES_URL,
    ANTHROPIC_VERSION,
)
from pipeline.textgate import degenerate_reason

logger = logging.getLogger(__name__)

_SYSTEM = "You write clear, concise, imperative steps for a Standard Operating Procedure."


def _image_data_url(path):
    """Base64-encodes a PNG screenshot into an OpenAI-compatible
    `data:image/png;base64,...` URL for a vision-model `image_url` content
    block. Shared by _caption_one (this module) and generation.py's
    generate_step_text -- one place for the encoding so both vision-capable
    call sites build the identical data URL shape."""
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{data}"


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
        "'screenshot' or 'step', and do not number it. If a file name, product name, or other "
        "proper noun is visible on screen, spell it exactly as shown in the image, even if the "
        "narrator's description above spells it differently.\n\n"
        "Reply in exactly this format:\n"
        "Title: <a 2-6 word title for this action, e.g. 'Open Subscriptions > Products'>\n"
        "<the 1-2 sentence instruction>"
    )


_TITLE_LINE = re.compile(r"^\W*title\W*:\s*(.+?)\s*$", re.IGNORECASE)
MAX_TITLE_LEN = 80


def split_title(caption):
    """(title, text) from a caption in _prompt's "Title: ...\\n<instruction>"
    format. A model that ignored the format yields (None, caption) -- the
    caption is then used exactly as before. A reply that is ONLY a title
    line yields (title, None) so the caller falls back for the text. The
    title is display-only (a heading/TOC label); the step itself is still
    the uploaded image."""
    if not caption:
        return None, caption
    first, _sep, rest = caption.strip().partition("\n")
    match = _TITLE_LINE.match(first.strip())
    if not match:
        return None, caption
    title = match.group(1).strip(" *`")
    # Only a wrapping pair of quotes comes off ("Sign in" -> Sign in); a
    # quote that is part of the title (Click 'OK') stays.
    if len(title) > 1 and title[0] == title[-1] and title[0] in "\"'":
        title = title[1:-1]
    title = title[:MAX_TITLE_LEN].strip() or None
    return title, rest.strip() or None


def _anthropic_request(path, prompt, model, api_key):
    """(url, headers, json) for Anthropic's Messages API: a base64 image
    block, the system prompt top-level. Always the fixed api.anthropic.com
    URL -- never the section's (ollama) endpoint, so the key can't leak."""
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        ANTHROPIC_MESSAGES_URL,
        {"x-api-key": api_key or "", "anthropic-version": ANTHROPIC_VERSION},
        {
            "model": model,
            "max_tokens": ANTHROPIC_DEFAULT_MAX_TOKENS,
            "system": _SYSTEM,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": data},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        },
    )


def _caption_one(
    path,
    narration,
    index,
    total,
    endpoint,
    model,
    timeout,
    transport,
    api_key,
    trust_env=False,
    provider="ollama",
):
    """Returns the caption text, or None on any failure -- including a
    degenerate reply (a repetition loop, a leaked chat-template special
    token) treated exactly like an HTTP failure, since the caller's
    fallback chain (caption or per_step.get(...) or a placeholder) already
    handles a missing caption safely. Never raises."""
    try:
        prompt = _prompt(narration, index, total)
        if provider == "anthropic":
            url, headers, payload = _anthropic_request(path, prompt, model, api_key)
        else:
            url = f"{endpoint.rstrip('/')}/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": _image_data_url(path)}},
                        ],
                    },
                ],
                "stream": False,
            }
        # Cap the CONNECT phase short (like LLMClient) so an unreachable/
        # firewalled endpoint fails fast per image instead of stalling the whole
        # build ~timeout seconds each.
        client_timeout = httpx.Timeout(timeout, connect=5.0)
        with httpx.Client(
            transport=transport, timeout=client_timeout, trust_env=trust_env
        ) as client:
            resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if provider == "anthropic":
            # Text blocks only -- current models may emit a thinking block first.
            text = "".join(
                b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
            ).strip()
        else:
            text = data["choices"][0]["message"]["content"].strip()
        if not text:
            return None
        reason = degenerate_reason(text)
        if reason:
            logger.warning("vision caption for %s rejected as degenerate (%s)", path, reason)
            return None
        return text
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
    trust_env=False,
    provider="ollama",
):
    """Caption each image, in parallel. Returns a list the same length as
    image_paths; each entry is the caption string or None on failure. Order is
    preserved regardless of completion order. `api_key` (Bearer) is used for
    OpenAI-compatible providers; `provider="anthropic"` switches to the
    Messages API (x-api-key) and ignores `endpoint`. `transport` is
    injectable for tests (no network).
    `on_progress`, if given, is called as `on_progress(completed, total)` as
    each image's caption finishes (mirroring generate_all_steps' callback) --
    a plain count, so out-of-order completion doesn't matter."""
    total = len(image_paths)
    if total == 0:
        return []

    def work(item):
        index, path = item
        return _caption_one(
            path,
            narration,
            index,
            total,
            endpoint,
            model,
            timeout,
            transport,
            api_key,
            trust_env,
            provider,
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
