"""Turn a pydantic model into a strict, self-contained JSON schema usable by every LLM backend."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from pydantic import BaseModel

_DROP_KEYS = {"title", "default", "examples"}


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"].split("/")[-1]
                merged = {**copy.deepcopy(defs[ref]), **{k: v for k, v in node.items() if k != "$ref"}}
                return walk(merged)
            out = {k: walk(v) for k, v in node.items() if k not in _DROP_KEYS}
            if out.get("type") == "object" and "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"].keys())
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def extract_json(text: str) -> Any:
    """Parse JSON from a model reply, tolerating ```json fences or surrounding prose."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("{"), text.find("[")) if i >= 0), default=-1)
        end = max(text.rfind("}"), text.rfind("]"))
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def schema_instructions(model: type[BaseModel]) -> str:
    return (
        "Respond with a single JSON object only, no prose, matching this JSON schema:\n"
        + json.dumps(strict_schema(model), ensure_ascii=False)
    )
