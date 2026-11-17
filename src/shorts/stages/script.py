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
    return (f"Your previous script was {direction}: {n} words of narration. It must be {lo}-{hi} words "
            f"(target {target}) to fill about {seconds} seconds. Rewrite it at the right length: "
            f"{'add more scenes and concrete details' if n < lo else 'cut filler and merge scenes'}, keep all rules.")


def write_script(ctx: Context, run: Run) -> Script:
    app = ctx.app
    idea = run.idea()
    seconds = app.video_target_seconds
    min_scenes = max(4, round(seconds / 6))
    max_scenes = max(min_scenes + 2, round(seconds / 3))
    system = render(
        "script_system", app.prompts_dir,
        channel_brief=app.channel_brief, seconds=seconds, words=f"{word_range(seconds)[0]}-{word_range(seconds)[2]}",
        language=app.content_language, min_scenes=min_scenes, max_scenes=max_scenes, style=app.style_prompt,
    )
    prompt = render("script_user", app.prompts_dir, topic=run.manifest.topic, title=idea.title,
                    hook=idea.hook, angle=idea.angle)
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
    script = best
    run.write_json("script.json", script)
    return script
