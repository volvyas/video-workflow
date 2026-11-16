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
