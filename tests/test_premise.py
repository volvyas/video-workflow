"""Idea premise fact-check before scripting."""

import json

from shorts import history, pipeline
from shorts.stages import ideas
from test_pipeline_e2e import make_ctx


def test_false_premise_ideas_are_not_picked(tmp_path):
    ctx = make_ctx(tmp_path, ideas_fact_check="true", llm_mock_premise_verdict="false", search_provider="mock")
    new = ideas.generate_ideas(ctx, "space", 2)
    assert all(i.premise.verdict == "false" for i in new)
    assert ideas.take_unused(ctx.app, "space", 5) == []


def test_run_from_false_idea_stops_before_the_script(tmp_path):
    ctx = make_ctx(tmp_path, ideas_fact_check="true", llm_mock_premise_verdict="false")
    (idea,) = ideas.generate_ideas(ctx, "space", 1)
    (run,) = pipeline.create_runs(ctx, "space", [idea])  # picked explicitly, like --idea
    status = pipeline.execute(ctx, [run], ["script"])
    assert "premise is false" in status[run.id] and not (run.path / "script.json").exists()
    assert json.loads((run.path / "premise.json").read_text())["verdict"] == "false"
    assert history.load(ctx.app).get(run.id).status == "auto_rejected"


def test_misleading_premise_is_corrected_in_the_script_prompt(tmp_path):
    ctx = make_ctx(tmp_path, ideas_fact_check="true", llm_mock_premise_verdict="misleading")
    (idea,) = ideas.generate_ideas(ctx, "space", 1)
    (run,) = pipeline.create_runs(ctx, "space", [idea])
    seen = []
    llm = ctx.llm("script")
    orig = llm.generate_json
    llm.generate_json = lambda **kw: (seen.append(kw["prompt"]), orig(**kw))[1]
    assert pipeline.execute(ctx, [run], ["script"])[run.id] == "ok"
    assert any("a corrected mock premise" in p for p in seen)


def test_disabled_by_default(tmp_path):
    ctx = make_ctx(tmp_path)
    (idea,) = ideas.generate_ideas(ctx, "space", 1)
    assert idea.premise is None
