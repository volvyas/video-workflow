"""Style profiles: styles/<name>.env layered between .env and real environment variables."""

import pytest

from shorts.config import ConfigError, Env


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for k in ("STYLE_PROFILE", "STYLE_PROMPT", "CAPTION_FONT", "STYLES_DIR", "SHORTS_ENV_FILE"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles" / "comic.env").write_text("STYLE_PROMPT=comic panel\nCAPTION_FONT=Bangers\n")
    (tmp_path / "styles" / "cinematic.env").write_text("STYLE_PROMPT=35mm photo\n")
    (tmp_path / ".env").write_text("STYLE_PROFILE=comic\nSTYLE_PROMPT=from dotenv\nCAPTION_FONT=Montserrat\n"
                                   "VIDEO_TARGET_SECONDS=30\n")
    return tmp_path


def test_profile_overrides_dotenv(project):
    a = Env.load().app
    assert a.style_profile == "comic"
    assert a.style_prompt == "comic panel" and a.caption_font == "Bangers"
    assert a.video_target_seconds == 30  # keys the profile doesn't set still come from .env


def test_explicit_style_and_real_env_win(project, monkeypatch):
    assert Env.load(style="cinematic").app.style_prompt == "35mm photo"
    monkeypatch.setenv("STYLE_PROMPT", "from shell")
    assert Env.load().app.style_prompt == "from shell"


def test_unknown_profile_lists_available(project):
    with pytest.raises(ConfigError, match="available: cinematic, comic"):
        Env.load(style="anime")


def test_runs_record_their_style(tmp_path):
    from shorts import pipeline
    from shorts.context import Context
    from shorts.stages import ideas

    ctx = Context(Env({"LLM_PROVIDER": "mock", "STYLE_PROFILE": "comic", "RUNS_DIR": str(tmp_path / "runs"),
                       "IDEAS_DIR": str(tmp_path / "ideas"), "HISTORY_FILE": str(tmp_path / "h.json")}))
    ideas.generate_ideas(ctx, "space", 1)
    (run,) = pipeline.create_runs(ctx, "space", ideas.take_unused(ctx.app, "space", 1))
    assert run.manifest.style_profile == "comic"


def test_script_prompt_carries_fixed_style(tmp_path):
    from shorts.context import Context
    from shorts.stages.script import script_system_prompt

    ctx = Context(Env({"STYLE_PROMPT": "comic panel", "STYLE_GUIDE": "Think in panels."}))
    p = script_system_prompt(ctx)
    assert 'visual style is FIXED: "comic panel"' in p and "Think in panels." in p


def test_shipped_profiles_only_use_known_keys():
    from pathlib import Path

    from dotenv import dotenv_values

    from shorts.config import AppSettings
    from shorts.registry import BUILTINS, resolve

    prefixes = {resolve(k, n).env_prefix for k, names in BUILTINS.items() for n in names}
    files = sorted(Path(__file__).parent.parent.joinpath("styles").glob("*.env"))
    assert {f.stem for f in files} >= {"cinematic", "comic", "cartoon", "animated3d"}
    for f in files:
        for key in dotenv_values(f):
            assert key.lower() in AppSettings.model_fields or any(p and key.startswith(p) for p in prefixes), \
                f"{f.name}: unknown key {key}"
