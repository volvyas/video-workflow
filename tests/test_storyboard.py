"""Storyboard approval gate, scene edits, animatic preview and the web API."""

import json
import threading
import time

import httpx

from shorts import history, pipeline, storyboard
from shorts.run import Run
from test_pipeline_e2e import make_ctx, new_run

FAST = dict(review_enabled="false", image_qa_enabled="false", llm_mock_scenes=2)


def test_gate_stops_before_motion_until_approved(tmp_path):
    ctx = make_ctx(tmp_path, storyboard_approval="true", **FAST)
    run = new_run(ctx)
    status = pipeline.execute(ctx, [run])
    assert status[run.id].startswith("waiting")
    assert run.stage("approve").status == "waiting" and run.is_done("images")
    assert not (run.path / "clips").exists() and not run.final_video.exists()
    assert storyboard.state(run) == "waiting"

    assert pipeline.execute(ctx, [run])[run.id].startswith("waiting")  # resume alone doesn't pass the gate
    storyboard.approve(run)
    assert pipeline.execute(ctx, [run])[run.id] == "ok"
    assert run.final_video.exists() and storyboard.state(run) == "done"


def test_gate_is_automatic_when_disabled(tmp_path):
    ctx = make_ctx(tmp_path, **FAST)
    run = new_run(ctx)
    assert pipeline.execute(ctx, [run])[run.id] == "ok"
    assert run.stage("approve").notes["by"] == "auto"


def test_reroll_pick_and_motion_choice(tmp_path):
    ctx = make_ctx(tmp_path, storyboard_approval="true", **FAST)
    run = new_run(ctx)
    pipeline.execute(ctx, [run])
    first = (run.path / "images" / "scene_01.png").read_bytes()

    storyboard.reroll(ctx, run, 0, prompt="a red lighthouse on a cliff at dawn", seed=123)
    assert run.script().scenes[0].image_prompt == "a red lighthouse on a cliff at dawn"
    (cand,) = storyboard.candidates(run, 0)
    assert cand.read_bytes() == first  # the old image is kept as a candidate
    assert (run.path / "images" / "scene_01.png").read_bytes() != first

    storyboard.pick(run, 0, cand.name)
    assert (run.path / "images" / "scene_01.png").read_bytes() == first
    assert len(storyboard.candidates(run, 0)) == 1  # the re-rolled one became the candidate

    storyboard.set_motion(run, 1, "still")
    storyboard.approve(run)
    assert pipeline.execute(ctx, [run])[run.id] == "ok"
    used = run.stage("motion").notes["providers"]
    assert used["1"].startswith("mock") and used["2"].startswith("kenburns")


def test_editing_after_approval_needs_a_new_approval(tmp_path):
    ctx = make_ctx(tmp_path, storyboard_approval="true", **FAST)
    run = new_run(ctx)
    pipeline.execute(ctx, [run])
    storyboard.approve(run)
    pipeline.execute(ctx, [run])
    storyboard.reroll(ctx, run, 1)
    assert run.stage("approve").status == "pending" and not run.is_done("assemble")
    assert not (run.path / "clips" / "scene_02.mp4").exists() and (run.path / "clips" / "scene_01.mp4").exists()


def test_preview_and_reject(tmp_path):
    ctx = make_ctx(tmp_path, storyboard_approval="true", **FAST)
    run = new_run(ctx)
    pipeline.execute(ctx, [run])
    out = storyboard.build_preview(ctx, run)
    assert out == storyboard.preview_path(run) and out.exists() and not run.final_video.exists()
    s = storyboard.summary(run)
    assert s["preview"] == "preview/preview.mp4" and len(s["scenes"]) == 2 and s["scenes"][0]["audio"]

    storyboard.reject(ctx, run, "boring hook")
    assert storyboard.state(run) == "rejected"
    e = history.load(ctx.app).get(run.id)
    assert e.status == "rejected" and e.log[-1].note == "boring hook"


def _env_file(tmp_path, **extra) -> str:
    ctx = make_ctx(tmp_path, **extra)  # builds music/sfx assets
    keys = {k: v for k, v in ctx.env.values.items() if k.isupper() and not k.startswith(("PATH", "HOME"))}
    p = tmp_path / "test.env"
    p.write_text("".join(f"{k}={v}\n" for k, v in keys.items() if k in {
        "LLM_PROVIDER", "TTS_PROVIDER", "ALIGN_PROVIDER", "IMAGE_PROVIDER", "MOTION_PROVIDER", "MOTION_FALLBACK",
        "LLM_MOCK_SCENES", "VIDEO_WIDTH", "VIDEO_HEIGHT", "IMAGE_WIDTH", "IMAGE_HEIGHT", "RUNS_DIR", "IDEAS_DIR",
        "MUSIC_DIR", "SFX_DIR", "CAPTION_FONT_SIZE", "SEED", "HISTORY_FILE", "STORYBOARD_APPROVAL",
        "REVIEW_ENABLED", "IMAGE_QA_ENABLED"}))
    return str(p)


def test_web_api(tmp_path, monkeypatch):
    from shorts.storyboard_server import serve

    for k in ("STYLE_PROFILE", "STYLE_PROMPT"):
        monkeypatch.delenv(k, raising=False)
    ctx = make_ctx(tmp_path, storyboard_approval="true", **FAST)
    run = new_run(ctx)
    pipeline.execute(ctx, [run])
    httpd = serve("127.0.0.1", 0, _env_file(tmp_path, storyboard_approval="true", **FAST))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        c = httpx.Client(base_url=base, timeout=30)
        assert "Storyboard" in c.get("/").text
        (listed,) = c.get("/api/runs").json()
        assert listed["id"] == run.id and listed["state"] == "waiting"
        d = c.get(f"/api/run/{run.id}").json()
        assert d["scenes"][0]["image"] == "images/scene_01.png"
        img = c.get(f"/files/{run.id}/images/scene_01.png")
        assert img.status_code == 200 and img.headers["content-type"] == "image/png"
        part = c.get(f"/files/{run.id}/images/scene_01.png", headers={"Range": "bytes=0-9"})
        assert part.status_code == 206 and len(part.content) == 10
        assert c.get(f"/files/{run.id}/../../test.env").status_code == 404  # no escaping the run folder

        job = c.post(f"/api/run/{run.id}/scene/1/reroll", json={"prompt": "a lighthouse"}).json()
        for _ in range(100):
            j = next(x for x in c.get("/api/jobs").json() if x["id"] == job["id"])
            if j["status"] in ("done", "failed"):
                break
            time.sleep(0.1)
        assert j["status"] == "done", j
        assert c.get(f"/api/run/{run.id}").json()["scenes"][0]["image_prompt"] == "a lighthouse"

        assert c.post(f"/api/run/{run.id}/scene/2/motion", json={"mode": "still"}).json() == {"ok": True}
        assert c.post(f"/api/run/{run.id}/scene/9/reroll", json={}).status_code == 400
        assert c.post(f"/api/run/{run.id}/approve", json={"render": False}).json() == {"ok": True}
        r = Run.open(run.path)
        assert r.is_done("approve") and r.stage("approve").notes["motion"] == {"2": "still"}
        assert json.loads((run.path / "script.json").read_text())["scenes"][0]["image_prompt"] == "a lighthouse"
    finally:
        httpd.shutdown()
