"""`shorts` command line."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

import typer

from shorts.config import ConfigError, Env
from shorts.context import Context

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Automated YouTube Shorts pipeline.")
comfy_app = typer.Typer(no_args_is_help=True, help="ComfyUI workflow helpers.")
app.add_typer(comfy_app, name="comfy")

_state: dict = {}


def ctx() -> Context:
    if "ctx" not in _state:
        try:
            _state["ctx"] = Context(Env.load(_state.get("env_file")))
        except ConfigError as e:
            typer.secho(f"config error: {e}", fg="red", err=True)
            raise typer.Exit(2)
    return _state["ctx"]


@app.callback()
def main(
    env_file: Annotated[Optional[Path], typer.Option("--env-file", "-e", help="Use this env file instead of .env")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
):
    _state["env_file"] = env_file
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname).1s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "anthropic", "urllib3", "PIL", "googleapiclient"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _runs(paths: list[Path]) -> list:
    from shorts.run import Run

    out = []
    for p in paths:
        if not p.exists():
            p = ctx().app.runs_dir / p
        out.append(Run.open(p))
    return out


def _report(status: dict[str, str], runs) -> None:
    for r in runs:
        s = status.get(r.id, "skipped")
        if s == "ok":
            final = r.final_video
            typer.secho(f"✔ {r.id}" + (f"  → {final}" if final.exists() else ""), fg="green")
        else:
            typer.secho(f"✘ {r.id}: {s}", fg="red")


# ------------------------------------------------------------------ ideas
@app.command("ideas")
def ideas_cmd(topic: str, count: Annotated[int, typer.Option("--count", "-n")] = 10):
    """Generate new ideas for TOPIC and add them to the idea pool."""
    from shorts.stages import ideas

    new = ideas.generate_ideas(ctx(), topic, count)
    for i in new:
        typer.echo(f"{i.id}  {i.title}\n        hook: {i.hook}")
    typer.echo(f"\n{len(new)} new ideas → {ideas.pool_path(ctx().app, topic)}")


@app.command("list-ideas")
def list_ideas(topic: str, all_: Annotated[bool, typer.Option("--all", help="Include used ideas")] = False):
    """Show the idea pool for TOPIC."""
    from shorts.stages import ideas

    for i in ideas.load_pool(ctx().app, topic).ideas:
        if i.used_by and not all_:
            continue
        typer.echo(f"{i.id}  {'[used] ' if i.used_by else ''}{i.title}")


# ------------------------------------------------------------------ make / resume / redo
@app.command()
def make(
    topic: str,
    count: Annotated[int, typer.Option("--count", "-n", help="Number of videos")] = 1,
    idea: Annotated[Optional[list[str]], typer.Option("--idea", help="Use specific idea id(s)")] = None,
    until: Annotated[Optional[str], typer.Option(help="Stop after this stage (e.g. script)")] = None,
):
    """Make COUNT videos for TOPIC from unused ideas (generating more ideas if needed)."""
    from shorts import pipeline
    from shorts.stages import ideas

    c = ctx()
    if idea:
        picked = [ideas.find_idea(c.app, topic, i) for i in idea]
    else:
        picked = ideas.take_unused(c.app, topic, count)
        if len(picked) < count:
            ideas.generate_ideas(c, topic, max(count - len(picked), 5))
            picked = ideas.take_unused(c.app, topic, count)
    runs = pipeline.create_runs(c, topic, picked)
    stages = pipeline.BUILD_STAGES
    if until:
        stages = stages[: stages.index(until) + 1]
    _report(pipeline.execute(c, runs, stages), runs)


@app.command()
def resume(
    runs: list[Path],
    force: Annotated[Optional[list[str]], typer.Option("--force", "-f", help="Re-run this stage and its dependents")] = None,
    until: Annotated[Optional[str], typer.Option(help="Stop after this stage")] = None,
):
    """Continue unfinished run(s); --force STAGE regenerates a stage from scratch."""
    from shorts import pipeline

    rs = _runs(runs)
    stages = pipeline.BUILD_STAGES
    if until:
        stages = stages[: stages.index(until) + 1]
    _report(pipeline.execute(ctx(), rs, stages, force=set(force or [])), rs)


@app.command()
def redo(
    run: Path,
    stage: Annotated[str, typer.Option("--stage", "-s", help="voice | images | motion")],
    scene: Annotated[list[int], typer.Option("--scene", help="1-based scene number, repeatable")],
    new_seed: Annotated[bool, typer.Option(help="Use a new seed for the regenerated scenes")] = True,
):
    """Regenerate single scenes of a run, then re-assemble."""
    from shorts import pipeline

    if stage not in ("voice", "images", "motion"):
        raise typer.BadParameter("stage must be voice, images or motion")
    (r,) = _runs([run])
    if new_seed:
        r.manifest.seed = ctx().new_seed()
    only = {s - 1 for s in scene}
    chain = {"voice": ["voice", "align", "assemble"], "images": ["images", "motion", "assemble"],
             "motion": ["motion", "assemble"]}[stage]
    r.mark_pending(chain)
    _report(pipeline.execute(ctx(), [r], chain, only_scenes=only), [r])


@app.command()
def status(runs: Annotated[Optional[list[Path]], typer.Argument()] = None):
    """Show stage status of runs (default: the 15 most recent)."""
    from shorts.run import STAGES, Run

    c = ctx()
    if runs:
        rs = _runs(runs)
    else:
        dirs = sorted((p for p in Path(c.app.runs_dir).glob("*") if (p / "manifest.json").exists()), reverse=True)
        rs = [Run.open(p) for p in dirs[:15]]
    icon = {"done": "✔", "failed": "✘", "running": "…", "pending": "·"}
    for r in rs:
        cells = " ".join(f"{s}:{icon.get(r.stage(s).status, '?')}" for s in STAGES)
        typer.echo(f"{r.id:60s} {cells}")
        for s in STAGES:
            if r.stage(s).error:
                typer.secho(f"    {s}: {r.stage(s).error}", fg="red")
        if r.manifest.upload_url:
            typer.echo(f"    uploaded: {r.manifest.upload_url}")


@app.command()
def upload(
    run: Path,
    privacy: Annotated[Optional[str], typer.Option(help="private | unlisted | public")] = None,
    publish_at: Annotated[Optional[str], typer.Option(help="Schedule, e.g. 2026-11-20T17:00:00+02:00")] = None,
):
    """Upload a finished run with the configured uploader."""
    from shorts.models import VideoMeta

    c = ctx()
    (r,) = _runs([run])
    if not r.final_video.exists():
        raise typer.BadParameter(f"{r.id} has no final.mp4 yet")
    s = r.script()
    default_privacy = getattr(c.uploader().settings, "privacy", "private")
    if publish_at:
        publish_at = datetime.fromisoformat(publish_at).isoformat()
    meta = VideoMeta(title=s.title, description=s.description, tags=s.tags,
                     privacy=privacy or default_privacy, publish_at=publish_at)
    r.start("upload", c.app.uploader)
    try:
        url = c.uploader().upload(r.final_video, meta)
    except Exception as e:
        r.fail("upload", str(e))
        raise
    r.manifest.upload_url = url
    r.finish("upload", url=url)
    typer.secho(f"uploaded: {url}", fg="green")


# ------------------------------------------------------------------ diagnostics
@app.command()
def providers():
    """List built-in providers and the current selection."""
    from shorts.registry import BUILTINS

    a = ctx().app
    selected = {"llm": a.llm_provider, "tts": a.tts_provider, "align": a.align_provider,
                "image": a.image_provider, "motion": f"{a.motion_provider} (fallback: {a.motion_fallback})",
                "upload": a.uploader}
    for kind, names in BUILTINS.items():
        typer.echo(f"{kind:7s} selected={selected[kind]:<32s} available: {', '.join(names)}")


@app.command()
def check(
    skip: Annotated[Optional[list[str]], typer.Option(help="Provider kinds to skip, e.g. --skip upload")] = None,
):
    """Instantiate every configured provider to validate settings and dependencies."""
    c = ctx()
    a = c.app
    targets = [("llm", a.llm_for("ideas")), ("llm", a.llm_for("script")), ("tts", a.tts_provider),
               ("align", a.align_provider), ("image", a.image_provider), ("motion", a.motion_provider),
               ("motion", a.motion_fallback), ("upload", a.uploader)]
    ok = True
    for kind, name in dict.fromkeys(t for t in targets if t[1] and t[1] != "none" or t[0] == "upload"):
        if skip and kind in skip:
            continue
        try:
            p = c.provider(kind, name)
            p.verify()
            typer.secho(f"✔ {kind:7s} {p.describe()}", fg="green")
        except Exception as e:  # noqa: BLE001
            ok = False
            typer.secho(f"✘ {kind:7s} {name}: {e}", fg="red")
    if "comfyui" in (a.image_provider, a.tts_provider) or "comfyui_i2v" in (a.motion_provider, a.motion_fallback):
        from shorts.comfy.client import ComfyClient

        try:
            stats = ComfyClient(a.comfy_url).ping()
            dev = (stats.get("devices") or [{}])[0]
            typer.secho(f"✔ comfyui {a.comfy_url}  {dev.get('name', '')}", fg="green")
        except Exception as e:  # noqa: BLE001
            ok = False
            typer.secho(f"✘ comfyui {e}", fg="red")
    from shorts.media import ffmpeg

    try:
        typer.secho(f"✔ ffmpeg  {ffmpeg.ffmpeg_bin()}", fg="green")
    except Exception as e:  # noqa: BLE001
        ok = False
        typer.secho(f"✘ ffmpeg  {e}", fg="red")
    raise typer.Exit(0 if ok else 1)


@app.command("llm-test")
def llm_test(topic: str = "the deep ocean", stage: str = "ideas"):
    """Ask the configured LLM for 3 ideas (quick connectivity + quality check)."""
    from shorts.models import IdeaBatch
    from shorts.prompts import render

    c = ctx()
    a = c.app
    system = render("ideas_system", a.prompts_dir, channel_brief=a.channel_brief,
                    seconds=a.video_target_seconds, language=a.content_language)
    prompt = render("ideas_user", a.prompts_dir, topic=topic, count=3, existing="(none)")
    out = c.llm(stage).generate_json(system=system, prompt=prompt, schema=IdeaBatch)
    typer.echo(out.model_dump_json(indent=2))


# ------------------------------------------------------------------ comfy helpers
@comfy_app.command("inspect")
def comfy_inspect(workflow: Path):
    """List nodes, their literal inputs and the placeholders of an API-format workflow."""
    from shorts.comfy import template

    wf = template.load(workflow)
    for line in template.describe(wf):
        typer.echo(line)
    typer.echo(f"\nplaceholders: {sorted(template.placeholders(wf)) or 'none'}")


@comfy_app.command("mark")
def comfy_mark(
    workflow: Path,
    assignments: Annotated[list[str], typer.Argument(help="NODE.INPUT=placeholder, e.g. 6.text=prompt 3.seed=seed")],
    output: Annotated[Optional[Path], typer.Option("--output", "-o", help="Default: overwrite input")] = None,
):
    """Write {{placeholders}} into a workflow exported from ComfyUI."""
    from shorts.comfy import template

    wf = template.mark(template.load(workflow), assignments)
    dest = output or workflow
    dest.write_text(json.dumps(wf, indent=2, ensure_ascii=False), encoding="utf-8")
    typer.echo(f"wrote {dest}; placeholders: {sorted(template.placeholders(wf))}")


@comfy_app.command("test-image")
def comfy_test_image(prompt: str, out: Path = Path("comfy_test.png")):
    """Generate one image with the configured image provider."""
    c = ctx()
    a = c.app
    c.image().generate(prompt=f"{prompt}, {a.style_prompt}", negative=a.negative_prompt, width=a.image_width,
                       height=a.image_height, seed=c.new_seed(), out_path=out)
    typer.echo(f"wrote {out}")


@comfy_app.command("test-motion")
def comfy_test_motion(image: Path, prompt: str = "slow cinematic push-in", seconds: float = 4.0,
                      out: Path = Path("motion_test.mp4"), camera: str = "zoom_in"):
    """Animate one image with the configured motion provider."""
    c = ctx()
    got = c.motion().animate(image=image, prompt=prompt, camera=camera, duration=seconds, seed=c.new_seed(),
                             out_path=out)
    typer.echo(f"wrote {got}")


if __name__ == "__main__":
    app()
