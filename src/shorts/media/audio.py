"""Small audio helpers that don't need extra dependencies."""

from __future__ import annotations

import struct
import wave
from collections.abc import Iterable
from pathlib import Path


def write_wav(path: Path, samples: Iterable[float], sample_rate: int) -> float:
    """Write mono float samples (-1..1) as 16-bit PCM WAV; returns the duration in seconds."""
    try:  # fast path for numpy arrays / torch tensors
        import numpy as np

        arr = np.asarray(samples, dtype=np.float32).reshape(-1)
        data = (np.clip(arr, -1.0, 1.0) * 32767).astype("<i2").tobytes()
        n = arr.shape[0]
    except ImportError:
        vals = [max(-1.0, min(1.0, float(s))) for s in samples]
        data = struct.pack(f"<{len(vals)}h", *(int(v * 32767) for v in vals))
        n = len(vals)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(data)
    return n / sample_rate
