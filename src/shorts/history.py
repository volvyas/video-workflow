"""Script history: every run's idea/script with a status.

Used to (1) warn when a new idea or script is similar to something already made, and (2) steer idea generation
away from repeats and towards what was popular.

Statuses
  draft          script written, video not finished yet                       (automatic)
  auto_rejected  stopped by a quality gate: failed review or too similar        (automatic)
  rejected       you rejected it                                                (manual: shorts history mark)
  created        final.mp4 produced                                             (automatic)
  uploaded       uploaded with `shorts upload`                                  (automatic)
  popular        did well with the audience                                     (manual)

Automatic updates never overwrite a manual status (rejected / popular).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args

from pydantic import BaseModel, Field

from shorts.config import AppSettings

if TYPE_CHECKING:
    from shorts.context import Context
    from shorts.run import Run

log = logging.getLogger("shorts.history")

Status = Literal["draft", "auto_rejected", "rejected", "created", "uploaded", "popular"]
STATUSES: list[str] = list(get_args(Status))
MANUAL = {"rejected", "popular"}


class SimilarContent(RuntimeError):
    pass


class StatusChange(BaseModel):
    status: Status
    at: float = Field(default_factory=time.time)
    by: Literal["auto", "human"] = "auto"
    note: str = ""


class HistoryEntry(BaseModel):
    id: str  # run id
    topic: str
    title: str
    hook: str = ""
    angle: str = ""
    script_title: str = ""
    narration: str = ""
    status: Status = "draft"
    review_score: int | None = None
    upload_url: str | None = None
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    log: list[StatusChange] = Field(default_factory=list)

    @property
    def idea_text(self) -> str:
        return f"{self.title}. {self.hook} {self.angle}".strip()

    @property
    def script_text(self) -> str:
        return f"{self.script_title or self.title}. {self.narration}" if self.narration else self.idea_text


class History(BaseModel):
    entries: list[HistoryEntry] = Field(default_factory=list)

    def get(self, run_id: str) -> HistoryEntry | None:
        return next((e for e in self.entries if e.id == run_id), None)


@dataclass
class Match:
    entry: HistoryEntry
    score: float

    def __str__(self) -> str:
        return f'{self.entry.id} [{self.entry.status}] "{self.entry.title}" (similarity {self.score:.2f})'


# ---------------------------------------------------------------- storage
def load(app: AppSettings) -> History:
    p = Path(app.history_file)
    if p.exists():
        return History.model_validate_json(p.read_text(encoding="utf-8"))
    return History()


def save(app: AppSettings, h: History) -> None:
    p = Path(app.history_file)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(h.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(p)


def _apply_status(e: HistoryEntry, status: str, by: str, note: str) -> bool:
    if by == "auto" and e.status in MANUAL:
        return False
    if e.status == status and not note:
        return False
    e.status = status  # type: ignore[assignment]
    e.updated_at = time.time()
    e.log.append(StatusChange(status=status, by=by, note=note))  # type: ignore[arg-type]
    return True


def upsert_from_run(app: AppSettings, run: "Run", status: str | None = None, *, by: str = "auto",
                    note: str = "") -> HistoryEntry:
    """Create/refresh the entry of a run from its files, optionally changing the status."""
    h = load(app)
    e = h.get(run.id)
    idea = run.idea()
    if e is None:
        status = status or "draft"
        e = HistoryEntry(id=run.id, topic=run.manifest.topic, title=idea.title, created_at=run.manifest.created_at,
                         status=status, log=[StatusChange(status=status, by=by, note=note)])  # type: ignore[arg-type]
        h.entries.append(e)
        status = None
    e.title, e.hook, e.angle = idea.title, idea.hook, idea.angle
    if (run.path / "script.json").exists():
        s = run.script()
        e.script_title, e.narration = s.title, s.narration
    score = run.stage("review").notes.get("score")
    if score is not None:
        e.review_score = score
    if run.manifest.upload_url:
        e.upload_url = run.manifest.upload_url
    e.updated_at = time.time()
    if status:
        _apply_status(e, status, by, note)
    save(app, h)
    return e


def set_status(app: AppSettings, run_id: str, status: str, *, by: str = "human", note: str = "") -> HistoryEntry:
    if status not in STATUSES:
        raise ValueError(f"unknown status '{status}', use one of: {', '.join(STATUSES)}")
    h = load(app)
    e = h.get(run_id)
    if e is None:
        raise KeyError(f"'{run_id}' is not in the history ({app.history_file}); try `shorts history sync`")
    _apply_status(e, status, by, note)
    save(app, h)
    return e


# ---------------------------------------------------------------- similarity
def find_similar(ctx: "Context", text: str, *, field: Literal["idea", "script"] = "idea",
                 exclude: set[str] | None = None, history: History | None = None) -> list[Match]:
    """History entries similar to `text` (score >= the similarity provider's threshold), best first."""
    sim = ctx.similarity()
    if sim is None:
        return []
    h = history or load(ctx.app)
    entries = [e for e in h.entries if e.id not in (exclude or set())]
    if not entries or not text.strip():
        return []
    docs = [e.idea_text if field == "idea" else e.script_text for e in entries]
    scores = sim.scores(text, docs)
    out = [Match(e, s) for e, s in zip(entries, scores) if s >= sim.threshold]
    return sorted(out, key=lambda m: m.score, reverse=True)


def _gate(ctx: "Context", run: "Run", what: str, matches: list[Match]) -> None:
    if not matches:
        return
    lines = [str(m) for m in matches[:3]]
    notes = run.stage("script").notes.setdefault("similar", [])
    for line in lines:
        if f"{what}: {line}" not in notes:
            notes.append(f"{what}: {line}")
    run.save()
    log.warning("[%s] %s is similar to earlier content:\n  %s", run.id, what, "\n  ".join(lines))
    if ctx.app.history_on_similar == "fail":
        raise SimilarContent(
            f"{what} is similar to {matches[0]}. Mark the old one with `shorts history mark`, pick another idea, "
            f"or continue anyway: HISTORY_ON_SIMILAR=warn shorts resume {run.id}")


def check_idea(ctx: "Context", run: "Run") -> None:
    if not ctx.app.history_enabled:
        return
    idea = run.idea()
    _gate(ctx, run, "idea", find_similar(ctx, f"{idea.title}. {idea.hook} {idea.angle}", field="idea",
                                         exclude={run.id}))


def check_script(ctx: "Context", run: "Run") -> None:
    if not ctx.app.history_enabled:
        return
    s = run.script()
    _gate(ctx, run, "script", find_similar(ctx, f"{s.title}. {s.narration}", field="script", exclude={run.id}))


# ---------------------------------------------------------------- pipeline hooks
_ON_DONE = {"script": "draft", "review": "draft", "assemble": "created", "upload": "uploaded"}


def record(ctx: "Context", run: "Run", stage: str) -> None:
    """Called after a stage finished. Never raises."""
    if not ctx.app.history_enabled or stage not in _ON_DONE:
        return
    try:
        upsert_from_run(ctx.app, run, _ON_DONE[stage])
    except Exception as e:  # noqa: BLE001
        log.warning("history update failed for %s: %s", run.id, e)


def record_failure(ctx: "Context", run: "Run", stage: str, error: BaseException) -> None:
    """Called after a stage failed: quality-gate failures become auto_rejected. Never raises."""
    if not ctx.app.history_enabled:
        return
    from shorts.stages.review import NeedsHumanReview
    from shorts.stages.script import FalsePremise

    if not isinstance(error, (NeedsHumanReview, SimilarContent, FalsePremise)):
        return
    try:
        if not (run.path / "idea.json").exists():
            return
        upsert_from_run(ctx.app, run, "auto_rejected", note=f"{stage}: {error}"[:500])
    except Exception as e:  # noqa: BLE001
        log.warning("history update failed for %s: %s", run.id, e)


def status_from_run(run: "Run") -> str:
    if run.is_done("upload"):
        return "uploaded"
    if run.is_done("assemble"):
        return "created"
    if run.stage("review").status == "failed" or run.stage("script").status == "failed" and \
            "similar" in (run.stage("script").error or ""):
        return "auto_rejected"
    return "draft"


def sync(ctx: "Context") -> list[HistoryEntry]:
    """Add/refresh entries for every run folder (backfill for runs made before history existed)."""
    from shorts.run import Run

    out = []
    for p in sorted(Path(ctx.app.runs_dir).glob("*/manifest.json")):
        run = Run.open(p.parent)
        if (run.path / "idea.json").exists():
            out.append(upsert_from_run(ctx.app, run, status_from_run(run)))
    return out


# ---------------------------------------------------------------- idea generation
def _why(e: HistoryEntry) -> str:
    """Your note on a rejection, so the ideas LLM learns what you don't want."""
    if e.status != "rejected":
        return ""
    note = next((c.note for c in reversed(e.log) if c.by == "human" and c.note), "")
    return f" (rejected because: {note})" if note else ""


def prompt_context(app: AppSettings, topic: str) -> str:
    """History summary for the ideas prompt: what to avoid and what the audience liked."""
    if not app.history_enabled:
        return "(none)"
    h = load(app)
    limit = app.history_prompt_limit
    same = [e for e in h.entries if e.topic == topic and e.status != "popular"][-limit:]
    popular = [e for e in h.entries if e.status == "popular"][-limit:]
    parts = []
    if same:
        parts.append("Already made or tried for this topic (do NOT repeat these subjects or facts; avoid the "
                     "reasons given for rejected ones):\n" + "\n".join(f"- [{e.status}] {e.title}: {e.hook}"
                                                                   + _why(e) for e in same))
    if popular:
        parts.append("POPULAR with our audience (new ideas with a similar kind of hook and payoff are welcome, "
                     "but about a different subject):\n" + "\n".join(f"- {e.title}: {e.hook}" for e in popular))
    return "\n\n".join(parts) or "(none)"
