"""DuckDuckGo search via the `ddgs` package: no API key, no server. Rate-limited for heavy use.

Install with `uv sync --extra search`.
"""

from __future__ import annotations

from shorts.providers.base import ProviderError, ProviderSettings, SearchProvider, SearchResult


class DDGSSettings(ProviderSettings):
    region: str = "us-en"  # e.g. us-en, uk-en, ua-uk (country-language)
    safesearch: str = "moderate"
    backends: str = "google,yahoo,duckduckgo,bing,brave"  # tried in order until one returns results


class DDGSSearch(SearchProvider):
    name = "ddgs"
    env_prefix = "SEARCH_DDGS_"
    Settings = DDGSSettings
    settings: DDGSSettings
    remote = True

    def verify(self) -> None:
        self._require_module("ddgs", "search")

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        try:
            from ddgs import DDGS
        except ImportError as e:
            raise ProviderError("ddgs not installed: run `uv sync --extra search`") from e
        s = self.settings
        errors = []
        for backend in [b.strip() for b in s.backends.split(",") if b.strip()]:
            try:
                hits = DDGS().text(query, region=s.region, safesearch=s.safesearch, backend=backend,
                                   max_results=max_results) or []
            except Exception as e:  # noqa: BLE001 - ddgs raises on "no results" too; try the next backend
                errors.append(f"{backend}: {e}")
                continue
            results = [SearchResult(title=h.get("title", ""), url=h.get("href", ""), snippet=h.get("body", ""))
                       for h in hits if h.get("href")]
            if results:
                return results
        raise ProviderError(f"no search results for {query!r} ({'; '.join(errors)[:300]})")
