"""Storyboard approval: a human looks at script, images, voice and music before the expensive motion stage.

The `approve` stage sits between `images` and `motion`. With STORYBOARD_APPROVAL=true it stops every run there
(status "waiting") until someone approves it, usually on the `shorts storyboard` page. Everything the page can do
lives here so it can also be scripted and tested: re-roll or pick scene images, choose AI motion or a still per
scene, regenerate music, build a cheap animatic preview, approve, reject.
"""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Any

from shorts.context import Context
from shorts.run import Run

log = logging.getLogger("shorts.storyboard")

WAITING = "waiting"
# what changing an approved storyboard invalidates
_AFTER_APPROVAL = ["approve", "motion", "assemble", "upload"]


class AwaitingApproval(RuntimeError):
    pass


# ---------------------------------------------------------------- the pipeline gate
def require_approval(ctx: Context, run: Run) -> None:
    if not ctx.app.storyboard_approval:
        run.stage("approve").notes.update({"by": "auto"})
        return
    raise AwaitingApproval(f"storyboard ready, waiting for approval: shorts storyboard (or shorts approve {run.id})")


def approve(run: Run, by: str = "human", note: str = "") -> None:
    rec = run.stage("approve")
    rec.notes.update({"by": by, "note": note, "at": time.time()})
    run.finish("approve")


def reject(ctx: Context, run: Run, note: str = "") -> None:
    from shorts import history

    rec = run.stage("approve")
    rec.status, rec.error = "failed", f"rejected on the storyboard{': ' + note if note else ''}"
    rec.notes.update({"by": "human", "rejected": True, "note": note, "at": time.time()})
    run.save()
    try:
        history.upsert_from_run(ctx.app, run)
        history.set_status(ctx.app, run.id, "rejected", by="human", note=note or "rejected on the storyboard")
    except Exception as e:  # noqa: BLE001
        log.warning("history update failed for %s: %s", run.id, e)


def _reset_after_approval(run: Run) -> None:
    """An edit needs a new approval; keep the per-scene motion choices (they live in the approve notes)."""
    motion = dict(run.stage("approve").notes.get("motion", {}))
    run.mark_pending(_AFTER_APPROVAL)
    if motion:
        run.stage("approve").notes["motion"] = motion
        run.save()


# ---------------------------------------------------------------- per-scene edits
def _scene_image(run: Run, i: int) -> Path:
    return run.scene_file("images", i, "png")


def candidates(run: Run, i: int) -> list[Path]:
    d = run.path / "images" / "candidates"
    return sorted(d.glob(f"scene_{i + 1:02d}_*.png"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []


def _drop_clip(run: Run, i: int) -> None:
    for p in (run.path / "clips").glob(f"scene_{i + 1:02d}.*"):
        p.unlink()


def _keep_as_candidate(run: Run, i: int) -> None:
    cur = _scene_image(run, i)
    if cur.exists():
        cur.replace(run.sub("images", "candidates", f"scene_{i + 1:02d}_{time.time_ns()}.png"))


def reroll(ctx: Context, run: Run, i: int, prompt: str | None = None, seed: int | None = None) -> Path:
    """New image for scene i (optionally with a new scene prompt). The old one is kept as a candidate."""
    from shorts.stages.visuals import image_prompt

    script = run.script()
    scene = script.scenes[i]
    if prompt is not None and prompt.strip() and prompt.strip() != scene.image_prompt:
        scene.image_prompt = prompt.strip()
        run.write_json("script.json", script)
    seed = seed if seed is not None else random.randint(0, 2**31 - 1)
    tmp = run.sub("images", "candidates", f"scene_{i + 1:02d}_new.png")
    app = ctx.app
    ctx.image().generate(prompt=image_prompt(app, script, scene), negative=app.negative_prompt,
                         width=app.image_width, height=app.image_height, seed=seed, out_path=tmp)
    _keep_as_candidate(run, i)
    tmp.replace(_scene_image(run, i))
    _drop_clip(run, i)
    run.stage("images").notes.setdefault("rerolls", {}).setdefault(str(i + 1), []).append(
        {"seed": seed, "prompt": scene.image_prompt, "at": time.time()})
    _reset_after_approval(run)
    return _scene_image(run, i)


def pick(run: Run, i: int, candidate_name: str) -> None:
    """Make a kept candidate the scene image again (the current one becomes a candidate)."""
    cand = run.path / "images" / "candidates" / Path(candidate_name).name
    if not cand.exists() or not cand.name.startswith(f"scene_{i + 1:02d}_"):
        raise FileNotFoundError(f"no candidate {candidate_name} for scene {i + 1}")
    chosen = cand.with_name(f"scene_{i + 1:02d}_picking.png")
    cand.replace(chosen)
    _keep_as_candidate(run, i)
    chosen.replace(_scene_image(run, i))
    _drop_clip(run, i)
    _reset_after_approval(run)


def edit_narration(ctx: Context, run: Run, i: int, text: str) -> None:
    """Change what scene i says: re-record only that scene, re-time captions, refit music, drop its clips.

    The fact-check result is marked stale (re-check it with `recheck_facts`)."""
    from shorts import history
    from shorts.media import ffmpeg
    from shorts.stages import align, voice
    from shorts.stages.music import make_music, music_file

    text = " ".join(text.split())
    if not text:
        raise ValueError("narration can't be empty")
    script = run.script()
    if script.scenes[i].narration.strip() == text:
        return
    script.scenes[i].narration = text
    run.write_json("script.json", script)
    voice.synthesize(ctx, run, only={i})
    align.align(ctx, run)
    total = sum(t.slot for t in run.timings())
    music = music_file(run)
    if ctx.app.music_provider == "comfyui" and (music is None or ffmpeg.duration(music) < total):
        make_music(ctx, run)  # generated music must cover the new length (folder music just loops)
    _drop_clip(run, i)
    for p in (run.path / "preview" / "clips").glob(f"scene_{i + 1:02d}.*"):
        p.unlink()
    rec = run.stage("review")
    rec.notes.setdefault("edited_scenes", []).append(i + 1)
    rec.notes["stale"] = True
    run.save()
    _reset_after_approval(run)
    try:
        history.upsert_from_run(ctx.app, run)
    except Exception as e:  # noqa: BLE001
        log.warning("history update failed for %s: %s", run.id, e)


def recheck_facts(ctx: Context, run: Run) -> dict:
    """Fact-check the current script again (no rewrite). Appended to review.json as a 'recheck' round."""
    from shorts.stages.review import bad_claims, judge_script
    from shorts.stages.script import word_count

    script = run.script()
    path = run.path / "review.json"
    rounds = json.loads(path.read_text()) if path.exists() else []
    n = (rounds[-1]["round"] + 1) if rounds else 1
    review, research = judge_script(ctx, run, script, n)
    rounds.append({"round": n, "recheck": True, "words": word_count(script), "research": research,
                   "script": script.model_dump(), "review": review.model_dump()})
    path.write_text(json.dumps(rounds, indent=2, ensure_ascii=False))
    rec = run.stage("review")
    rec.notes.update({"score": review.score, "hook_score": review.hook_score, "kept_round": n, "stale": False,
                      "rounds": len(rounds)})
    run.save()
    return {"score": review.score, "bad_claims": len(bad_claims(review))}


def set_motion(run: Run, i: int, mode: str) -> None:
    """'ai' = primary motion provider (e.g. LTX), 'still' = the fallback (Ken Burns pan/zoom, no GPU)."""
    if mode not in ("ai", "still"):
        raise ValueError("mode must be 'ai' or 'still'")
    choices = run.stage("approve").notes.setdefault("motion", {})
    if choices.get(str(i + 1)) != mode:
        choices[str(i + 1)] = mode
        _drop_clip(run, i)
        _reset_after_approval(run)
    run.save()


def motion_choice(run: Run, i: int) -> str | None:
    return run.stage("approve").notes.get("motion", {}).get(str(i + 1))


def regenerate_music(ctx: Context, run: Run, tags: str | None = None, bpm: int | None = None,
                     key: str | None = None) -> Path | None:
    from shorts.stages.music import make_music

    script = run.script()
    m = script.music
    if tags is not None:
        m.tags = tags.strip()
    if bpm is not None:
        m.bpm = int(bpm)
    if key:
        m.key = key  # type: ignore[assignment]  # validated by the model on the next load
    run.write_json("script.json", script.model_validate(script.model_dump()))
    make_music(ctx, run)
    run.finish("music")
    run.mark_pending(["assemble", "upload"])
    from shorts.stages.music import music_file

    return music_file(run)


# ---------------------------------------------------------------- animatic preview
def preview_path(run: Run) -> Path:
    return run.path / "preview" / "preview.mp4"


def build_preview(ctx: Context, run: Run) -> Path:
    """Stills with pan/zoom + voice + music + captions: the final timing for a few CPU-minutes, no GPU.

    Preview clips are cached per scene and only re-rendered when the scene image changed.
    """
    from shorts.media.assemble import assemble

    kb = ctx.provider("motion", "kenburns")
    script, timings = run.script(), run.timings()
    clips = []
    for i, (scene, t) in enumerate(zip(script.scenes, timings)):
        img = _scene_image(run, i)
        clip = run.sub("preview", "clips", f"scene_{i + 1:02d}.mp4")
        if not clip.exists() or clip.stat().st_mtime < img.stat().st_mtime:
            kb.animate(image=img, prompt=scene.motion_prompt, camera=scene.camera, duration=t.slot,
                       seed=run.manifest.seed + i, out_path=clip)
        clips.append(clip)
    return assemble(ctx.app, run, clips, out=preview_path(run), work_dir=run.path / "preview" / "segments",
                    preset="veryfast", crf=26)


# ---------------------------------------------------------------- what the page shows
def summary(run: Run) -> dict[str, Any]:
    from shorts.stages.music import music_file

    m = run.manifest
    script = run.script() if (run.path / "script.json").exists() else None
    timings = run.timings() if (run.path / "timings.json").exists() else []
    review: dict[str, Any] = {}
    if (run.path / "review.json").exists():
        rounds = json.loads((run.path / "review.json").read_text())
        notes = run.stage("review").notes
        kept = notes.get("kept_round") or (rounds[-1]["round"] if rounds else None)
        r = next((x for x in rounds if x["round"] == kept), rounds[-1] if rounds else None)
        if r:
            review = {"score": r["review"]["score"], "hook_score": r["review"]["hook_score"], "round": r["round"],
                      "rounds": len(rounds), "claims": r["review"]["claims"], "problems": r["review"]["problems"],
                      "stale": bool(notes.get("stale")), "edited_scenes": notes.get("edited_scenes", [])}
    qa = run.stage("images").notes.get("qa", {})
    scenes = []
    for i, sc in enumerate(script.scenes if script else []):
        img = _scene_image(run, i)
        audio = run.scene_file("audio", i, "wav")
        scenes.append({
            "n": i + 1, "narration": sc.narration, "image_prompt": sc.image_prompt, "motion_prompt": sc.motion_prompt,
            "on_screen_text": sc.on_screen_text, "camera": sc.camera,
            "seconds": round(timings[i].slot, 2) if i < len(timings) else None,
            "image": _rel(run, img) if img.exists() else None, "image_mtime": img.stat().st_mtime if img.exists() else 0,
            "candidates": [_rel(run, p) for p in candidates(run, i)],
            "audio": _rel(run, audio) if audio.exists() else None,
            "motion": motion_choice(run, i) or "ai", "qa": (qa.get(str(i + 1)) or [None])[-1],
        })
    music = music_file(run)
    return {
        "id": run.id, "topic": m.topic, "style": m.style_profile, "created_at": m.created_at,
        "stages": {k: {"status": v.status, "error": v.error} for k, v in m.stages.items()},
        "state": state(run),
        "title": script.title if script else "", "description": script.description if script else "",
        "narration": script.narration if script else "", "visual_style": script.visual_style if script else "",
        "music": {"file": _rel(run, music) if music else None, "mtime": music.stat().st_mtime if music else 0,
                  **(script.music.model_dump() if script else {})},
        "review": review, "scenes": scenes,
        "preview": _rel(run, preview_path(run)) if preview_path(run).exists() else None,
        "preview_mtime": preview_path(run).stat().st_mtime if preview_path(run).exists() else 0,
        "final": _rel(run, run.final_video) if run.final_video.exists() and run.is_done("assemble") else None,
        "final_mtime": run.final_video.stat().st_mtime if run.final_video.exists() else 0,
        "seconds": round(sum(t.slot for t in timings), 1) if timings else None,
    }


def state(run: Run) -> str:
    """One word for the run list: draft | waiting | approved | rendering-ready | done | rejected | failed."""
    if run.is_done("assemble") and run.is_done("approve"):
        return "done"
    a = run.stage("approve")
    if a.notes.get("rejected"):
        return "rejected"
    if a.status == WAITING:
        return "waiting"
    if a.status == "done":
        return "approved"
    if any(r.status == "failed" for r in run.manifest.stages.values()):
        return "failed"
    if run.is_done("assemble"):
        return "done"
    return "draft"


def _rel(run: Run, p: Path) -> str:
    return str(p.relative_to(run.path))
