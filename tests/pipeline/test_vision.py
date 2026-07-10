"""Vision captioning: caption_images against a mock transport (no network)."""

import time

import httpx
from PIL import Image

from pipeline.vision import caption_images


def _png(tmp_path, name):
    p = tmp_path / name
    Image.new("RGB", (40, 30), (10, 20, 30)).save(p)
    return p


def test_caption_images_returns_one_caption_per_image(tmp_path):
    imgs = [_png(tmp_path, "1.png"), _png(tmp_path, "2.png")]
    seen = []

    def handler(request):
        body = request.read().decode()
        seen.append(body)
        assert "data:image/png;base64," in body
        assert "the whole procedure" in body
        return httpx.Response(200, json={"choices": [{"message": {"content": "Do the thing."}}]})

    caps = caption_images(
        imgs,
        "narration of the whole procedure",
        "http://x/v1",
        "m",
        transport=httpx.MockTransport(handler),
    )
    assert caps == ["Do the thing.", "Do the thing."]
    assert len(seen) == 2  # one call per image


def test_prompt_instructs_spelling_on_screen_text_over_narration(tmp_path):
    """See consistency.py: a raw narration transcript can spell an
    out-of-vocabulary proper noun differently each time (ASR homophone
    drift). Vision captioning reads the actual on-screen pixels, so it
    should be told to trust what's shown over what the narrator said."""
    imgs = [_png(tmp_path, "1.png")]
    seen = []

    def handler(request):
        seen.append(request.read().decode())
        return httpx.Response(200, json={"choices": [{"message": {"content": "Do the thing."}}]})

    caption_images(
        imgs,
        "narration mentioning a product name",
        "http://x/v1",
        "m",
        transport=httpx.MockTransport(handler),
    )
    assert "spell it exactly as shown in the image" in seen[0]


def test_caption_failure_returns_none_for_that_image(tmp_path):
    imgs = [_png(tmp_path, "1.png")]

    def handler(request):
        return httpx.Response(500, text="boom")

    caps = caption_images(imgs, "", "http://x/v1", "m", transport=httpx.MockTransport(handler))
    assert caps == [None]


def test_empty_caption_becomes_none(tmp_path):
    imgs = [_png(tmp_path, "1.png")]

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]})

    caps = caption_images(imgs, "", "http://x/v1", "m", transport=httpx.MockTransport(handler))
    assert caps == [None]


def test_degenerate_caption_becomes_none(tmp_path):
    """Regression: a real caption came back as a leaked chat-template token
    plus a repetition loop (<|im_start|> addCriterion addCriterion ...) and
    was used verbatim as a step's text -- caption_images must treat this
    exactly like an HTTP failure, not trust it."""
    imgs = [_png(tmp_path, "1.png")]
    garbage = "<|im_start|> addCriterion addCriterion addCriterion addCriterion"

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": garbage}}]})

    caps = caption_images(imgs, "", "http://x/v1", "m", transport=httpx.MockTransport(handler))
    assert caps == [None]


def test_reports_progress_as_each_caption_completes(tmp_path):
    imgs = [_png(tmp_path, f"{i}.png") for i in range(3)]

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "Do it."}}]})

    progress_calls = []
    caps = caption_images(
        imgs,
        "",
        "http://x/v1",
        "m",
        transport=httpx.MockTransport(handler),
        on_progress=lambda done, total: progress_calls.append((done, total)),
    )
    assert caps == ["Do it.", "Do it.", "Do it."]
    assert len(progress_calls) == 3
    assert progress_calls[-1] == (3, 3)
    assert [c[0] for c in progress_calls] == sorted(c[0] for c in progress_calls)


def test_result_order_matches_image_order_despite_out_of_order_completion(tmp_path):
    """Progress is a plain count (as_completed doesn't preserve submission
    order), but the RESULT list must still match image order -- the first
    image's request is deliberately the slowest here to prove it."""
    imgs = [_png(tmp_path, f"{i}.png") for i in (1, 2, 3)]

    def handler(request):
        body = request.read().decode()
        if "step 1 of 3" in body:
            time.sleep(0.1)
            return httpx.Response(200, json={"choices": [{"message": {"content": "First."}}]})
        if "step 2 of 3" in body:
            return httpx.Response(200, json={"choices": [{"message": {"content": "Second."}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Third."}}]})

    caps = caption_images(
        imgs, "", "http://x/v1", "m", transport=httpx.MockTransport(handler), max_workers=3
    )
    assert caps == ["First.", "Second.", "Third."]


def test_on_progress_none_is_unaffected(tmp_path):
    imgs = [_png(tmp_path, "1.png")]

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "Do it."}}]})

    caps = caption_images(imgs, "", "http://x/v1", "m", transport=httpx.MockTransport(handler))
    assert caps == ["Do it."]
