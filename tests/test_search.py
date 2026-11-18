"""Search providers (wikipedia, multi), the search cache, and visible search failures."""

import json

import httpx

from shorts import pipeline, storyboard
from shorts.providers.base import ProviderError, SearchProvider, SearchResult
from shorts.providers.search.wikipedia import excerpt
from shorts.stages.research import research_report
from test_pipeline_e2e import make_ctx, new_run

CALLS: list[str] = []


class FailingSearch(SearchProvider):
    name = "failing"

    def search(self, query, max_results):
        CALLS.append(query)
        raise ProviderError("no search results (rate limited)")


class FarmSearch(SearchProvider):
    name = "farm"

    def search(self, query, max_results):
        CALLS.append(query)
        return [SearchResult(title="myth", url="https://iere.org/x", snippet="trench bones shatter"),
                SearchResult(title="Trench blog", url="https://someblog.com/a", snippet="trench pressure blog"),
                SearchResult(title="Ocean trench facts", url="https://oceanservice.noaa.gov/facts",
                             snippet="trench pressure"),
                SearchResult(title="Unrelated", url="https://other.org/b", snippet="cooking recipes")]


class WikiStub(SearchProvider):
    name = "wikistub"

    def search(self, query, max_results):
        CALLS.append(query)
        return [SearchResult(title="Challenger Deep", url="https://en.wikipedia.org/wiki/Challenger_Deep",
                             snippet="deepest", text="The Challenger Deep is the deepest known point of the trench."),
                SearchResult(title="Subduction", url="https://en.wikipedia.org/wiki/Subduction",
                             snippet="plates", text="Tectonic plates move.")]


def test_excerpt_keeps_lead_and_relevant_paragraphs():
    text = "\n".join(["Lead paragraph about the trench and its location in the Pacific Ocean today.",
                      "History section about expeditions and the people who went there, long ago.",
                      "Pressure at the bottom is about 1,086 bar, more than a thousand atmospheres of pressure."])
    out = excerpt(text, "trench pressure atmospheres", 170)
    assert out.startswith("Lead paragraph") and "1,086 bar" in out and "History" not in out


def test_wikipedia_provider_parses_the_api(tmp_path, monkeypatch):
    from shorts.providers.search import wikipedia

    def fake_get(url, **kw):
        assert kw["params"]["gsrsearch"] == "mariana pressure" and "shorts-pipeline" in kw["headers"]["User-Agent"]
        return httpx.Response(200, request=httpx.Request("GET", url), json={"query": {"pages": [
            {"index": 2, "title": "Deep sea", "fullurl": "https://en.wikipedia.org/wiki/Deep_sea", "extract": "B " * 40},
            {"index": 1, "title": "Challenger Deep", "fullurl": "https://en.wikipedia.org/wiki/Challenger_Deep",
             "extract": "The Challenger Deep is the deepest point of the Mariana Trench, under great pressure."}]}})

    monkeypatch.setattr(wikipedia.httpx, "get", fake_get)
    ctx = make_ctx(tmp_path, search_provider="wikipedia")
    res = ctx.search().search("mariana pressure", 2)
    assert [r.title for r in res] == ["Challenger Deep", "Deep sea"] and "deepest point" in res[0].text


def test_multi_blocks_farms_and_ranks_reliable_sources(tmp_path):
    ctx = make_ctx(tmp_path, search_provider="multi",
                   search_multi_providers="test_search:FailingSearch,test_search:FarmSearch,test_search:WikiStub")
    urls = [r.url for r in ctx.search().search("trench pressure", 6)]
    assert "https://iere.org/x" not in urls  # blocked content farm
    assert "https://other.org/b" not in urls and "https://en.wikipedia.org/wiki/Subduction" not in urls  # off-topic
    assert urls[0] == "https://oceanservice.noaa.gov/facts"  # relevant + reliable first
    assert urls.index("https://someblog.com/a") > urls.index("https://oceanservice.noaa.gov/facts")


def test_search_cache_avoids_repeat_requests(tmp_path):
    CALLS.clear()
    ctx = make_ctx(tmp_path, search_provider="test_search:WikiStub", search_cache_file=str(tmp_path / "c.json"),
                   llm_mock_web_search="false",
                   review_search_queries=2)
    research_report(ctx, "ocean", "The Challenger Deep is deep.")
    n = len(CALLS)
    text, stats = research_report(ctx, "ocean", "The Challenger Deep is deep.")
    assert len(CALLS) == n and stats.sources == 2 and "Article excerpt" in text


def test_failed_search_is_recorded_and_shown(tmp_path):
    ctx = make_ctx(tmp_path, search_provider="test_search:FailingSearch", llm_mock_web_search="false",
                   storyboard_approval="true", image_qa_enabled="false", llm_mock_scenes=2,
                   search_cache_file=str(tmp_path / "c.json"))
    run = new_run(ctx)
    seen = []
    llm = ctx.llm("review")
    orig = llm.generate_json
    llm.generate_json = lambda **kw: (seen.append(kw["prompt"]), orig(**kw))[1]
    pipeline.execute(ctx, [run])
    rnd = json.loads((run.path / "review.json").read_text())[0]
    assert rnd["search"]["sources"] == 0 and rnd["search"]["failed"] == rnd["search"]["queries"] > 0
    assert "Web search failed" in rnd["search"]["warning"]
    assert any("WEB SEARCH FAILED" in p for p in seen)  # the judge is told
    assert "Web search failed" in storyboard.summary(run)["review"]["search_warning"]


def test_failed_search_marks_premise_checks(tmp_path):
    from shorts.stages import ideas

    ctx = make_ctx(tmp_path, ideas_fact_check="true", search_provider="test_search:FailingSearch",
                   llm_mock_web_search="false", search_cache_file=str(tmp_path / "c.json"))
    (idea,) = ideas.generate_ideas(ctx, "space", 1)
    assert idea.premise.explanation.startswith("[no web results")
