"""Several search providers combined: e.g. Wikipedia first, then the open web. Results from blocked domains
(content farms, video sites) are dropped and reliable domains are ranked first."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from shorts.providers.base import ProviderError, ProviderSettings, SearchProvider, SearchResult

DEFAULT_BLOCK = ("iere.org,youtube.com,youtu.be,tiktok.com,facebook.com,instagram.com,pinterest.com,quora.com,"
                 "reddit.com,x.com,twitter.com")
DEFAULT_PREFER = ("wikipedia.org,britannica.com,nasa.gov,noaa.gov,nih.gov,nature.com,science.org,cell.com,"
                  "pnas.org,sciencedirect.com,springer.com,wiley.com,smithsonianmag.com,nationalgeographic.com,"
                  "scientificamerican.com,newscientist.com,bbc.com,bbc.co.uk,.gov,.edu,.ac.uk,.int")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


_STOP = {"the", "and", "for", "with", "what", "how", "does", "can", "are", "why", "from", "that", "this", "into",
         "your", "you", "its", "was", "were", "has", "have", "about", "effect", "effects"}


def relevance(query: str, hit: SearchResult) -> float:
    """Share of the query's words found in the result (title counts double)."""
    words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2 and w not in _STOP}
    if not words:
        return 0.0
    title = set(re.findall(r"[a-z0-9]+", hit.title.lower()))
    body = set(re.findall(r"[a-z0-9]+", f"{hit.snippet} {hit.text[:1500]}".lower()))
    return sum(2 if w in title else 1 if w in body else 0 for w in words) / (2 * len(words))


def _matches(host: str, patterns: list[str]) -> bool:
    return any(host == p or host.endswith("." + p) or (p.startswith(".") and host.endswith(p)) for p in patterns)


class MultiSettings(ProviderSettings):
    providers: str = "wikipedia,ddgs"  # queried in this order; failures are skipped
    per_provider: int = 3  # results asked from each provider
    block: str = DEFAULT_BLOCK
    prefer: str = DEFAULT_PREFER
    prefer_bonus: float = 0.25  # added to the relevance (0-1) of results from preferred domains
    min_relevance: float = 0.15  # results sharing almost no words with the query are dropped


class MultiSearch(SearchProvider):
    name = "multi"
    env_prefix = "SEARCH_MULTI_"
    Settings = MultiSettings
    settings: MultiSettings
    remote = True

    def describe(self) -> str:
        return f"multi:{self.settings.providers}"

    def search(self, query: str, max_results: int) -> list[SearchResult]:
        if self.provider_factory is None:
            raise ProviderError("multi search needs the provider factory (created through Context)")
        s = self.settings
        block = [p.strip().lower() for p in s.block.split(",") if p.strip()]
        prefer = [p.strip().lower() for p in s.prefer.split(",") if p.strip()]
        found, errors, seen = [], [], set()
        for order, name in enumerate(n.strip() for n in s.providers.split(",") if n.strip()):
            try:
                hits = self.provider_factory(name).search(query, s.per_provider)
            except Exception as e:  # noqa: BLE001 - another provider may still answer
                errors.append(f"{name}: {e}")
                continue
            for k, h in enumerate(hits):
                host = _host(h.url)
                if not h.url or h.url in seen or _matches(host, block):
                    continue
                seen.add(h.url)
                rel = relevance(query, h)
                if rel < s.min_relevance:
                    continue
                score = rel + (s.prefer_bonus if _matches(host, prefer) else 0.0)
                found.append((-score, order, k, h))
        if not found:
            raise ProviderError(f"no usable results for {query!r} ({'; '.join(errors)[:300] or 'all blocked'})")
        return [h for *_, h in sorted(found, key=lambda x: x[:3])][:max_results]
