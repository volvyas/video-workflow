from __future__ import annotations

from pathlib import Path

from shorts.models import VideoMeta
from shorts.providers.base import ProviderSettings, Uploader


class NoUploadSettings(ProviderSettings):
    pass


class NoUpload(Uploader):
    """Does nothing; the video stays in its run folder."""

    name = "none"
    env_prefix = "UPLOAD_NONE_"
    Settings = NoUploadSettings

    def upload(self, video: Path, meta: VideoMeta) -> str:
        return str(Path(video).resolve())
