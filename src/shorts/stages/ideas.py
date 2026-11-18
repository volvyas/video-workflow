"""Ideas stage: topic -> pool of ideas (ideas/<topic-slug>.json), deduplicated across calls."""

from __future__ import annotations

import re
from pathlib import Path

from shorts.config import AppSettings
from shorts.context import Context
from shorts.models import Idea, IdeaBatch, IdeaPool, PremiseCheck, StoredIdea
from shorts.prompts import render
from shorts.run import slugify


def _norm(s: str) -> str:
    return re.sub(r"\W+", " ", s.lower()).strip()


def pool_path(app: AppSettings, topic: str) -> Path:
    return Path(app.ideas_dir) / f"{slugify(topic)}.json"


def load_pool(app: AppSettings, topic: str) -> IdeaPool:
    p = pool_path(app, topic)
    if p.exists():
        return IdeaPool.model_validate_json(p.read_text(encoding="utf-8"))
    return IdeaPool(topic=topic)


def save_pool(app: AppSettings, pool: IdeaPool) -> None:
    p = pool_path(app, pool.topic)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(pool.model_dump_json(indent=2), encoding="utf-8")


def generate_ideas(ctx: Context, topic: str, count: int) -> list[StoredIdea]:
    app = ctx.app
    pool = load_pool(app, topic)
    existing = "\n".join(f"- {i.title}: {i.angle}" for i in pool.ideas) or "(none yet)"
    system = render("ideas_system", app.prompts_dir, channel_brief=app.channel_brief,
                    seconds=app.video_target_seconds, language=app.content_language)
    from shorts import history

    prompt = render("ideas_user", app.prompts_dir, topic=topic, count=count, existing=existing,
                    history=history.prompt_context(app, topic))
    batch = ctx.llm("ideas").generate_json(system=system, prompt=prompt, schema=IdeaBatch)

    seen = {_norm(i.title) for i in pool.ideas}
    past = history.load(app)
    base = slugify(topic, 24)
    new: list[StoredIdea] = []
    for idea in batch.ideas:
        key = _norm(idea.title)
        if not key or key in seen:
            continue
        seen.add(key)
        stored = StoredIdea(id=f"{base}-{len(pool.ideas) + 1:03d}", topic=topic, **idea.model_dump())
        if app.history_enabled:
            stored.similar_to = [str(m) for m in history.find_similar(
                ctx, f"{idea.title}. {idea.hook} {idea.angle}", field="idea", history=past)[:3]]
        pool.ideas.append(stored)
        new.append(stored)
    save_pool(app, pool)
    if app.ideas_fact_check:
        for stored in new:
            stored.premise = check_premise(ctx, topic, stored)
            save_pool(app, pool)  # after each check: a long batch keeps its progress
    return new


def check_premise(ctx: Context, topic: str, idea: Idea) -> PremiseCheck:
    """Web research + LLM verdict on the idea's central claim."""
    import logging

    from shorts.stages.research import research

    app = ctx.app
    notes = research(ctx, topic, f"{idea.title}. {idea.hook} {idea.angle}") if app.review_web_search else ""
    check = ctx.llm("review").generate_json(
        system=render("premise_system", app.prompts_dir, channel_brief=app.channel_brief),
        prompt=render("premise_user", app.prompts_dir, topic=topic, title=idea.title, hook=idea.hook,
                      angle=idea.angle, research=notes or "(none)"),
        schema=PremiseCheck)
    logging.getLogger("shorts.stage.ideas").info("premise of '%s': %s (%s)", idea.title, check.verdict,
                                                 check.explanation[:120])
    return check


def premise_note(check: PremiseCheck | None) -> str:
    """Line for the script prompt."""
    if check is None or check.verdict == "true":
        return ""
    if check.verdict == "misleading" and check.corrected_premise:
        return (f"Premise check: the idea as stated is misleading ({check.explanation}). Build the script on this "
                f"accurate premise instead: {check.corrected_premise}")
    if check.verdict == "unverifiable":
        return f"Premise check: unverifiable ({check.explanation}). Only state what is well established."
    return f"Premise check: {check.verdict}. {check.explanation}"


def take_unused(app: AppSettings, topic: str, n: int) -> list[StoredIdea]:
    """Unused ideas, skipping those that resemble earlier videos (use them explicitly with --idea)."""
    return [i for i in load_pool(app, topic).ideas if not i.used_by and not i.similar_to
            and not (i.premise and i.premise.verdict == "false")][:n]


def find_idea(app: AppSettings, topic: str, idea_id: str) -> StoredIdea:
    for i in load_pool(app, topic).ideas:
        if i.id == idea_id:
            return i
    raise KeyError(f"idea '{idea_id}' not found in {pool_path(app, topic)}")


def mark_used(app: AppSettings, topic: str, idea_id: str, run_id: str) -> None:
    pool = load_pool(app, topic)
    for i in pool.ideas:
        if i.id == idea_id:
            i.used_by = run_id
    save_pool(app, pool)
