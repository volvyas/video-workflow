"""Build styled ASS subtitles: short word groups with the spoken word highlighted (karaoke style)."""

from __future__ import annotations

import re

from shorts.config import AppSettings
from shorts.models import Scene, SceneTiming, WordTiming


def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"color must be #RRGGBB, got {hex_color}")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str) -> str:
    return re.sub(r"[{}\\]", "", text)


def group_words(words: list[WordTiming], max_words: int) -> list[list[WordTiming]]:
    groups: list[list[WordTiming]] = []
    cur: list[WordTiming] = []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or w.word[-1:] in ".!?,;:":
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def build_ass(app: AppSettings, words: list[WordTiming], scenes: list[Scene] | None = None,
              timings: list[SceneTiming] | None = None) -> str:
    W, H = app.video_width, app.video_height
    primary = ass_color(app.caption_color)
    outline = ass_color(app.caption_outline_color)
    highlight = ass_color(app.caption_highlight_color)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{app.caption_font},{app.caption_font_size},{primary},{highlight},{outline},&H80000000,-1,0,0,0,100,100,0,0,1,{app.caption_outline},2,2,60,60,{app.caption_margin_bottom},1
Style: Overlay,{app.caption_font},{int(app.caption_font_size * 0.8)},{highlight},{primary},{outline},&H80000000,-1,0,0,0,100,100,0,0,1,{app.caption_outline},2,8,60,60,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines: list[str] = []
    groups = group_words(words, max(1, app.caption_words_per_group))
    for gi, group in enumerate(groups):
        g_end = groups[gi + 1][0].start if gi + 1 < len(groups) else group[-1].end + 0.4
        g_end = min(g_end, group[-1].end + 0.6)  # don't linger over pauses between scenes
        tokens = [_clean(w.word.upper() if app.caption_uppercase else w.word) for w in group]
        for wi, w in enumerate(group):
            start = group[0].start if wi == 0 else w.start
            end = group[wi + 1].start if wi + 1 < len(group) else g_end
            if end <= start:
                continue
            parts = []
            for ti, tok in enumerate(tokens):
                if ti == wi:
                    parts.append(r"{\c" + highlight + r"&\fscx108\fscy108}" + tok + r"{\c" + primary + r"&\fscx100\fscy100}")
                else:
                    parts.append(tok)
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{' '.join(parts)}")

    if scenes and timings:
        for scene, t in zip(scenes, timings):
            text = _clean(scene.on_screen_text.strip())
            if text:
                fade = r"{\fad(150,150)}"
                lines.append(
                    f"Dialogue: 1,{ass_time(t.start)},{ass_time(t.start + t.slot)},Overlay,,0,0,0,,{fade}{text.upper()}"
                )
    return header + "\n".join(lines) + "\n"
