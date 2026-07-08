"""Vision captioning: caption_images against a mock transport (no network)."""

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
