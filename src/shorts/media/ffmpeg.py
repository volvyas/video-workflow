"""Locate and run ffmpeg; probe media durations (works without ffprobe)."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import wave
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("shorts.ffmpeg")


class FFmpegError(RuntimeError):
    pass


_configured: dict[str, str | None] = {"ffmpeg": None, "ffprobe": None}


def configure(ffmpeg_path: str | None = None, ffprobe_path: str | None = None) -> None:
    _configured["ffmpeg"], _configured["ffprobe"] = ffmpeg_path, ffprobe_path
    ffmpeg_bin.cache_clear()
    ffprobe_bin.cache_clear()


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    if _configured["ffmpeg"]:
        return _configured["ffmpeg"]
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise FFmpegError("ffmpeg not found: install it (apt install ffmpeg) or `uv sync --extra ffmpeg`")


@lru_cache(maxsize=1)
def ffprobe_bin() -> str | None:
    return _configured["ffprobe"] or shutil.which("ffprobe")


def run(args: list[str], *, quiet: bool = True) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_bin(), "-hide_banner", "-y", *(["-loglevel", "error"] if quiet else []), *map(str, args)]
    log.debug("ffmpeg %s", " ".join(cmd[1:]))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()[-2000:]}")
    return proc


def duration(path: Path) -> float:
    path = Path(path)
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path)) as w:
                return w.getnframes() / float(w.getframerate())
        except wave.Error:
            pass  # e.g. float WAV; fall through
    probe = ffprobe_bin()
    if probe:
        out = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True,
        )
        try:
            return float(out.stdout.strip())
        except ValueError:
            pass
    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not m:
        raise FFmpegError(f"cannot determine duration of {path}")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def loudness(path: Path) -> float | None:
    """Integrated loudness in LUFS (EBU R128), or None if it can't be measured (e.g. silence)."""
    import re

    proc = subprocess.run([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(path), "-filter_complex", "ebur128",
                           "-f", "null", "-"], capture_output=True, text=True)
    found = re.findall(r"^\s+I:\s+(-?[\d.]+) LUFS", proc.stderr, re.M)
    if not found:
        return None
    v = float(found[-1])
    return v if v > -70 else None


def concat_audio(inputs: list[Path], gaps: list[float], out: Path, sample_rate: int = 44100) -> None:
    """Concatenate audio files, appending gaps[i] seconds of silence after input i."""
    args: list[str] = []
    for p in inputs:
        args += ["-i", str(p)]
    parts = []
    for i, gap in enumerate(gaps):
        parts.append(
            f"[{i}:a]aresample={sample_rate},aformat=sample_fmts=fltp:channel_layouts=mono,"
            f"apad=pad_dur={gap:.3f}[a{i}]"
        )
    labels = "".join(f"[a{i}]" for i in range(len(inputs)))
    graph = ";".join(parts) + f";{labels}concat=n={len(inputs)}:v=0:a=1[out]"
    run([*args, "-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s16le", str(out)])
