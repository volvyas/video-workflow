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
history_app = typer.Typer(no_args_is_help=True, help="History of produced scripts and their statuses.")
app.add_typer(history_app, name="history")

_state: dict = {}


def ctx() -> Context:
    if "ctx" not in _state:
        try:
            _state["ctx"] = Context(Env.load(_state.get("env_file"), _state.get("style")))
        except ConfigError as e:
            typer.secho(f"config error: {e}", fg="red", err=True)
            raise typer.Exit(2)
    return _state["ctx"]


@app.callback()
def main(
    env_file: Annotated[Optional[Path], typer.Option("--env-file", "-e", help="Use this env file instead of .env")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    style: Annotated[Optional[str], typer.Option("--style", help="Style profile (styles/<name>.env), overrides "
                                                 "STYLE_PROFILE; `shorts styles` lists them")] = None,
):
    _state["env_file"] = env_file
    _state["style"] = style
    _state["style_explicit"] = style is not None
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
    _adopt_run_style(out)
    return out


def _adopt_run_style(runs: list) -> None:
    """Existing runs keep the style they were made with, unless --style was given explicitly."""
    styles = {r.manifest.style_profile for r in runs if r.manifest.style_profile}
    if not styles or _state.get("style_explicit"):
        return
    if len(styles) > 1:
        raise typer.BadParameter(f"these runs use different style profiles ({', '.join(sorted(styles))}); "
                                 "run them separately or pass --style")
    (style,) = styles
    if style != ctx().app.style_profile:
        typer.secho(f"using the run's style profile '{style}' (current setting: {ctx().app.style_profile})",
                    fg="yellow")
        _state["style"] = style
        _state.pop("ctx", None)


def _report(status: dict[str, str], runs) -> None:
    for r in runs:
        s = status.get(r.id, "skipped")
        if s == "ok":
            final = r.final_video
            typer.secho(f"✔ {r.id}" + (f"  → {final}" if final.exists() else ""), fg="green")
        elif s.startswith("waiting"):
            typer.secho(f"⏸ {r.id}: storyboard ready, approve it on `shorts storyboard` or `shorts approve {r.id}`",
                        fg="yellow")
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
        for s in i.similar_to:
            typer.secho(f"        ⚠ similar to {s} (not picked automatically; use --idea {i.id} to make it anyway)",
                        fg="yellow")
    typer.echo(f"\n{len(new)} new ideas → {ideas.pool_path(ctx().app, topic)}")


@app.command("list-ideas")
def list_ideas(topic: str, all_: Annotated[bool, typer.Option("--all", help="Include used ideas")] = False):
    """Show the idea pool for TOPIC."""
    from shorts.stages import ideas

    for i in ideas.load_pool(ctx().app, topic).ideas:
        if i.used_by and not all_:
            continue
        typer.echo(f"{i.id}  {'[used] ' if i.used_by else ''}{i.title}")
        for s in i.similar_to:
            typer.secho(f"        ⚠ similar to {s}", fg="yellow")


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
def accept(runs: list[Path]):
    """Accept the current script.json of run(s) after a failed/low-score review, so `resume` continues."""
    from shorts.stages.review import accept as accept_review

    from shorts import history

    for r in _runs(runs):
        accept_review(r)
        history.record(ctx(), r, "review")
        typer.echo(f"accepted script of {r.id}; now run: shorts resume {r.id}")


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
        for note in r.stage("script").notes.get("similar", []):
            typer.secho(f"    ⚠ {note}", fg="yellow")
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
    from shorts import history

    history.record(c, r, "upload")
    typer.secho(f"uploaded: {url}", fg="green")


# ------------------------------------------------------------------ storyboard approval
@app.command("storyboard")
def storyboard_cmd(
    host: Annotated[str, typer.Option(help="0.0.0.0 to open it from your phone on the home network")] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8765,
):
    """Web page to review script, images, voice and music, then approve (renders motion) or reject runs."""
    from shorts.storyboard_server import serve

    httpd = serve(host, port, _state.get("env_file"))
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    typer.secho(f"storyboard: http://{shown}:{port}/   (Ctrl+C to stop)", fg="green")
    if host == "0.0.0.0":
        typer.secho("  reachable from your network, without a password: use it on a trusted home network only",
                    fg="yellow")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


@app.command("approve")
def approve_cmd(runs: list[Path], render: Annotated[bool, typer.Option(help="Continue with motion + assembly")] = True):
    """Approve the storyboard of run(s) from the terminal; renders them unless --no-render."""
    from shorts import pipeline, storyboard

    rs = _runs(runs)
    for r in rs:
        storyboard.approve(r, by="human (cli)")
        typer.echo(f"approved {r.id}")
    if render:
        _report(pipeline.execute(ctx(), rs), rs)


@app.command("reject")
def reject_cmd(runs: list[Path], note: Annotated[str, typer.Option(help="Why (goes into the history)")] = ""):
    """Reject run(s) at the storyboard step (history status: rejected)."""
    from shorts import storyboard

    for r in _runs(runs):
        storyboard.reject(ctx(), r, note)
        typer.echo(f"rejected {r.id}")


# ------------------------------------------------------------------ styles
@app.command()
def styles():
    """List style profiles (styles/*.env) and show which one is active."""
    from dotenv import dotenv_values

    from shorts.config import list_styles

    a = ctx().app
    for name in list_styles(a.styles_dir):
        v = dotenv_values(Path(a.styles_dir) / f"{name}.env")
        mark = "→" if name == a.style_profile else " "
        typer.echo(f"{mark} {name:12s} {(v.get('STYLE_PROMPT') or '')[:90]}")
    typer.echo(f"\nactive: {a.style_profile or '(none: STYLE_PROMPT from .env)'}. "
               "Switch: STYLE_PROFILE=name in .env, or `shorts --style name make ...`")


# ------------------------------------------------------------------ diagnostics
@app.command()
def providers():
    """List built-in providers and the current selection."""
    from shorts.registry import BUILTINS

    a = ctx().app
    selected = {"llm": a.llm_provider, "tts": a.tts_provider, "align": a.align_provider,
                "image": a.image_provider, "motion": f"{a.motion_provider} (fallback: {a.motion_fallback})",
                "search": a.search_provider, "similarity": a.similarity_provider, "music": a.music_provider,
                "upload": a.uploader}
    for kind, names in BUILTINS.items():
        typer.echo(f"{kind:10s} selected={selected.get(kind, '?'):<32s} available: {', '.join(names)}")


@app.command()
def check(
    skip: Annotated[Optional[list[str]], typer.Option(help="Provider kinds to skip, e.g. --skip upload")] = None,
):
    """Instantiate every configured provider to validate settings and dependencies."""
    c = ctx()
    a = c.app
    targets = [("llm", a.llm_for("ideas")), ("llm", a.llm_for("script")), ("tts", a.tts_provider),
               ("align", a.align_provider), ("image", a.image_provider), ("motion", a.motion_provider),
               ("motion", a.motion_fallback), ("similarity", a.similarity_provider), ("music", a.music_provider),
               ("upload", a.uploader)]
    ok = True
    for kind, name in dict.fromkeys(t for t in targets if t[1] and t[1] != "none" or t[0] == "upload"):
        if skip and kind in skip:
            continue
        try:
            p = c.provider(kind, name)
            p.verify()
            typer.secho(f"✔ {kind:7s} {p.describe()}", fg="green")
            if p.license_note and not (kind == "tts" and p.name == "f5" and getattr(p.settings, "ckpt_file", "")):
                typer.secho(f"  ⚠ license: {p.license_note}", fg="yellow")
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


# ------------------------------------------------------------------ history
@history_app.command("list")
def history_list(
    status_: Annotated[Optional[list[str]], typer.Option("--status", "-s", help="Filter, repeatable")] = None,
    topic: Annotated[Optional[str], typer.Option(help="Only this topic")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 30,
):
    """Show produced scripts, newest first."""
    from shorts import history

    color = {"draft": None, "auto_rejected": "red", "rejected": "red", "created": "cyan", "uploaded": "green",
             "popular": "magenta"}
    entries = [e for e in history.load(ctx().app).entries
               if (not status_ or e.status in status_) and (not topic or e.topic == topic)]
    for e in sorted(entries, key=lambda e: e.created_at, reverse=True)[:limit]:
        score = f" score {e.review_score}" if e.review_score is not None else ""
        typer.secho(f"{e.status:13s} {e.id}", fg=color.get(e.status))
        typer.echo(f"              {e.title}{score}" + (f"  {e.upload_url}" if e.upload_url else ""))
    typer.echo(f"\n{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}; statuses: {', '.join(history.STATUSES)}")


@history_app.command("show")
def history_show(run: str):
    """Full entry of one run, with its status log."""
    from shorts import history

    e = history.load(ctx().app).get(Path(run).name)
    if e is None:
        raise typer.BadParameter(f"{run} is not in the history")
    typer.echo(e.model_dump_json(indent=2))


@history_app.command("mark")
def history_mark(
    runs: list[str],
    status_: Annotated[str, typer.Option("--status", "-s", help="rejected | popular | created | uploaded | draft")],
    note: Annotated[str, typer.Option(help="Why (kept in the status log)")] = "",
):
    """Set the status of run(s) by hand, e.g. mark a video popular or reject a script."""
    from shorts import history

    for run in runs:
        e = history.set_status(ctx().app, Path(run).name, status_, by="human", note=note)
        typer.echo(f"{e.id}: {e.status}")


@history_app.command("similar")
def history_similar(
    text: Annotated[str, typer.Argument(help="An idea or script text, or a run id")],
    field: Annotated[str, typer.Option(help="idea | script")] = "idea",
):
    """Check a text (or an existing run) against the history with the configured similarity provider."""
    from shorts import history

    c = ctx()
    exclude: set[str] = set()
    run_dir = Path(c.app.runs_dir) / Path(text).name
    if (run_dir / "manifest.json").exists():
        (r,) = _runs([run_dir])
        exclude = {r.id}
        if field == "script" and (r.path / "script.json").exists():
            s = r.script()
            text = f"{s.title}. {s.narration}"
        else:
            i = r.idea()
            text = f"{i.title}. {i.hook} {i.angle}"
    matches = history.find_similar(c, text, field=field, exclude=exclude)  # type: ignore[arg-type]
    for m in matches:
        typer.secho(f"⚠ {m}", fg="yellow")
    typer.echo(f"{len(matches)} similar (threshold {c.similarity().threshold if c.similarity() else '-'})")


@history_app.command("sync")
def history_sync():
    """Add every existing run folder to the history (backfill), refreshing titles/narration/statuses."""
    from shorts import history

    entries = history.sync(ctx())
    typer.echo(f"{len(entries)} run(s) in {ctx().app.history_file}")


# ------------------------------------------------------------------ comfy helpers
@comfy_app.command("inspect")
def comfy_inspect(workflow: Path):
    """List nodes, their literal inputs and the placeholders of an API-format workflow."""
    from shorts.comfy import template

    wf = template.load(workflow)
    for line in template.describe(wf):
        typer.echo(line)
    typer.echo(f"\nplaceholders: {sorted(template.placeholders(wf)) or 'none'}")


@comfy_app.command("pull")
def comfy_pull(
    output: Annotated[Optional[Path], typer.Argument(help="Save the job here, e.g. workflows/my_image.json")] = None,
    job: Annotated[int, typer.Option("--job", "-j", help="1 = most recent job (see the list without OUTPUT)")] = 1,
):
    """Copy a job you ran in the ComfyUI browser UI as an API-format workflow (no export needed).

    Without OUTPUT: list recent jobs. Then run `shorts comfy inspect` and `shorts comfy mark` on the saved file.
    """
    from shorts.comfy.client import ComfyClient

    jobs = ComfyClient(ctx().app.comfy_url).history()
    if not jobs:
        raise typer.BadParameter("ComfyUI has no finished jobs: run the workflow once in the browser first")
    if output is None:
        for n, j in enumerate(jobs, 1):
            saves = [t for t in j["class_types"] if t.startswith("Save") or "Combine" in t]
            loaders = [t for t in j["class_types"] if "Loader" in t][:3]
            typer.echo(f"{n:2d}. {j['status']:8s} {j['seconds']:6.1f}s  {', '.join(saves)}  ({', '.join(loaders)})")
        typer.echo("\nSave one: shorts comfy pull workflows/NAME.json --job N")
        return
    j = jobs[job - 1]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(j["workflow"], indent=2, ensure_ascii=False), encoding="utf-8")
    typer.echo(f"wrote {output} (job {job}, {j['status']}). Next: shorts comfy inspect {output}")


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
