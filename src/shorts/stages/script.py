"""Script stage: idea -> script.json (scenes with narration and visual prompts)."""

from __future__ import annotations

import logging

from shorts.context import Context
from shorts.models import Script
from shorts.prompts import render
from shorts.providers.base import ProviderError
from shorts.run import Run

WORDS_PER_SECOND = 2.6  # energetic narration pace
LENGTH_RETRIES = 2

log = logging.getLogger("shorts.stage.script")


def word_range(seconds: int) -> tuple[int, int, int]:
    target = round(seconds * WORDS_PER_SECOND)
    return round(target * 0.8), target, round(target * 1.25)


def word_count(script: Script) -> int:
    return len(script.narration.split())


def length_feedback(script: Script, seconds: int) -> str | None:
    lo, target, hi = word_range(seconds)
    n = word_count(script)
    if lo <= n <= hi:
        return None
    direction = "too short" if n < lo else "too long"
    scenes, per_lo, per_hi = scene_plan(seconds)
    return (f"Your previous script was {direction}: {n} words in {len(script.scenes)} scenes. It must be {lo}-{hi} "
            f"words (target {target}) to fill about {seconds} seconds: write exactly {scenes} scenes with "
            f"{per_lo}-{per_hi} words of narration each. Keep all other rules.")


def script_system_prompt(ctx: Context) -> str:
    app = ctx.app
    seconds = app.video_target_seconds
    lo, target, hi = word_range(seconds)
    scenes, per_lo, per_hi = scene_plan(seconds)
    return render(
        "script_system", app.prompts_dir,
        channel_brief=app.channel_brief, seconds=seconds, words=f"{lo}-{hi}", scenes=scenes,
        scene_words=f"{per_lo}-{per_hi}", language=app.content_language, style=app.style_prompt,
        style_guide=app.style_guide or "",
    )


def scene_plan(seconds: int) -> tuple[int, int, int]:
    """Number of scenes and words per scene. Models hit structure (N scenes × M words) far better than totals."""
    target = word_range(seconds)[1]
    scenes = max(4, round(target / 13))
    per = target / scenes
    return scenes, max(6, round(per * 0.8)), round(per * 1.25)


class FalsePremise(RuntimeError):
    pass


def idea_premise(ctx: Context, run: Run):
    """The idea's premise check: from premise.json, the idea pool, or checked now (IDEAS_FACT_CHECK)."""
    from shorts.models import PremiseCheck
    from shorts.stages import ideas

    p = run.path / "premise.json"
    if p.exists():
        return PremiseCheck.model_validate_json(p.read_text())
    if not ctx.app.ideas_fact_check:
        return None
    check = None
    if run.manifest.idea_id:
        try:
            check = ideas.find_idea(ctx.app, run.manifest.topic, run.manifest.idea_id).premise
        except KeyError:
            pass
    if check is None:
        check = ideas.check_premise(ctx, run.manifest.topic, run.idea())
    run.write_json("premise.json", check)
    return check


def write_script(ctx: Context, run: Run) -> Script:
    from shorts.stages.ideas import premise_note, usable

    idea = run.idea()
    check = idea_premise(ctx, run)
    if not usable(ctx.app, check):
        raise FalsePremise(f"the idea's premise is false: {check.premise} ({check.explanation}). Pick another idea, "
                           f"or edit idea.json and delete premise.json, then resume")
    prompt = render("script_user", ctx.app.prompts_dir, topic=run.manifest.topic, title=idea.title,
                    hook=idea.hook, angle=idea.angle, premise=premise_note(check))
    script = generate_script(ctx, run, prompt)
    run.write_json("script.json", script)
    return script


def generate_script(ctx: Context, run: Run, prompt: str) -> Script:
    """Ask the script LLM for a Script, retrying with feedback until the narration length is in range."""
    seconds = ctx.app.video_target_seconds
    system = script_system_prompt(ctx)
    llm = ctx.llm("script")
    best: Script | None = None
    feedback = None
    for attempt in range(LENGTH_RETRIES + 1):
        p = prompt if feedback is None else f"{prompt}\n\n{feedback}"
        script = llm.generate_json(system=system, prompt=p, schema=Script)
        script.scenes = [s for s in script.scenes if s.narration.strip()]
        if not script.scenes:
            continue
        target = word_range(seconds)[1]
        if best is None or abs(word_count(script) - target) < abs(word_count(best) - target):
            best = script
        feedback = length_feedback(script, seconds)
        if feedback is None:
            break
        log.info("[%s] script length off (%d words), retry %d: %s", run.id, word_count(script), attempt + 1,
                 feedback.split(".")[0])
    if best is None:
        raise ProviderError("script has no scenes with narration")
    return best
