"""YouTube Data API v3 uploader (OAuth installed-app flow). Install with `uv sync --extra youtube`.

The first upload opens a browser for consent; the token is cached in UPLOAD_YOUTUBE_TOKEN_FILE.
Quota: an upload costs ~1600 of the default 10,000 units/day, so about 6 uploads per day.
"""

from __future__ import annotations

from pathlib import Path

from shorts.models import VideoMeta
from shorts.providers.base import ProviderError, ProviderSettings, Uploader

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class YouTubeSettings(ProviderSettings):
    client_secrets: Path = Path("secrets/client_secret.json")
    token_file: Path = Path("secrets/youtube_token.json")
    privacy: str = "private"  # private | unlisted | public
    category_id: str = "27"  # 27 Education, 28 Science & Tech, 24 Entertainment, 22 People & Blogs
    default_language: str | None = None  # e.g. "en"
    made_for_kids: bool = False


class YouTubeUploader(Uploader):
    name = "youtube"
    env_prefix = "UPLOAD_YOUTUBE_"
    Settings = YouTubeSettings
    settings: YouTubeSettings

    def verify(self) -> None:
        self._require_module("googleapiclient", "youtube")
        if not self.settings.token_file.exists() and not self.settings.client_secrets.exists():
            raise ProviderError(f"no OAuth client file at {self.settings.client_secrets} (docs/SETUP.md §8)")

    def _credentials(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
        except ImportError as e:
            raise ProviderError("YouTube libraries missing: run `uv sync --extra youtube`") from e
        s = self.settings
        creds = Credentials.from_authorized_user_file(str(s.token_file), SCOPES) if s.token_file.exists() else None
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        elif not creds or not creds.valid:
            if not s.client_secrets.exists():
                raise ProviderError(f"OAuth client file not found: {s.client_secrets} (see docs/SETUP.md §YouTube)")
            flow = InstalledAppFlow.from_client_secrets_file(str(s.client_secrets), SCOPES)
            creds = flow.run_local_server(port=0)
        s.token_file.parent.mkdir(parents=True, exist_ok=True)
        s.token_file.write_text(creds.to_json())
        return creds

    def upload(self, video: Path, meta: VideoMeta) -> str:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        yt = build("youtube", "v3", credentials=self._credentials(), cache_discovery=False)
        snippet = {"title": meta.title[:100], "description": meta.description[:5000],
                   "tags": meta.tags[:30], "categoryId": self.settings.category_id}
        if self.settings.default_language:
            snippet["defaultLanguage"] = snippet["defaultAudioLanguage"] = self.settings.default_language
        status = {"privacyStatus": meta.privacy, "selfDeclaredMadeForKids": meta.made_for_kids,
                  "containsSyntheticMedia": meta.synthetic_media}
        if meta.publish_at:
            status["privacyStatus"] = "private"  # required for scheduled publishing
            status["publishAt"] = meta.publish_at
        media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        req = yt.videos().insert(part="snippet,status", body={"snippet": snippet, "status": status}, media_body=media)
        response = None
        while response is None:
            progress, response = req.next_chunk()
            if progress:
                self.log.info("upload %d%%", int(progress.progress() * 100))
        return f"https://youtube.com/shorts/{response['id']}"
