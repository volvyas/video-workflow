"""Mock image: a gradient with the prompt written on it."""

from __future__ import annotations

import random
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from shorts.providers.base import ImageProvider, ProviderSettings


class MockImageSettings(ProviderSettings):
    pass


class MockImage(ImageProvider):
    name = "mock"
    env_prefix = "IMAGE_MOCK_"
    Settings = MockImageSettings

    def generate(self, *, prompt: str, negative: str, width: int, height: int, seed: int, out_path: Path) -> Path:
        rnd = random.Random(seed)
        c1 = [rnd.randint(20, 200) for _ in range(3)]
        c2 = [rnd.randint(20, 200) for _ in range(3)]
        col = Image.new("RGB", (1, height))
        col.putdata([tuple(int(c1[k] * (1 - y / height) + c2[k] * y / height) for k in range(3))
                     for y in range(height)])
        img = col.resize((width, height))
        d = ImageDraw.Draw(img)
        for i in range(0, width + height, 80):  # diagonal stripes make camera motion visible
            d.line([(i, 0), (i - height, height)], fill=(255, 255, 255), width=3)
        try:
            font = ImageFont.load_default(size=max(24, width // 20))
        except TypeError:
            font = ImageFont.load_default()
        d.multiline_text((40, height // 3), "\n".join(textwrap.wrap(prompt[:200], 24)), fill="white", font=font,
                         stroke_width=3, stroke_fill="black")
        img.save(out_path)
        return out_path
