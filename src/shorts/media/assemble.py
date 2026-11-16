"""Final assembly: normalize scene clips, fit them to the narration, mix audio, burn captions."""

from __future__ import annotations

import logging
import random
from pathlib import Path

from shorts.config import AppSettings
from shorts.media import ffmpeg
from shorts.media.captions import build_ass
from shorts.run import Run

log = logging.getLogger("shorts.assemble")

_MUSIC_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}


def _filter_escape(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def pick_sfx(app: AppSettings, seed: int, count: int) -> list[Path]:
    d = app.sfx_dir
    if count <= 0 or not d or not Path(d).is_dir():
        return []
    files = sorted(p for p in Path(d).iterdir() if p.suffix.lower() in _MUSIC_EXT)
    if not files:
        return []
    rnd = random.Random(seed + 7)
    return [rnd.choice(files) for _ in range(count)]


def pick_music(app: AppSettings, seed: int) -> Path | None:
    d = app.music_dir
    if not d or not Path(d).is_dir():
        return None
    tracks = sorted(p for p in Path(d).iterdir() if p.suffix.lower() in _MUSIC_EXT)
    return random.Random(seed).choice(tracks) if tracks else None


def normalize_clip(app: AppSettings, clip: Path, target: float, out: Path) -> None:
    """Scale/crop to the output frame, then trim, or slow down (≤ max_slowdown) and freeze, to hit `target`."""
    W, H, fps = app.video_width, app.video_height, app.video_fps
    src = ffmpeg.duration(clip)
    chain = []
    if src < target - 0.02:
        factor = min(target / max(src, 0.01), app.max_slowdown)
        if factor > 1.001:
            chain.append(f"setpts={factor:.4f}*PTS")
        remaining = target - src * factor
        if remaining > 0.01:
            chain.append(f"tpad=stop_mode=clone:stop_duration={remaining + 0.1:.3f}")
    chain += [f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos", f"crop={W}:{H}", "setsar=1",
              f"fps={fps}", f"trim=duration={target:.3f}", "setpts=PTS-STARTPTS"]
    ffmpeg.run(["-i", clip, "-an", "-vf", ",".join(chain), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "17", "-pix_fmt", "yuv420p", out])


def assemble(app: AppSettings, run: Run, clips: list[Path]) -> Path:
    timings = run.timings()
    script = run.script()
    seg_dir = run.path / "segments"
    seg_dir.mkdir(exist_ok=True)
    segs = []
    for clip, t in zip(clips, timings):
        seg = seg_dir / f"seg_{t.index + 1:02d}.mp4"
        normalize_clip(app, clip, t.slot, seg)
        segs.append(seg)
    concat_list = seg_dir / "list.txt"
    concat_list.write_text("".join(f"file '{s.resolve()}'\n" for s in segs))

    total = sum(t.slot for t in timings)
    args: list[str] = ["-f", "concat", "-safe", "0", "-i", concat_list, "-i", run.path / "audio" / "voice.wav"]
    music = pick_music(app, run.manifest.seed)
    if music:
        args += ["-stream_loop", "-1", "-i", music]
    cuts = [t.start for t in timings[1:]]  # a whoosh on every scene change
    sfx = pick_sfx(app, run.manifest.seed, len(cuts))
    sfx_first_input = 3 if music else 2
    for f in sfx:
        args += ["-i", f]

    vf = "[0:v]null[v]"
    if app.captions_enabled:
        ass_path = run.path / "captions.ass"
        ass_path.write_text(build_ass(app, run.words(), script.scenes, timings), encoding="utf-8")
        sub = f"subtitles='{_filter_escape(ass_path.resolve())}'"
        if app.caption_fonts_dir and Path(app.caption_fonts_dir).is_dir():
            sub += f":fontsdir='{_filter_escape(Path(app.caption_fonts_dir).resolve())}'"
        vf = f"[0:v]{sub}[v]"

    loud = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"
    parts, mix = [], []
    if music:
        parts += [
            "[1:a]aresample=48000,asplit=2[voice][key]",
            f"[2:a]aresample=48000,volume={app.music_volume}[m]",
            "[m][key]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[duck]",
        ]
        mix += ["[voice]", "[duck]"]
    else:
        parts.append("[1:a]aresample=48000[voice]")
        mix.append("[voice]")
    for k, (f, cut) in enumerate(zip(sfx, cuts)):
        delay = max(0, int((cut - app.sfx_lead) * 1000))
        parts.append(f"[{sfx_first_input + k}:a]aresample=48000,aformat=channel_layouts=mono,"
                     f"volume={app.sfx_volume},adelay=delays={delay}:all=1[s{k}]")
        mix.append(f"[s{k}]")
    if len(mix) > 1:
        parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0,{loud}[a]")
    else:
        parts.append(f"[voice]{loud}[a]")
    af = ";".join(parts)

    out = run.final_video
    ffmpeg.run([*args, "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                "-r", str(app.video_fps), "-c:a", "aac", "-ar", "48000", "-b:a", "192k", "-movflags", "+faststart", out])
    log.info("[%s] final video: %s (%.1fs%s%s)", run.id, out, total, f", music: {music.name}" if music else "",
             f", {len(sfx)} sfx" if sfx else "")
    return out
