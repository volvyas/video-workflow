"""Images and motion stages."""

from __future__ import annotations

import logging
from pathlib import Path

from shorts.context import Context
from shorts.models import ImageVerdict, Scene
from shorts.prompts import render
from shorts.run import Run

log = logging.getLogger("shorts.stage.visuals")


def scene_file(run: Run, folder: str, index: int) -> Path | None:
    matches = sorted((run.path / folder).glob(f"scene_{index + 1:02d}.*"))
    return matches[0] if matches else None


def image_prompt(app, script, scene: Scene) -> str:
    parts = [scene.image_prompt.strip().rstrip(".")]
    if script.visual_style.strip():
        parts.append(script.visual_style.strip().rstrip("."))
    if app.style_prompt:
        parts.append(app.style_prompt)
    return ", ".join(parts)


def _check(ctx: Context, image: Path, prompt: str, scene: Scene) -> ImageVerdict:
    app = ctx.app
    llm = ctx.llm("image_qa")
    return llm.generate_json(
        system=render("image_qa_system", app.prompts_dir),
        prompt=render("image_qa_user", app.prompts_dir, prompt=prompt, narration=scene.narration),
        schema=ImageVerdict, images=[image],
    )


class _Item:
    """One scene image being produced (possibly over several QA rounds)."""

    def __init__(self, run: Run, index: int, scene: Scene, prompt: str):
        self.run, self.index, self.scene, self.prompt = run, index, scene, prompt
        self.out = run.scene_file("images", index, "png")
        self.best: tuple[int, Path] | None = None
        self.passed = False
        self.history: list[dict] = []

    def candidate(self, k: int) -> Path:
        return self.out if k == 0 else self.run.sub("images", "candidates", f"scene_{self.index + 1:02d}_try{k + 1}.png")


def generate_images_batch(ctx: Context, runs: list[Run], only: set[int] | None = None) -> dict[str, str]:
    """Generate (and QA) images for all runs in rounds, so GPU models swap once per round, not once per image.

    Round k: generate every pending image with ComfyUI → free ComfyUI → check all with the vision LLM →
    free the LLM → re-roll only the rejected ones. Returns {run_id: error} for runs that failed.
    """
    app = ctx.app
    errors: dict[str, str] = {}
    items: list[_Item] = []
    for run in runs:
        script = run.script()
        for i, scene in enumerate(script.scenes):
            if run.scene_file("images", i, "png").exists() and not (only and i in only):
                continue
            items.append(_Item(run, i, scene, image_prompt(app, script, scene)))

    qa = app.image_qa_enabled
    if qa and not ctx.llm("image_qa").supports_vision:
        log.warning("image QA disabled: %s has no vision support", ctx.llm("image_qa").describe())
        qa = False
    llm_remote = qa and ctx.llm("image_qa").remote
    rounds = 1 + (max(0, app.image_qa_max_retries) if qa else 0)
    todo = items
    for k in range(rounds):
        todo = [it for it in todo if it.run.id not in errors]
        if not todo:
            break
        provider = ctx.image()
        for it in todo:
            if it.run.id in errors:
                continue
            seed = it.run.manifest.seed + it.index + 1000 * k
            log.info("[%s] image %d%s", it.run.id, it.index + 1, f" (re-roll {k})" if k else "")
            try:
                provider.generate(prompt=it.prompt, negative=app.negative_prompt, width=app.image_width,
                                  height=app.image_height, seed=seed, out_path=it.candidate(k))
            except Exception as e:  # noqa: BLE001 - isolate failures per run
                errors[it.run.id] = f"image scene {it.index + 1}: {e}"
                continue
            it.history.append({"try": k + 1, "seed": seed})
            if not qa:
                it.best, it.passed = (0, it.candidate(k)), True
        if not qa:
            break
        if not llm_remote:
            ctx.unload("image")  # free ComfyUI VRAM/RAM before the local LLM loads
        for it in todo:
            if it.run.id in errors or len(it.history) <= k:
                continue
            verdict = _check(ctx, it.candidate(k), it.prompt, it.scene)
            it.history[-1].update(verdict.model_dump())
            if it.best is None or verdict.score > it.best[0]:
                it.best = (verdict.score, it.candidate(k))
            if verdict.acceptable(app.image_qa_min_score):
                it.best, it.passed = (verdict.score, it.candidate(k)), True
            else:
                log.info("[%s] image %d rejected (score %d): %s", it.run.id, it.index + 1, verdict.score,
                         "; ".join(verdict.problems) or "flags set")
        todo = [it for it in todo if not it.passed]
        if todo and k + 1 < rounds and not llm_remote:
            ctx.unload("llm")  # free the LLM before ComfyUI loads again

    for it in items:
        if it.run.id in errors or it.best is None:
            continue
        if it.best[1] != it.out:  # keep the best candidate as the scene image; the first try moves to candidates/
            if it.out.exists():
                it.out.replace(it.run.sub("images", "candidates", f"scene_{it.index + 1:02d}_try1.png"))
            it.best[1].replace(it.out)
        if qa:
            it.run.stage("images").notes.setdefault("qa", {})[str(it.index + 1)] = it.history
            if not it.passed:
                log.warning("[%s] scene %d: no candidate passed QA; using the best (score %d)",
                            it.run.id, it.index + 1, it.best[0])
    return errors


def wants_primary(policy: str, index: int) -> bool:
    policy = policy.strip().lower()
    if policy == "all":
        return True
    if policy == "none":
        return False
    kind, _, n = policy.partition(":")
    if kind == "first":
        return index < int(n)
    if kind == "every":
        return index % int(n) == 0
    raise ValueError(f"bad MOTION_SCENES value '{policy}' (all | none | first:N | every:N)")


def generate_motion(ctx: Context, run: Run, only: set[int] | None = None) -> dict[str, str]:
    """Returns {scene_number: provider_used}. Falls back per scene if the primary provider fails."""
    script, timings = run.script(), run.timings()
    used: dict[str, str] = {}
    errors: dict[str, str] = {}
    primary, fallback = ctx.motion(), ctx.motion(fallback=True)
    for i, (scene, t) in enumerate(zip(script.scenes, timings)):
        existing = scene_file(run, "clips", i)
        if existing and not (only and i in only):
            used[str(i + 1)] = run.stage("motion").notes.get("providers", {}).get(str(i + 1), "existing")
            continue
        if existing:
            existing.unlink()
        image = run.scene_file("images", i, "png")
        out = run.scene_file("clips", i, "mp4")
        chain = [p for p in ([primary] if primary and wants_primary(ctx.app.motion_scenes, i) else []) + [fallback] if p]
        if not chain:
            raise RuntimeError("no motion provider configured (MOTION_PROVIDER / MOTION_FALLBACK)")
        for p in dict.fromkeys(chain):  # dedupe, keep order
            try:
                log.info("[%s] motion %d/%d via %s", run.id, i + 1, len(script.scenes), p.describe())
                p.animate(image=image, prompt=scene.motion_prompt, camera=scene.camera, duration=t.slot,
                          seed=run.manifest.seed + i, out_path=out)
                used[str(i + 1)] = p.describe()
                break
            except Exception as e:  # noqa: BLE001 - try the next provider
                log.warning("motion scene %d failed with %s: %s", i + 1, p.describe(), e)
                errors[str(i + 1)] = f"{p.describe()}: {e}"
        else:
            raise RuntimeError(f"all motion providers failed for scene {i + 1}: {errors.get(str(i + 1))}")
    run.stage("motion").notes.update({"providers": used, "errors": errors})
    return used
