"""Web research for the fact-check: native LLM web search (Claude), or search provider + page fetch (local LLMs)."""

from __future__ import annotations

import html as htmllib
import logging
import re

import httpx
from pydantic import BaseModel, Field

from shorts.context import Context
from shorts.prompts import render

log = logging.getLogger("shorts.research")


class SearchQueries(BaseModel):
    queries: list[str] = Field(description="Short web search queries, one per claim worth checking")


def page_text(url: str, limit: int = 4000) -> str:
    try:
        r = httpx.get(url, timeout=12, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; shorts-factcheck/0.1)"})
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.debug("fetch %s failed: %s", url, e)
        return ""
    if "html" not in r.headers.get("content-type", "html"):
        return ""
    html = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header|math|table)\b.*?</\1\s*>", " ", r.text)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    text = re.sub(r"""<(?:[^>"']|"[^"]*"|'[^']*')*>""", " ", html)  # tags, quote-aware
    text = htmllib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def research(ctx: Context, topic: str, narration: str) -> str:
    """Return research notes (free text) for the review prompt, or "" when no web access is configured."""
    app = ctx.app
    llm = ctx.llm("review")
    if llm.supports_web_search:
        return llm.research(system=render("research_system", app.prompts_dir),
                            prompt=render("research_user", app.prompts_dir, topic=topic, narration=narration))
    if app.search_provider in ("", "none"):
        log.warning("no web search: %s has none and SEARCH_PROVIDER=none; facts are checked from model knowledge",
                    llm.describe())
        return ""
    search = ctx.search()
    queries = llm.generate_json(
        system=render("research_system", app.prompts_dir),
        prompt=render("search_queries_user", app.prompts_dir, topic=topic, narration=narration,
                      count=app.review_search_queries),
        schema=SearchQueries,
    ).queries[: app.review_search_queries]
    seen, blocks, fetched = set(), [], 0
    for q in queries:
        try:
            results = search.search(q, app.review_search_results)
        except Exception as e:  # noqa: BLE001 - one failing query shouldn't kill the review
            log.warning("search failed for %r: %s", q, e)
            continue
        lines = [f"## Query: {q}"]
        for res in results:
            if res.url in seen:
                continue
            seen.add(res.url)
            lines.append(f"- {res.title} ({res.url})\n  {res.snippet}")
            if fetched < app.review_fetch_pages:
                text = page_text(res.url)
                if text:
                    fetched += 1
                    lines.append(f"  Page excerpt: {text}")
        blocks.append("\n".join(lines))
    log.info("research: %d queries, %d sources, %d pages read", len(queries), len(seen), fetched)
    return "Web search results (verify each claim against these; cite the URL you rely on):\n\n" + "\n\n".join(blocks)
