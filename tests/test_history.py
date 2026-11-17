"""Script history: statuses, similarity providers, repeat warnings and gates."""

import json

import httpx
import pytest

from shorts import history, pipeline
from shorts.models import Idea
from shorts.providers.similarity.lexical import tfidf_cosine
from shorts.run import Run
from shorts.stages import ideas
from test_pipeline_e2e import make_ctx

ABYSS = Idea(title="The ocean is darker than space", hook="Below 1000 metres there is no sunlight at all.",
             angle="The midnight zone and the animals that make their own light.", why_it_works="contrast")
ABYSS_AGAIN = Idea(title="Why the deep ocean is darker than outer space",
                   hook="Past 1000 metres sunlight never reaches the ocean.",
                   angle="Bioluminescent animals of the midnight zone.", why_it_works="contrast")
VOLCANO = Idea(title="A volcano taller than Everest", hook="Mauna Kea is 10 km tall from its base.",
               angle="Measuring mountains from the sea floor.", why_it_works="surprise")


def run_for(ctx, idea, topic="the deep ocean"):
    return Run.create(ctx.app.runs_dir, topic, idea, 1)


def text(i: Idea) -> str:
    return f"{i.title}. {i.hook} {i.angle}"


def test_lexical_separates_repeat_from_new_story():
    same, other = tfidf_cosine(text(ABYSS_AGAIN), [text(ABYSS), text(VOLCANO)])
    assert same >= 0.35 > other


def test_statuses_follow_the_pipeline_and_manual_marks_stick(tmp_path):
    ctx = make_ctx(tmp_path, review_enabled="false", image_qa_enabled="false")
    run = run_for(ctx, ABYSS)
    assert pipeline.execute(ctx, [run], ["script"])[run.id] == "ok"
    e = history.load(ctx.app).get(run.id)
    assert e.status == "draft" and e.narration.startswith("This is scene number 1")

    assert pipeline.execute(ctx, [run])[run.id] == "ok"
    assert history.load(ctx.app).get(run.id).status == "created"

    history.set_status(ctx.app, run.id, "popular", note="1M views")
    assert pipeline.execute(ctx, [run], force={"assemble"})[run.id] == "ok"  # automatic update...
    e = history.load(ctx.app).get(run.id)
    assert e.status == "popular"  # ...does not overwrite a manual status
    assert [(c.status, c.by) for c in e.log] == [("draft", "auto"), ("created", "auto"), ("popular", "human")]


def test_similar_idea_warns_by_default(tmp_path):
    ctx = make_ctx(tmp_path, review_enabled="false")
    first, second = run_for(ctx, ABYSS), run_for(ctx, ABYSS_AGAIN)
    assert pipeline.execute(ctx, [first], ["script"])[first.id] == "ok"
    assert pipeline.execute(ctx, [second], ["script"])[second.id] == "ok"
    notes = second.stage("script").notes["similar"]
    assert any(n.startswith("idea: ") and first.id in n for n in notes)


def test_similar_idea_fails_before_writing_when_configured(tmp_path):
    ctx = make_ctx(tmp_path, review_enabled="false", history_on_similar="fail")
    first, second, other = run_for(ctx, ABYSS), run_for(ctx, ABYSS_AGAIN), run_for(ctx, VOLCANO)
    pipeline.execute(ctx, [first], ["script"])
    status = pipeline.execute(ctx, [second, other], ["script"])
    assert status[second.id].startswith("script: idea is similar")
    assert not (second.path / "script.json").exists()  # stopped before the LLM wrote anything
    assert history.load(ctx.app).get(second.id).status == "auto_rejected"
    # a different idea passes the idea check; the mock LLM then writes the same narration as for the first run,
    # which the script check catches
    assert status[other.id].startswith("script: script is similar")
    assert (other.path / "script.json").exists()
    assert history.load(ctx.app).get(other.id).status == "auto_rejected"


def test_failed_review_is_auto_rejected_and_accept_restores_draft(tmp_path):
    from shorts.cli import _state

    ctx = make_ctx(tmp_path, llm_mock_review_score=3, review_max_rounds=1)
    run = run_for(ctx, VOLCANO)
    assert pipeline.execute(ctx, [run], ["script", "review"])[run.id].startswith("review:")
    e = history.load(ctx.app).get(run.id)
    assert e.status == "auto_rejected" and "scored 3" in e.log[-1].note

    from shorts.stages.review import accept

    accept(run)
    history.record(ctx, run, "review")
    assert history.load(ctx.app).get(run.id).status == "draft"
    _state.clear()


def test_new_ideas_similar_to_history_are_flagged_and_not_picked(tmp_path):
    ctx = make_ctx(tmp_path)
    ideas.generate_ideas(ctx, "space", 1)  # "Mock idea 1 about space"
    run = pipeline.create_runs(ctx, "space", ideas.take_unused(ctx.app, "space", 1))[0]
    pipeline.execute(ctx, [run], ["script"])
    new = ideas.generate_ideas(ctx, "space", 3)  # mock re-proposes idea 1 (deduped) and 2, 3: near-identical text
    assert new and all(i.similar_to for i in new)
    assert ideas.take_unused(ctx.app, "space", 5) == []


def test_ideas_prompt_contains_history(tmp_path):
    ctx = make_ctx(tmp_path)
    run = run_for(ctx, ABYSS)
    history.upsert_from_run(ctx.app, run)
    pop = run_for(ctx, VOLCANO, topic="geology")
    history.upsert_from_run(ctx.app, pop)
    history.set_status(ctx.app, pop.id, "popular")
    bad = run_for(ctx, ABYSS_AGAIN)
    history.upsert_from_run(ctx.app, bad)
    history.set_status(ctx.app, bad.id, "rejected", note="too much like a school lesson")
    p = history.prompt_context(ctx.app, "the deep ocean")
    assert "[draft] The ocean is darker than space" in p
    assert "rejected because: too much like a school lesson" in p
    assert "POPULAR" in p and "A volcano taller than Everest" in p


def test_llm_similarity_judges_the_closest_candidates(tmp_path):
    ctx = make_ctx(tmp_path, similarity_provider="llm", llm_mock_similarity_score=9, similarity_llm_candidates=1)
    for idea in (ABYSS, VOLCANO):
        history.upsert_from_run(ctx.app, run_for(ctx, idea))
    matches = history.find_similar(ctx, text(ABYSS_AGAIN))
    assert [m.entry.title for m in matches] == [ABYSS.title]  # volcano wasn't even sent to the LLM
    assert matches[0].score == pytest.approx(0.9)


def test_embeddings_similarity_with_cache(tmp_path):
    ctx = make_ctx(tmp_path, similarity_provider="embeddings",
                   similarity_embeddings_cache_file=str(tmp_path / "emb.json"))
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        inputs = json.loads(req.content)["input"]
        calls.append(len(inputs))
        vec = lambda t: [1.0, 0.0] if "dark" in t.lower() else [0.0, 1.0]  # noqa: E731
        return httpx.Response(200, json={"data": [{"index": i, "embedding": vec(t)} for i, t in enumerate(inputs)]})

    sim = ctx.similarity()
    sim._http = httpx.Client(base_url="http://x/v1", transport=httpx.MockTransport(handler))
    assert sim.scores("dark ocean", ["darker than space", "volcano"]) == pytest.approx([1.0, 0.0])
    assert sim.scores("dark ocean", ["volcano"]) == pytest.approx([0.0])
    assert calls == [3]  # second call fully served from the cache
    assert (tmp_path / "emb.json").exists()


def test_sync_backfills_existing_runs(tmp_path):
    ctx = make_ctx(tmp_path, review_enabled="false", history_enabled="false")
    run = run_for(ctx, ABYSS)
    pipeline.execute(ctx, [run])
    assert history.load(ctx.app).entries == []
    ctx2 = make_ctx(tmp_path)
    (e,) = history.sync(ctx2)
    assert e.id == run.id and e.status == "created"
