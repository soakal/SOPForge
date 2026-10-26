"""Vision captioning: caption_images against a mock transport (no network)."""

import json
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


def test_caption_client_is_built_with_trust_env_false(tmp_path, monkeypatch):
    imgs = [_png(tmp_path, "1.png")]
    recorded = []
    real_client = httpx.Client

    def spy(*args, **kwargs):
        recorded.append(kwargs.get("trust_env"))
        return real_client(*args, **kwargs)

    monkeypatch.setattr("pipeline.vision.httpx.Client", spy)

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "Do it."}}]})

    mock = httpx.MockTransport(handler)
    assert caption_images(imgs, "", "http://x/v1", "m", transport=mock) == ["Do it."]
    assert recorded == [False]
    caption_images(imgs, "", "http://x/v1", "m", transport=mock, trust_env=True)
    assert recorded == [False, True]


def test_anthropic_provider_uses_messages_api_not_endpoint(tmp_path):
    """provider=anthropic must hit api.anthropic.com with x-api-key and an
    Anthropic image block -- never the configured (ollama) endpoint, which
    would leak the key -- and read the reply's text blocks."""
    imgs = [_png(tmp_path, "1.png")]
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "thinking", "thinking": "..."},
                    {"type": "text", "text": "Click Save."},
                ]
            },
        )

    caps = caption_images(
        imgs,
        "narration",
        "http://192.168.200.60:11434/v1",
        "claude-sonnet-5-5",
        api_key="sk-ant-test",
        transport=httpx.MockTransport(handler),
        provider="anthropic",
    )
    assert caps == ["Click Save."]
    req = seen[0]
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "sk-ant-test"
    assert "authorization" not in req.headers
    body = json.loads(req.read())
    assert body["system"] and body["model"] == "claude-sonnet-5-5"
    image = body["messages"][0]["content"][0]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/png"


def test_prompt_asks_for_a_title_line_and_split_title_parses_it():
    from pipeline.vision import _prompt, split_title

    assert "Title:" in _prompt("", 1, 3)
    assert split_title("Title: Open Products\nClick Products.") == (
        "Open Products",
        "Click Products.",
    )
    assert split_title('**Title:** "Sign in"\n\nClick Login.') == ("Sign in", "Click Login.")
    # The prompt's own example is quoted, so models quote back; a quote that
    # is part of the title must survive.
    assert split_title("Title: 'Open Subscriptions > Products'\nx")[0] == (
        "Open Subscriptions > Products"
    )
    assert split_title("Title: Click 'OK'\nPress it.")[0] == "Click 'OK'"
    assert split_title("Click Save.") == (None, "Click Save.")  # format ignored: as before
    assert split_title("Title: Only a title") == ("Only a title", None)
    assert split_title("Title of the page is X. Click it.")[0] is None
    assert split_title(None) == (None, None)
