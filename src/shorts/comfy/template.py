"""ComfyUI API-format workflow templates with {{placeholders}}.

- A string that is exactly "{{name}}" is replaced by the typed value (int stays int).
- "{{name}}" inside a longer string is substituted as text.
- Unknown placeholders raise, so a typo in a workflow fails loudly.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

_FULL = re.compile(r"^\{\{\s*(\w+)\s*\}\}$")
_INLINE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class TemplateError(ValueError):
    pass


def load(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "nodes" in data and "links" in data:
        raise TemplateError(
            f"{path} is a UI-format workflow. In ComfyUI use Workflow → Export (API) and save that file instead."
        )
    return data


def placeholders(workflow: dict[str, Any]) -> set[str]:
    return set(_INLINE.findall(json.dumps(workflow)))


def fill(workflow: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    missing = placeholders(workflow) - values.keys()
    if missing:
        raise TemplateError(f"workflow placeholders without values: {sorted(missing)}")

    def sub(node: Any) -> Any:
        if isinstance(node, str):
            m = _FULL.match(node)
            if m:
                return values[m.group(1)]
            return _INLINE.sub(lambda mm: str(values[mm.group(1)]), node)
        if isinstance(node, dict):
            return {k: sub(v) for k, v in node.items()}
        if isinstance(node, list):
            return [sub(v) for v in node]
        return node

    return sub(copy.deepcopy(workflow))


def describe(workflow: dict[str, Any]) -> list[str]:
    """Human-readable list of nodes and their literal inputs (for `shorts comfy inspect`)."""
    rows = []
    for node_id, node in sorted(workflow.items(), key=lambda kv: _sort_key(kv[0])):
        title = node.get("_meta", {}).get("title", "")
        rows.append(f"[{node_id}] {node.get('class_type')}" + (f"  “{title}”" if title else ""))
        for k, v in node.get("inputs", {}).items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[1], int):
                continue  # link to another node
            shown = json.dumps(v, ensure_ascii=False)
            rows.append(f"    {node_id}.{k} = {shown[:100]}{'…' if len(shown) > 100 else ''}")
    return rows


def mark(workflow: dict[str, Any], assignments: list[str]) -> dict[str, Any]:
    """Apply 'NODE.INPUT=placeholder' assignments, writing "{{placeholder}}" into the workflow."""
    wf = copy.deepcopy(workflow)
    for a in assignments:
        try:
            target, name = a.split("=", 1)
            node_id, inp = target.split(".", 1)
        except ValueError:
            raise TemplateError(f"bad assignment '{a}', expected NODE.INPUT=name")
        if node_id not in wf:
            raise TemplateError(f"node {node_id} not in workflow")
        if inp not in wf[node_id].get("inputs", {}):
            raise TemplateError(f"node {node_id} ({wf[node_id].get('class_type')}) has no input '{inp}'")
        wf[node_id]["inputs"][inp] = "{{" + name.strip() + "}}"
    return wf


def _sort_key(node_id: str):
    return (0, int(node_id)) if node_id.isdigit() else (1, node_id)
