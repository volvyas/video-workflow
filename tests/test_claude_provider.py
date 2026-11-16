"""Offline check of the Claude provider: request shape and response parsing (no network, no key)."""

import json

import httpx2 as httpx
import pytest

pytest.importorskip("anthropic")

from shorts.config import Env  # noqa: E402
from shorts.models import IdeaBatch  # noqa: E402
from shorts.registry import create  # noqa: E402


def test_claude_request_and_parse():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        payload = {"ideas": [{"title": "t", "hook": "h", "angle": "a", "why_it_works": "w"}]}
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": json.dumps(payload)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

    p = create("llm", "claude", Env({"LLM_CLAUDE_API_KEY": "test", "LLM_CLAUDE_EFFORT": "high"}))
    import anthropic

    p._client = anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = p.generate_json(system="sys", prompt="hi", schema=IdeaBatch)

    assert out.ideas[0].title == "t"
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["beta"]
    assert "thinking" not in body and "temperature" not in body


def _msg(content, stop="end_turn"):
    return {"id": "msg_x", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}


def _provider(handler):
    import anthropic

    p = create("llm", "claude", Env({"LLM_CLAUDE_API_KEY": "test"}))
    p._client = anthropic.Anthropic(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return p


def test_claude_research_resumes_pause_turn():
    bodies = []
    replies = [
        _msg([{"type": "text", "text": "Checking claim one."},
              {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "q"}}],
             stop="pause_turn"),
        _msg([{"type": "text", "text": "Claim one: correct. https://example.org"}]),
    ]

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=replies[len(bodies) - 1])

    out = _provider(handler).research(system="s", prompt="check this")
    assert out == "Checking claim one.\nClaim one: correct. https://example.org"
    assert bodies[0]["tools"][0]["type"] == "web_search_20260209"
    assert [m["role"] for m in bodies[1]["messages"]] == ["user", "assistant"]  # resumed, no extra user turn
    assert bodies[1]["messages"][1]["content"][1]["type"] == "server_tool_use"


def test_claude_generate_json_with_image(tmp_path):
    from PIL import Image

    from shorts.models import ImageVerdict

    img = tmp_path / "x.png"
    Image.new("RGB", (2000, 3000), "red").save(img)
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        verdict = {"matches_prompt": True, "has_text_or_watermark": False, "has_artifacts": False,
                   "score": 8, "problems": []}
        return httpx.Response(200, json=_msg([{"type": "text", "text": json.dumps(verdict)}]))

    v = _provider(handler).generate_json(system="s", prompt="p", schema=ImageVerdict, images=[img])
    assert v.acceptable(6)
    content = bodies[0]["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    assert content[1] == {"type": "text", "text": "p"}
