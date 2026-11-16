"""Prepare images for vision LLMs: downscale and JPEG-encode (cuts tokens/bandwidth, keeps quality for review)."""

from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image


def encode_jpeg_b64(path: Path, max_side: int = 1024, quality: int = 88) -> str:
    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")
