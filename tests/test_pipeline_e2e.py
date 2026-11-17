"""End-to-end pipeline runs with mock providers (small frame size to keep it fast)."""

import json

import pytest

from shorts import pipeline
from shorts.config import Env
from shorts.context import Context
from shorts.media import ffmpeg
from shorts.stages import ideas
from shorts.stages.review import accept


def make_ctx(tmp_path, **extra) -> Context:
    music, sfx = tmp_path / "music", tmp_path / "sfx"
    music.mkdir(exist_ok=True)
    sfx.mkdir(exist_ok=True)
    ffmpeg.run(["-f", "lavfi", "-i", "sine=frequency=330:duration=3", music / "m.mp3"])
    ffmpeg.run(["-f", "lavfi", "-i", "sine=frequency=900:duration=0.3", sfx / "whoosh.wav"])
    values = {
        "LLM_PROVIDER": "mock", "TTS_PROVIDER": "mock", "ALIGN_PROVIDER": "proportional",
        "IMAGE_PROVIDER": "mock", "MOTION_PROVIDER": "mock", "MOTION_FALLBACK": "kenburns",
        "LLM_MOCK_SCENES": "3", "VIDEO_WIDTH": "216", "VIDEO_HEIGHT": "384", "IMAGE_WIDTH": "216",
        "IMAGE_HEIGHT": "384", "RUNS_DIR": str(tmp_path / "runs"), "IDEAS_DIR": str(tmp_path / "ideas"), "HISTORY_FILE": str(tmp_path / "history.json"),
        "MUSIC_DIR": str(music), "SFX_DIR": str(sfx), "CAPTION_FONT_SIZE": "20", "SEED": "1",
    }
    values.update({k.upper(): str(v) for k, v in extra.items()})
    return Context(Env(values))


def new_run(ctx):
    ideas.generate_ideas(ctx, "space", 1)
    return pipeline.create_runs(ctx, "space", ideas.take_unused(ctx.app, "space", 1))[0]


def test_full_pipeline_with_review_and_qa(tmp_path):
    ctx = make_ctx(tmp_path)
    run = new_run(ctx)
    status = pipeline.execute(ctx, [run])
    assert status[run.id] == "ok", status
    assert run.final_video.exists()
    assert (run.path / "script.original.json").exists()
    rounds = json.loads((run.path / "review.json").read_text())
    assert rounds[0]["research"].startswith("Mock research")  # web-search step ran
    assert run.stage("review").notes["score"] == 8
    assert set(run.stage("images").notes["qa"]) == {"1", "2", "3"}
    assert "visual_style" in json.loads((run.path / "script.json").read_text())
    assert ffmpeg.duration(run.final_video) == pytest.approx(sum(t.slot for t in run.timings()), abs=0.15)


def test_low_review_score_stops_until_accepted(tmp_path):
    ctx = make_ctx(tmp_path, llm_mock_review_score=4, review_max_rounds=2)
    run = new_run(ctx)
    status = pipeline.execute(ctx, [run])
    assert status[run.id].startswith("review:")
    rounds = json.loads((run.path / "review.json").read_text())
    assert len(rounds) == 2  # judged, rewritten by the script writer, judged again
    assert all("script" in r and r["words"] > 0 for r in rounds)
    assert "revised" not in rounds[0]["review"]  # the reviewer only judges
    assert not run.final_video.exists()

    accept(run)
    assert pipeline.execute(ctx, [run])[run.id] == "ok"
    assert run.final_video.exists()


def test_bad_images_are_rerolled(tmp_path):
    ctx = make_ctx(tmp_path, llm_mock_image_score=3, image_qa_max_retries=2, llm_mock_scenes=1)
    run = new_run(ctx)
    assert pipeline.execute(ctx, [run], ["script", "review", "images"])[run.id] == "ok"
    tries = run.stage("images").notes["qa"]["1"]
    assert [t["try"] for t in tries] == [1, 2, 3]
    assert (run.path / "images" / "scene_01.png").exists()
    assert len(list((run.path / "images" / "candidates").glob("*.png"))) == 2


def test_review_can_be_disabled(tmp_path):
    ctx = make_ctx(tmp_path, review_enabled="false", image_qa_enabled="false")
    run = new_run(ctx)
    assert pipeline.execute(ctx, [run], ["script", "review", "images"])[run.id] == "ok"
    assert not (run.path / "review.json").exists()
    assert "qa" not in run.stage("images").notes or not run.stage("images").notes["qa"]


def test_local_llm_review_uses_search_provider(tmp_path, monkeypatch):
    from shorts.stages import research

    monkeypatch.setattr(research, "page_text", lambda url, limit=4000: f"page text of {url}")
    ctx = make_ctx(tmp_path, llm_mock_web_search="false", search_provider="mock", review_fetch_pages=2,
                   review_search_results=2)
    run = new_run(ctx)
    assert pipeline.execute(ctx, [run], ["script", "review"])[run.id] == "ok"
    notes = json.loads((run.path / "review.json").read_text())[0]["research"]
    assert "## Query: mock claim one" in notes and "## Query: mock claim two" in notes
    assert notes.count("Page excerpt:") == 2


def test_images_batch_unloads_between_rounds_for_local_llm(tmp_path):
    ctx = make_ctx(tmp_path, llm_mock_image_score=3, image_qa_max_retries=1, llm_mock_scenes=2)
    unloads = []
    orig = ctx.unload
    ctx.unload = lambda kind=None: (unloads.append(kind), orig(kind))[1]
    ideas.generate_ideas(ctx, "space", 2)
    runs = pipeline.create_runs(ctx, "space", ideas.take_unused(ctx.app, "space", 2))
    status = pipeline.execute(ctx, runs, ["script", "review", "images"])
    assert all(v == "ok" for v in status.values()), status
    # round 1 gen → free image → QA → free llm → round 2 gen → free image → QA ; then stage-end unloads
    seq = [u for u in unloads if u in ("image", "llm")]
    assert seq[:4] == ["llm", "image", "llm", "image"]  # (first "llm" is the end of the review stage)
    for r in runs:
        assert [t["try"] for t in r.stage("images").notes["qa"]["1"]] == [1, 2]


def test_review_keeps_best_round_when_rewrite_is_worse(tmp_path):
    from shorts.models import ClaimCheck, ScriptReview

    ctx = make_ctx(tmp_path, review_max_rounds=2, review_on_low_score="continue")
    run = new_run(ctx)
    llm = ctx.llm("review")
    orig = llm.generate_json
    reviews = iter([
        ScriptReview(claims=[ClaimCheck(claim="a", verdict="incorrect", correction="b", source="s")], score=7,
                     hook_score=8, problems=["x"]),
        ScriptReview(claims=[ClaimCheck(claim="a", verdict="incorrect", correction="b", source="s")] * 2, score=4,
                     hook_score=7, problems=["y"]),
    ])
    llm.generate_json = lambda **kw: next(reviews) if kw["schema"] is ScriptReview else orig(**kw)
    assert pipeline.execute(ctx, [run], ["script", "review"])[run.id] == "ok"
    rounds = json.loads((run.path / "review.json").read_text())
    assert len(rounds) == 2
    assert run.stage("review").notes["kept_round"] == 1 and run.stage("review").notes["score"] == 7
    assert json.loads((run.path / "script.json").read_text()) == rounds[0]["script"]
