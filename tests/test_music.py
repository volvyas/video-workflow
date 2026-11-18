"""Music stage and providers."""

import json
from pathlib import Path

from shorts import pipeline
from shorts.comfy.client import ComfyClient
from shorts.stages.music import music_file
from test_pipeline_e2e import make_ctx, new_run


def test_folder_music_is_copied_into_the_run_and_used(tmp_path):
    ctx = make_ctx(tmp_path, review_enabled="false", image_qa_enabled="false")
    run = new_run(ctx)
    assert pipeline.execute(ctx, [run])[run.id] == "ok"
    assert music_file(run).name == "music.mp3"  # copied from MUSIC_DIR
    assert run.stage("music").notes["provider"] == "folder"


def test_no_music(tmp_path):
    ctx = make_ctx(tmp_path, review_enabled="false", image_qa_enabled="false", music_provider="none")
    run = new_run(ctx)
    assert pipeline.execute(ctx, [run])[run.id] == "ok"
    assert music_file(run) is None and run.final_video.exists()


def test_comfy_music_fills_the_workflow(tmp_path, monkeypatch):
    seen = {}

    def fake_run(self, wf, dest, kind="image"):
        seen["wf"], seen["kind"] = wf, kind
        out = Path(dest).with_suffix(".mp3")
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr(ComfyClient, "run", fake_run)
    wf = Path(__file__).parent.parent / "workflows" / "music_acestep15_turbo.json"
    ctx = make_ctx(tmp_path, music_provider="comfyui", music_comfy_workflow=str(wf))
    run = new_run(ctx)
    pipeline.execute(ctx, [run], ["script", "voice", "align", "music"])
    enc = seen["wf"]["94"]["inputs"]
    total = sum(t.slot for t in run.timings())
    assert seen["kind"] == "audio"
    assert enc["lyrics"] == "[Instrumental]" and enc["tags"].endswith("instrumental, no vocals")
    assert enc["keyscale"] == "A minor" and 60 <= enc["bpm"] <= 140
    assert abs(seen["wf"]["98"]["inputs"]["seconds"] - (total + 2)) < 0.02
    assert music_file(run).name == "music.mp3"


def test_script_schema_asks_for_music():
    from shorts.models import Script
    from shorts.providers.llm._schema import strict_schema

    music = strict_schema(Script)["properties"]["music"]
    assert set(music["required"]) == {"tags", "bpm", "key"} and "A minor" in music["properties"]["key"]["enum"]


def test_old_scripts_without_music_still_load(tmp_path):
    from shorts.models import Script

    s = Script.model_validate({"title": "t", "description": "d", "tags": [], "scenes": []})
    assert s.music.tags == "" and s.music.key == "A minor"
    assert json.loads(s.model_dump_json())["music"]["bpm"] == 90
