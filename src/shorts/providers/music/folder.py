"""Background music from a folder of your own tracks (MUSIC_DIR), or no music at all."""

from __future__ import annotations

import random
import shutil
from pathlib import Path

from shorts.providers.base import MusicProvider

MUSIC_EXT = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}


class FolderMusic(MusicProvider):
    """A random track from MUSIC_DIR (same seed → same track). Loops in assembly if shorter than the video."""

    name = "folder"
    remote = True

    def generate(self, *, tags: str, bpm: int, key: str, seconds: float, seed: int, out_path: Path) -> Path | None:
        d = self.app.music_dir
        if not d or not Path(d).is_dir():
            return None
        tracks = sorted(p for p in Path(d).iterdir() if p.suffix.lower() in MUSIC_EXT)
        if not tracks:
            return None
        track = random.Random(seed).choice(tracks)
        dest = out_path.with_suffix(track.suffix.lower())
        shutil.copyfile(track, dest)
        self.log.info("music: %s", track.name)
        return dest


class NoMusic(MusicProvider):
    name = "none"
    remote = True

    def generate(self, *, tags: str, bpm: int, key: str, seconds: float, seed: int, out_path: Path) -> Path | None:
        return None
