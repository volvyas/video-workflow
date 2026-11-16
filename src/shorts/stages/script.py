"""Script stage: idea -> script.json (scenes with narration and visual prompts)."""

from __future__ import annotations

from shorts.context import Context
from shorts.models import Script
from shorts.prompts import render
from shorts.providers.base import ProviderError
from shorts.run import Run

WORDS_PER_SECOND = 2.6  # energetic narration pace


def write_script(ctx: Context, run: Run) -> Script:
    app = ctx.app
    idea = run.idea()
    seconds = app.video_target_seconds
    min_scenes = max(4, round(seconds / 6))
    max_scenes = max(min_scenes + 2, round(seconds / 3))
    system = render(
        "script_system", app.prompts_dir,
        channel_brief=app.channel_brief, seconds=seconds, words=round(seconds * WORDS_PER_SECOND),
        language=app.content_language, min_scenes=min_scenes, max_scenes=max_scenes, style=app.style_prompt,
    )
    prompt = render("script_user", app.prompts_dir, topic=run.manifest.topic, title=idea.title,
                    hook=idea.hook, angle=idea.angle)
    script = ctx.llm("script").generate_json(system=system, prompt=prompt, schema=Script)
    script.scenes = [s for s in script.scenes if s.narration.strip()]
    if not script.scenes:
        raise ProviderError("script has no scenes with narration")
    run.write_json("script.json", script)
    return script
