"""Minimal ComfyUI HTTP API client."""

from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("shorts.comfy")

_VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".gif", ".webp"}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
_AUDIO_EXT = {".flac", ".wav", ".mp3", ".opus", ".ogg", ".m4a"}
_EXTS = {"image": _IMAGE_EXT, "video": _VIDEO_EXT, "audio": _AUDIO_EXT}


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, url: str, timeout: float = 1800, poll_interval: float = 1.0):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.client_id = uuid.uuid4().hex
        self._http = httpx.Client(base_url=self.url, timeout=60)

    # ---------------------------------------------------------------- basics
    def ping(self) -> dict[str, Any]:
        try:
            r = self._http.get("/system_stats")
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise ComfyError(f"ComfyUI not reachable at {self.url}: {e}") from e

    def upload_image(self, path: Path, name: str | None = None) -> str:
        """Upload a file (image or audio) to ComfyUI's input folder; returns the name to use in LoadImage/LoadAudio."""
        name = name or f"shorts_{uuid.uuid4().hex[:8]}_{Path(path).name}"
        mime = "audio/wav" if Path(path).suffix.lower() in _AUDIO_EXT else "image/png"
        with open(path, "rb") as f:
            r = self._http.post(
                "/upload/image",  # ComfyUI uses this endpoint for every input upload, audio included
                files={"image": (name, f, mime)},
                data={"overwrite": "true", "type": "input"},
            )
        r.raise_for_status()
        d = r.json()
        return f"{d['subfolder']}/{d['name']}" if d.get("subfolder") else d["name"]

    def free(self, unload_models: bool = True, free_memory: bool = True) -> None:
        try:
            self._http.post("/free", json={"unload_models": unload_models, "free_memory": free_memory})
        except httpx.HTTPError as e:
            log.debug("comfy /free failed: %s", e)

    # ---------------------------------------------------------------- jobs
    def queue(self, workflow: dict[str, Any]) -> str:
        r = self._http.post("/prompt", json={"prompt": workflow, "client_id": self.client_id})
        if r.status_code >= 400:
            raise ComfyError(f"ComfyUI rejected the workflow ({r.status_code}): {_error_text(r)}")
        return r.json()["prompt_id"]

    def wait(self, prompt_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            r = self._http.get(f"/history/{prompt_id}")
            r.raise_for_status()
            entry = r.json().get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyError(f"ComfyUI job failed: {_exec_error(status)}")
                if status.get("completed", True) and entry.get("outputs") is not None:
                    return entry
            time.sleep(self.poll_interval)
        raise ComfyError(f"ComfyUI job {prompt_id} timed out after {self.timeout}s")

    def output_files(self, entry: dict[str, Any]) -> list[dict[str, Any]]:
        files = []
        for node_out in entry.get("outputs", {}).values():
            for value in node_out.values():
                if isinstance(value, list):
                    files += [v for v in value if isinstance(v, dict) and "filename" in v]
        return [f for f in files if f.get("type", "output") == "output"] or files

    def download(self, file_info: dict[str, Any], dest: Path) -> Path:
        params = {"filename": file_info["filename"], "subfolder": file_info.get("subfolder", ""),
                  "type": file_info.get("type", "output")}
        with self._http.stream("GET", "/view", params=params, timeout=300) as r:
            r.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        return dest

    def run(self, workflow: dict[str, Any], dest: Path, kind: str = "image") -> Path:
        """Queue, wait, and download the (last) output of the requested kind to `dest`."""
        prompt_id = self.queue(workflow)
        log.debug("queued %s", prompt_id)
        entry = self.wait(prompt_id)
        exts = _EXTS[kind]
        files = [f for f in self.output_files(entry) if Path(f["filename"]).suffix.lower() in exts]
        if not files:
            raise ComfyError(f"workflow produced no {kind} output. Does it end with a Save{kind.title()} node?")
        chosen = files[-1]
        target = dest.with_suffix(Path(chosen["filename"]).suffix.lower())
        return self.download(chosen, target)


def _error_text(r: httpx.Response) -> str:
    try:
        d = r.json()
    except ValueError:
        return r.text[:1000]
    err = d.get("error", {})
    msg = err.get("message", "") if isinstance(err, dict) else str(err)
    details = []
    for node_id, ne in (d.get("node_errors") or {}).items():
        for e in ne.get("errors", []):
            details.append(f"node {node_id} ({ne.get('class_type')}): {e.get('message')} {e.get('details', '')}")
    return msg + ("\n  " + "\n  ".join(details) if details else "")


def _exec_error(status: dict[str, Any]) -> str:
    for kind, data in status.get("messages", []):
        if kind == "execution_error":
            return f"{data.get('node_type')}: {data.get('exception_message', '').strip()}"
    return "unknown error"
