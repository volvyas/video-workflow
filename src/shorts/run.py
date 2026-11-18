"""Run folder and manifest handling."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from shorts.models import Idea, Script, SceneTiming, WordTiming

STAGES = ["script", "review", "voice", "align", "music", "images", "approve", "motion", "assemble", "upload"]

# files/folders each stage produces (relative to the run folder)
OUTPUTS: dict[str, list[str]] = {
    "script": ["script.json", "script.original.json"],
    "review": ["review.json"],
    "voice": ["audio", "timings.json"],
    "align": ["words.json"],
    "music": [f"music.{ext}" for ext in ("wav", "mp3", "flac", "m4a", "ogg", "aac")],
    "images": ["images"],
    "approve": [],
    "motion": ["clips"],
    "assemble": ["segments", "captions.ass", "final.mp4"],
    "upload": [],
}

# stages whose outputs become stale when a stage is re-run
DEPENDENTS: dict[str, list[str]] = {
    "script": ["review", "voice", "align", "music", "images", "approve", "motion", "assemble", "upload"],
    "review": ["voice", "align", "music", "images", "approve", "motion", "assemble", "upload"],
    "voice": ["align", "music", "approve", "assemble", "upload"],  # music length follows the narration
    "align": ["assemble", "upload"],
    "music": ["approve", "assemble", "upload"],
    "images": ["approve", "motion", "assemble", "upload"],
    "approve": ["motion", "assemble", "upload"],
    "motion": ["assemble", "upload"],
    "assemble": ["upload"],
    "upload": [],
}


def slugify(text: str, max_len: int = 48) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:max_len].rstrip("-") or "untitled"


class StageRecord(BaseModel):
    status: str = "pending"  # pending | running | done | failed | waiting (storyboard approval)
    provider: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    notes: dict[str, Any] = Field(default_factory=dict)


class Manifest(BaseModel):
    run_id: str
    topic: str
    idea_id: str | None = None
    style_profile: str | None = None  # resume/redo use this style even if .env changed since
    seed: int
    created_at: float = Field(default_factory=time.time)
    stages: dict[str, StageRecord] = Field(default_factory=dict)
    upload_url: str | None = None


class Run:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._manifest: Manifest | None = None

    # ------------------------------------------------------------ creation
    @classmethod
    def create(cls, runs_dir: Path, topic: str, idea: Idea, seed: int, idea_id: str | None = None,
               style_profile: str | None = None) -> "Run":
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = Path(runs_dir) / f"{stamp}_{slugify(idea.title)}"
        n = 2
        while path.exists():
            path = path.with_name(f"{path.name}-{n}")
            n += 1
        path.mkdir(parents=True)
        run = cls(path)
        run._manifest = Manifest(run_id=path.name, topic=topic, idea_id=idea_id, seed=seed,
                                 style_profile=style_profile)
        run.write_json("idea.json", idea)
        run.save()
        return run

    @classmethod
    def open(cls, path: Path) -> "Run":
        run = cls(path)
        if not run.manifest_path.exists():
            raise FileNotFoundError(f"not a run folder (no manifest.json): {path}")
        return run

    # ------------------------------------------------------------ paths
    @property
    def id(self) -> str:
        return self.path.name

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    def sub(self, *parts: str) -> Path:
        p = self.path.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def scene_file(self, folder: str, index: int, ext: str) -> Path:
        return self.sub(folder, f"scene_{index + 1:02d}.{ext}")

    @property
    def final_video(self) -> Path:
        return self.path / "final.mp4"

    # ------------------------------------------------------------ manifest
    @property
    def manifest(self) -> Manifest:
        if self._manifest is None:
            self._manifest = Manifest.model_validate_json(self.manifest_path.read_text())
        return self._manifest

    def save(self) -> None:
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(self.manifest.model_dump_json(indent=2))
        tmp.replace(self.manifest_path)

    def stage(self, name: str) -> StageRecord:
        return self.manifest.stages.setdefault(name, StageRecord())

    def is_done(self, name: str) -> bool:
        return self.stage(name).status == "done"

    def start(self, name: str, provider: str | None) -> None:
        rec = self.stage(name)
        rec.status, rec.provider, rec.started_at, rec.error = "running", provider, time.time(), None
        self.save()

    def finish(self, name: str, **notes: Any) -> None:
        rec = self.stage(name)
        rec.status, rec.finished_at = "done", time.time()
        rec.notes.update(notes)
        self.save()

    def fail(self, name: str, error: str) -> None:
        rec = self.stage(name)
        rec.status, rec.finished_at, rec.error = "failed", time.time(), error
        self.save()

    def invalidate(self, name: str, delete_outputs: bool = True) -> None:
        """Re-run `name` from scratch: delete its outputs and those of dependent stages, mark them pending."""
        import shutil

        for s in [name, *DEPENDENTS[name]]:
            if delete_outputs:
                for rel in OUTPUTS[s]:
                    p = self.path / rel
                    if p.is_dir():
                        shutil.rmtree(p)
                    elif p.exists():
                        p.unlink()
            self.manifest.stages[s] = StageRecord()
        self.save()

    def mark_pending(self, stages: list[str]) -> None:
        for s in stages:
            self.manifest.stages[s] = StageRecord()
        self.save()

    # ------------------------------------------------------------ artifacts
    def write_json(self, name: str, obj: BaseModel | list | dict) -> Path:
        p = self.sub(name)
        if isinstance(obj, BaseModel):
            p.write_text(obj.model_dump_json(indent=2))
        else:
            data = [o.model_dump() if isinstance(o, BaseModel) else o for o in obj] if isinstance(obj, list) else obj
            p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return p

    def idea(self) -> Idea:
        return Idea.model_validate_json((self.path / "idea.json").read_text())

    def script(self) -> Script:
        return Script.model_validate_json((self.path / "script.json").read_text())

    def timings(self) -> list[SceneTiming]:
        return [SceneTiming.model_validate(t) for t in json.loads((self.path / "timings.json").read_text())]

    def words(self) -> list[WordTiming]:
        return [WordTiming.model_validate(w) for w in json.loads((self.path / "words.json").read_text())]
