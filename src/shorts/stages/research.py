"""Web research for the fact-check: native LLM web search (Claude), or search provider + page fetch (local LLMs)."""

from __future__ import annotations

import html as htmllib
import logging
import re
from pathlib import Path

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


def cached_search(ctx: Context, query: str, max_results: int):
    """Search through the configured provider, with an on-disk cache (SEARCH_CACHE_DAYS) so repeated
    fact-checks don't hit search engines again (they rate-limit)."""
    import hashlib
    import json
    import time

    from shorts.providers.base import SearchResult

    app = ctx.app
    days = app.search_cache_days
    path = Path(app.search_cache_file) if app.search_cache_file else None
    key = hashlib.sha1(f"{ctx.search().describe()}\0{max_results}\0{query.strip().lower()}".encode()).hexdigest()
    cache: dict = {}
    if days > 0 and path and path.exists():
        try:
            cache = json.loads(path.read_text())
        except ValueError:
            cache = {}
        hit = cache.get(key)
        if hit and time.time() - hit["at"] < days * 86400:
            return [SearchResult.model_validate(r) for r in hit["results"]]
    results = ctx.search().search(query, max_results)
    if days > 0 and path:
        cutoff = time.time() - days * 86400
        cache = {k: v for k, v in cache.items() if v.get("at", 0) > cutoff}
        cache[key] = {"at": time.time(), "query": query, "results": [r.model_dump() for r in results]}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False))
    return results


class ResearchStats(BaseModel):
    mode: str = "search"  # search | native (LLM's own web search) | none (not configured)
    queries: int = 0
    failed: int = 0  # queries that errored or returned nothing
    sources: int = 0
    pages: int = 0  # pages or article excerpts read
    errors: list[str] = Field(default_factory=list)

    @property
    def no_web(self) -> bool:
        """True when the facts could only be checked from the model's own knowledge."""
        return self.mode == "none" or (self.mode == "search" and self.sources == 0)

    def warning(self) -> str:
        if self.mode == "none":
            return "No web search configured: facts were checked from the model's own knowledge only."
        if self.sources == 0:
            return (f"Web search failed: {self.failed} of {self.queries} queries returned nothing usable. Facts were "
                    f"checked from the model's own knowledge only.")
        if self.failed:
            return f"{self.failed} of {self.queries} web searches failed; {self.sources} sources were used."
        return ""


NO_WEB_NOTE = ("(WEB SEARCH FAILED: no search results are available. Judge only from well-established knowledge, "
               "and mark every claim you are not certain about as unverifiable.)")


def research(ctx: Context, topic: str, narration: str) -> str:
    """Research notes (free text) for the review prompt, or "" when no web access is configured."""
    return research_report(ctx, topic, narration)[0]


def research_report(ctx: Context, topic: str, narration: str) -> tuple[str, ResearchStats]:
    """Research notes plus what happened (so a failed search is visible in review.json and on the storyboard)."""
    app = ctx.app
    llm = ctx.llm("review")
    if llm.supports_web_search:
        text = llm.research(system=render("research_system", app.prompts_dir),
                            prompt=render("research_user", app.prompts_dir, topic=topic, narration=narration))
        return text, ResearchStats(mode="native", sources=1 if text.strip() else 0)
    if app.search_provider in ("", "none"):
        log.warning("no web search: %s has none and SEARCH_PROVIDER=none; facts are checked from model knowledge",
                    llm.describe())
        return "", ResearchStats(mode="none")
    queries = llm.generate_json(
        system=render("research_system", app.prompts_dir),
        prompt=render("search_queries_user", app.prompts_dir, topic=topic, narration=narration,
                      count=app.review_search_queries),
        schema=SearchQueries,
    ).queries[: app.review_search_queries]
    seen, blocks, fetched = set(), [], 0
    stats = ResearchStats(queries=len(queries))
    for q in queries:
        try:
            results = cached_search(ctx, q, app.review_search_results)
        except Exception as e:  # noqa: BLE001 - one failing query shouldn't kill the review
            log.warning("search failed for %r: %s", q, e)
            stats.failed += 1
            stats.errors.append(f"{q}: {str(e)[:160]}")
            continue
        if not results:
            stats.failed += 1
        lines = [f"## Query: {q}"]
        for res in results:
            if res.url in seen:
                continue
            seen.add(res.url)
            lines.append(f"- {res.title} ({res.url})\n  {res.snippet}")
            if res.text:  # the provider already has the relevant text (e.g. Wikipedia)
                lines.append(f"  Article excerpt: {res.text}")
                fetched += 1
            elif fetched < app.review_fetch_pages:
                text = page_text(res.url)
                if text:
                    fetched += 1
                    lines.append(f"  Page excerpt: {text}")
        blocks.append("\n".join(lines))
    stats.sources, stats.pages = len(seen), fetched
    log.info("research: %d queries (%d failed), %d sources, %d pages read", len(queries), stats.failed, len(seen),
             fetched)
    if stats.no_web:
        log.warning("research: %s", stats.warning())
        return "", stats
    return ("Web search results (verify each claim against these; cite the URL you rely on):\n\n"
            + "\n\n".join(blocks)), stats
