import json

import httpx

from shorts.config import Env
from shorts.models import IdeaBatch
from shorts.registry import create


def test_openai_compat_request_extra_body_and_unload():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, str(req.url), json.loads(req.content) if req.content else None))
        if req.url.path.endswith("/chat/completions"):
            content = "```json\n" + json.dumps({"ideas": [{"title": "t", "hook": "h", "angle": "a",
                                                           "why_it_works": "w"}]}) + "\n```"
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                             "usage": {"prompt_tokens": 5, "completion_tokens": 7}})
        return httpx.Response(200, json={})

    p = create("llm", "openai_compat", Env({
        "LLM_OPENAI_BASE_URL": "http://gpu-box:8080/v1", "LLM_OPENAI_MODEL": "qwen3.8-flash-next",
        "LLM_OPENAI_EXTRA_BODY": '{"chat_template_kwargs": {"enable_thinking": false}}',
        "LLM_OPENAI_UNLOAD_URL": "http://gpu-box:8080/api/models/unload",
    }))
    p._http = httpx.Client(base_url="http://gpu-box:8080/v1", transport=httpx.MockTransport(handler))

    out = p.generate_json(system="s", prompt="p", schema=IdeaBatch)
    assert out.ideas[0].title == "t"  # parsed despite the ```json fence
    method, url, body = seen[0]
    assert url == "http://gpu-box:8080/v1/chat/completions"
    assert body["model"] == "qwen3.8-flash-next"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["response_format"]["type"] == "json_schema"

    p.unload()
    assert seen[-1][:2] == ("POST", "http://gpu-box:8080/api/models/unload")
