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
from shorts.stages.script import generate_script, scene_plan, word_count, word_range

log = logging.getLogger("shorts.stage.review")


class NeedsHumanReview(RuntimeError):
    pass


def review_script(ctx: Context, run: Run) -> ScriptReview | None:
    """Judge → (if not good enough) rewrite with the script writer → judge again, up to REVIEW_MAX_ROUNDS reviews.

    script.json ends up as the last *judged* version, so the stored score always matches it.
    """
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
    lo, _, hi = word_range(app.video_target_seconds)
    system = render("review_system", app.prompts_dir, channel_brief=app.channel_brief, words=f"{lo}-{hi}")
    rounds: list[dict] = []
    review: ScriptReview | None = None
    max_rounds = max(1, app.review_max_rounds)
    for rnd in range(1, max_rounds + 1):
        research = ""
        if app.review_web_search:
            log.info("[%s] fact-check round %d: web research", run.id, rnd)
            research = web_research(ctx, topic, current.narration)
        prompt = render("review_user", app.prompts_dir, topic=topic, research=research or "(none)",
                        script=current.model_dump_json())
        prompt += f"\n\nThe script has {word_count(current)} words of narration; the target is {lo}-{hi}."
        review = llm.generate_json(system=system, prompt=prompt, schema=ScriptReview)
        bad = [c for c in review.claims if c.verdict in ("incorrect", "unverifiable")]
        log.info("[%s] review round %d: score %d, hook %d, %d bad claim(s), %d words", run.id, rnd, review.score,
                 review.hook_score, len(bad), word_count(current))
        rounds.append({"round": rnd, "words": word_count(current), "research": research,
                       "script": current.model_dump(), "review": review.model_dump()})
        if review.score >= app.review_min_score and not bad:
            break
        if rnd == max_rounds:
            break
        log.info("[%s] rewriting script with the editor's notes", run.id)
        corrections = "\n".join(f"- [{c.verdict}] \"{c.claim}\" → {c.correction or 'remove it'} ({c.source})"
                                for c in bad) or "(none)"
        problems = "\n".join(f"- {p}" for p in review.problems) or "(none)"
        rewrite = render("rewrite_user", app.prompts_dir, topic=topic, script=current.model_dump_json(),
                         corrections=corrections, problems=problems, research=research or "(none)",
                         words=f"{lo}-{hi}", scene_rule="exactly {} scenes of {}-{} words".format(
                             *scene_plan(app.video_target_seconds)))
        current = generate_script(ctx, run, rewrite)

    run.write_json("review.json", rounds)
    run.write_json("script.json", current)
    assert review is not None
    run.stage("review").notes.update({"score": review.score, "hook_score": review.hook_score, "rounds": len(rounds)})
    if (review.score < app.review_min_score or any(c.verdict in ("incorrect", "unverifiable") for c in review.claims)) \
            and app.review_on_low_score == "fail":
        raise NeedsHumanReview(
            f"script scored {review.score} (min {app.review_min_score}) with "
            f"{sum(c.verdict in ('incorrect', 'unverifiable') for c in review.claims)} unresolved claim(s) after "
            f"{len(rounds)} review(s). Read review.json and script.json (edit it if you like), then "
            f"`shorts accept {run.id}` and `shorts resume {run.id}`; or `shorts resume {run.id} --force script`."
        )
    return review


def accept(run: Run) -> None:
    """Mark the review stage done without re-running it (after a human looked at the script)."""
    run.finish("review", accepted_by="human")
