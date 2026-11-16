"""Offline search provider for tests."""

from __future__ import annotations

from shorts.providers.base import ProviderSettings, SearchProvider, SearchResult


class MockSearchSettings(ProviderSettings):
    pass


class MockSearch(SearchProvider):
    name = "mock"
    env_prefix = "SEARCH_MOCK_"
    Settings = MockSearchSettings
    remote = True

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        return [SearchResult(title=f"Result {i} for {query}", url=f"https://example.org/{abs(hash(query)) % 999}/{i}",
                             snippet=f"Snippet {i} about {query}.") for i in range(max_results)]
