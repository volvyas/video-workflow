"""Sound effects: peak aligned to the cut and leveled."""

import pytest

from shorts.media import ffmpeg
from shorts.media.assemble import _sfx_profile, sfx_plan
from test_pipeline_e2e import make_ctx


def _tone(path, delay, dur, vol):
    """Silence for `delay` s, then a tone burst of `dur` s at `vol`, then a bit of silence."""
    ffmpeg.run(["-f", "lavfi", "-i", f"sine=frequency=800:duration={dur}", "-af",
                f"volume={vol},adelay={int(delay * 1000)}:all=1,apad=pad_dur=0.2", path])
    return path


def test_peak_lands_on_the_cut_and_levels_match(tmp_path):
    click = _tone(tmp_path / "click.wav", 0.0, 0.05, 0.9)           # loud, peaks at once
    whoosh = _tone(tmp_path / "whoosh.wav", 0.6, 0.2, 0.3)          # much quieter, peaks ~0.7 s in
    app = make_ctx(tmp_path).app
    (pc, tc), (pw, tw) = (_sfx_profile(str(p), p.stat().st_mtime_ns) for p in (click, whoosh))
    assert pc < 0.08 and 0.6 < pw < 0.82
    plan = sfx_plan(app, [click, whoosh], [3.0, 8.0])
    assert plan[0]["delay_ms"] == pytest.approx((3.0 - pc) * 1000, abs=2)
    assert plan[1]["delay_ms"] == pytest.approx((8.0 - pw) * 1000, abs=2)
    # after the gain both peak at the same level (SFX_LEVEL_DB, scaled by SFX_VOLUME)
    import math
    lv = [db + 20 * math.log10(p["gain"]) for db, p in ((tc, plan[0]), (tw, plan[1]))]
    assert lv[0] == pytest.approx(lv[1], abs=0.5)


def test_effect_longer_than_the_time_before_the_cut_is_trimmed(tmp_path):
    late = _tone(tmp_path / "late.wav", 0.9, 0.1, 0.5)
    (p,) = sfx_plan(make_ctx(tmp_path).app, [late], [0.4])
    assert p["delay_ms"] == 0 and p["skip"] == pytest.approx(p["peak_at"] - 0.4, abs=0.01)


def test_start_mode_and_level_off(tmp_path):
    f = _tone(tmp_path / "w.wav", 0.6, 0.2, 0.05)
    (p,) = sfx_plan(make_ctx(tmp_path, sfx_align="start", sfx_level_db="off").app, [f], [5.0])
    assert p["delay_ms"] == round((5.0 - 0.12) * 1000) and p["gain"] == 0.35


def test_gain_is_capped_for_near_silent_files(tmp_path):
    faint = _tone(tmp_path / "faint.wav", 0.0, 0.2, 0.01)  # about -55 dB
    (p,) = sfx_plan(make_ctx(tmp_path).app, [faint], [2.0])
    assert p["gain"] == pytest.approx(0.35 * 10 ** (24 / 20), rel=1e-3)
