"""Self-hosted SearXNG metasearch (JSON API). Enable `json` in settings.yml → search.formats."""

from __future__ import annotations

import httpx

from shorts.providers.base import ProviderError, ProviderSettings, SearchProvider, SearchResult


class SearXNGSettings(ProviderSettings):
    url: str = "http://127.0.0.1:8888"
    language: str = "en"
    timeout: float = 20


class SearXNGSearch(SearchProvider):
    name = "searxng"
    env_prefix = "SEARCH_SEARXNG_"
    Settings = SearXNGSettings
    settings: SearXNGSettings
    remote = True

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        s = self.settings
        try:
            r = httpx.get(f"{s.url.rstrip('/')}/search", params={"q": query, "format": "json", "language": s.language},
                          timeout=s.timeout)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ProviderError(f"SearXNG request failed ({s.url}): {e}. Is the json format enabled?") from e
        return [SearchResult(title=h.get("title", ""), url=h["url"], snippet=h.get("content", ""))
                for h in r.json().get("results", [])[:max_results] if h.get("url")]
