"""Review stage: fact-check (web search when available) + editorial critique, then a revised script.

Files: script.original.json (first LLM draft, kept), review.json (all rounds), script.json (final, used downstream).
"""

from __future__ import annotations

import logging

from shorts.context import Context
from shorts.models import Script, ScriptReview
from shorts.prompts import render
from shorts.run import Run
from shorts.stages.research import research as web_research

log = logging.getLogger("shorts.stage.review")


class NeedsHumanReview(RuntimeError):
    pass


def review_script(ctx: Context, run: Run) -> ScriptReview | None:
    app = ctx.app
    if not app.review_enabled:
        return None
    original = run.path / "script.original.json"
    if original.exists():  # re-review always starts from the first draft
        current = Script.model_validate_json(original.read_text())
    else:
        current = run.script()
        original.write_text(current.model_dump_json(indent=2))

    llm = ctx.llm("review")
    topic = run.manifest.topic
    system = render("review_system", app.prompts_dir, channel_brief=app.channel_brief,
                    language=app.content_language, seconds=app.video_target_seconds)
    rounds: list[dict] = []
    review: ScriptReview | None = None
    for rnd in range(1, max(1, app.review_max_rounds) + 1):
        research = ""
        if app.review_web_search:
            log.info("[%s] fact-check round %d: web research", run.id, rnd)
            research = web_research(ctx, topic, current.narration)
        prompt = render("review_user", app.prompts_dir, topic=topic, research=research or "(none)",
                        script=current.model_dump_json())
        review = llm.generate_json(system=system, prompt=prompt, schema=ScriptReview)
        bad = [c for c in review.claims if c.verdict in ("incorrect", "unverifiable")]
        log.info("[%s] review round %d: score %d, hook %d, %d claim(s) fixed", run.id, rnd, review.score,
                 review.hook_score, len(bad))
        rounds.append({"round": rnd, "research": research, "review": review.model_dump(exclude={"revised"})})
        if review.revised.scenes:
            current = review.revised
        if review.score >= app.review_min_score and not bad:
            break

    run.write_json("review.json", rounds)
    run.write_json("script.json", current)
    assert review is not None
    run.stage("review").notes.update({"score": review.score, "hook_score": review.hook_score, "rounds": len(rounds)})
    if review.score < app.review_min_score and app.review_on_low_score == "fail":
        raise NeedsHumanReview(
            f"script scored {review.score} < REVIEW_MIN_SCORE={app.review_min_score} after {len(rounds)} round(s). "
            f"Read review.json and script.json (edit it if you like), then `shorts accept {run.id}` and "
            f"`shorts resume {run.id}`; or `shorts resume {run.id} --force script` for a new draft."
        )
    return review


def accept(run: Run) -> None:
    """Mark the review stage done without re-running it (after a human looked at the script)."""
    run.finish("review", accepted_by="human")
