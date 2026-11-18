"""Wikipedia through the official MediaWiki API: no key, no install, reliable, and a far better source than most
pages a general web search returns. Each result carries the article's most relevant paragraphs as `text`."""

from __future__ import annotations

import re

import httpx

from shorts.providers.base import ProviderError, ProviderSettings, SearchProvider, SearchResult

# Wikimedia asks API clients for a descriptive User-Agent; this one names the tool, not the user.
USER_AGENT = "shorts-pipeline/0.1 (local fact-check tool; python-httpx)"


def excerpt(text: str, query: str, limit: int) -> str:
    """The paragraphs of `text` that share the most words with `query`, in article order, up to `limit` chars."""
    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2}
    paras = [p.strip() for p in re.split(r"\n+", text) if len(p.strip()) > 40 and not p.startswith("==")]
    if not paras:
        return text[:limit]
    scored = sorted(range(len(paras)), key=lambda i: (-len(words & set(re.findall(r"[a-z0-9]+", paras[i].lower()))), i))
    keep, size = {0}, len(paras[0])  # always the lead paragraph
    for i in scored:
        if i in keep:
            continue
        if size + len(paras[i]) > limit:
            break
        keep.add(i)
        size += len(paras[i])
    return "\n".join(paras[i] for i in sorted(keep))[:limit]


class WikipediaSettings(ProviderSettings):
    lang: str = "en"
    text_chars: int = 3500  # per article
    timeout: float = 20


class WikipediaSearch(SearchProvider):
    name = "wikipedia"
    env_prefix = "SEARCH_WIKIPEDIA_"
    Settings = WikipediaSettings
    settings: WikipediaSettings
    remote = True

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        s = self.settings
        api = f"https://{s.lang}.wikipedia.org/w/api.php"
        try:
            r = httpx.get(api, timeout=s.timeout, headers={"User-Agent": USER_AGENT}, params={
                "action": "query", "format": "json", "formatversion": "2", "generator": "search",
                "gsrsearch": query, "gsrlimit": max_results, "prop": "extracts|info", "inprop": "url",
                "explaintext": "1", "exsectionformat": "plain"})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderError(f"Wikipedia search failed: {e}") from e
        pages = sorted(r.json().get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
        out = []
        for p in pages:
            text = p.get("extract") or ""
            out.append(SearchResult(title=p["title"], url=p.get("fullurl", ""), snippet=text[:300].replace("\n", " "),
                                    text=excerpt(text, query, s.text_chars)))
        return out
