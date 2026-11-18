"""`shorts storyboard`: a local web page for approving runs before the expensive motion stage.

Standard library only (http.server + one HTML page). GPU work (image re-rolls, music) and previews run in a
background worker, one job at a time; "Approve & render" starts `shorts resume <run>` as a separate process
(queued: one render at a time) and logs it to runs/<run>/render.log.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import queue
import subprocess
import sys
import threading
import time
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from shorts import storyboard
from shorts.config import Env
from shorts.context import Context
from shorts.run import Run

log = logging.getLogger("shorts.storyboard.server")


class Jobs:
    """Two FIFO workers: 'gpu' for quick edits (re-roll, music, preview), 'render' for approved runs."""

    def __init__(self):
        self.jobs: dict[str, dict[str, Any]] = {}
        self._queues = {"gpu": queue.Queue(), "render": queue.Queue()}
        self._lock = threading.Lock()
        for name, q in self._queues.items():
            threading.Thread(target=self._work, args=(q,), daemon=True, name=f"storyboard-{name}").start()

    def submit(self, lane: str, run_id: str, kind: str, fn: Callable[[], Any], scene: int | None = None) -> dict:
        job = {"id": uuid.uuid4().hex[:10], "lane": lane, "run": run_id, "kind": kind, "scene": scene,
               "status": "queued", "error": None, "created": time.time(), "started": None, "finished": None}
        with self._lock:
            self.jobs[job["id"]] = job
        self._queues[lane].put((job, fn))
        return job

    def _work(self, q: queue.Queue) -> None:
        while True:
            job, fn = q.get()
            job["status"], job["started"] = "running", time.time()
            try:
                fn()
                job["status"] = "done"
            except Exception as e:  # noqa: BLE001 - report to the page
                log.error("job %s %s failed: %s", job["kind"], job["run"], e)
                log.debug(traceback.format_exc())
                job["status"], job["error"] = "failed", f"{type(e).__name__}: {e}"
            job["finished"] = time.time()

    def active(self) -> list[dict]:
        cutoff = time.time() - 600
        with self._lock:
            return [j for j in self.jobs.values() if j["status"] in ("queued", "running") or
                    (j["finished"] or 0) > cutoff]


class Storyboard:
    def __init__(self, env_file: Path | None = None, project_dir: Path | None = None):
        self.env_file = env_file
        self.project_dir = Path(project_dir or Path.cwd())
        self.base = Context(Env.load(env_file))
        self.runs_dir = Path(self.base.app.runs_dir)
        self._contexts: dict[str | None, Context] = {self.base.app.style_profile: self.base}
        self._ctx_lock = threading.Lock()
        self.jobs = Jobs()

    # ------------------------------------------------------------ helpers
    def ctx_for(self, run: Run) -> Context:
        """A context with the run's own style profile (image re-rolls must keep the run's look)."""
        style = run.manifest.style_profile
        with self._ctx_lock:
            if style not in self._contexts:
                self._contexts[style] = Context(Env.load(self.env_file, style))
            return self._contexts[style]

    def run(self, run_id: str) -> Run:
        p = (self.runs_dir / run_id).resolve()
        if p.parent != self.runs_dir.resolve() or not (p / "manifest.json").exists():
            raise FileNotFoundError(f"no run {run_id}")
        return Run.open(p)

    def list_runs(self) -> list[dict]:
        out = []
        for p in sorted(self.runs_dir.glob("*/manifest.json"), reverse=True):
            try:
                r = Run.open(p.parent)
                title = r.script().title if (r.path / "script.json").exists() else r.idea().title
                thumb = r.scene_file("images", 0, "png")
                out.append({"id": r.id, "title": title, "topic": r.manifest.topic, "style": r.manifest.style_profile,
                            "state": storyboard.state(r), "created_at": r.manifest.created_at,
                            "thumb": "images/scene_01.png" if thumb.exists() else None,
                            "rendering": any(j["run"] == r.id and j["kind"] == "render" and
                                             j["status"] in ("queued", "running") for j in self.jobs.active())})
            except Exception as e:  # noqa: BLE001 - a broken run folder must not break the list
                log.warning("skipping %s: %s", p.parent.name, e)
        order = {"waiting": 0, "approved": 1, "draft": 2, "failed": 3, "done": 4, "rejected": 5}
        return sorted(out, key=lambda r: (order.get(r["state"], 9), -r["created_at"]))

    def render(self, run: Run) -> None:
        log_file = run.path / "render.log"
        cmd = [sys.executable, "-m", "shorts.cli"]
        if self.env_file:
            cmd += ["--env-file", str(self.env_file)]
        cmd += ["resume", run.id]
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)}\n")
            f.flush()
            rc = subprocess.run(cmd, cwd=self.project_dir, stdout=f, stderr=subprocess.STDOUT).returncode
        if rc != 0:
            raise RuntimeError(f"render failed (exit {rc}), see {log_file}")

    # ------------------------------------------------------------ API
    def handle(self, method: str, parts: list[str], body: dict) -> tuple[int, Any]:
        if method == "GET" and parts == ["runs"]:
            return 200, self.list_runs()
        if method == "GET" and parts == ["jobs"]:
            return 200, self.jobs.active()
        if len(parts) < 2 or parts[0] != "run":
            return 404, {"error": "unknown endpoint"}
        run = self.run(parts[1])
        rest = parts[2:]
        if method == "GET" and not rest:
            return 200, storyboard.summary(run)
        if method != "POST":
            return 405, {"error": "use POST"}
        ctx = self.ctx_for(run)
        submit = self.jobs.submit
        if rest[:1] == ["scene"] and len(rest) == 3:
            i = int(rest[1]) - 1
            if not 0 <= i < len(run.script().scenes):
                return 400, {"error": "bad scene number"}
            action = rest[2]
            if action == "reroll":
                prompt = body.get("prompt")
                return 202, submit("gpu", run.id, "reroll", lambda: storyboard.reroll(ctx, self.run(run.id), i, prompt),
                                   scene=i + 1)
            if action == "narration":
                text = body["text"]
                return 202, submit("gpu", run.id, "narration",
                                   lambda: storyboard.edit_narration(ctx, self.run(run.id), i, text), scene=i + 1)
            if action == "pick":
                storyboard.pick(run, i, body["candidate"])
                return 200, {"ok": True}
            if action == "motion":
                storyboard.set_motion(run, i, body["mode"])
                return 200, {"ok": True}
            return 404, {"error": f"unknown scene action {action}"}
        action = rest[0] if rest else ""
        if action == "music":
            tags, bpm, key = body.get("tags"), body.get("bpm"), body.get("key")
            return 202, submit("gpu", run.id, "music",
                               lambda: storyboard.regenerate_music(ctx, self.run(run.id), tags, bpm, key))
        if action == "recheck":
            return 202, submit("gpu", run.id, "recheck", lambda: storyboard.recheck_facts(ctx, self.run(run.id)))
        if action == "preview":
            return 202, submit("gpu", run.id, "preview", lambda: storyboard.build_preview(ctx, self.run(run.id)))
        if action == "approve":
            storyboard.approve(run, note=body.get("note", ""))
            if body.get("render", True):
                return 202, submit("render", run.id, "render", lambda: self.render(self.run(run.id)))
            return 200, {"ok": True}
        if action == "reject":
            storyboard.reject(ctx, run, body.get("note", ""))
            return 200, {"ok": True}
        return 404, {"error": f"unknown action {action}"}

    def file(self, run_id: str, rel: str) -> Path:
        run = self.run(run_id)
        p = (run.path / rel).resolve()
        if run.path.resolve() not in p.parents or not p.is_file():
            raise FileNotFoundError(rel)
        return p


def make_handler(sb: Storyboard) -> type[BaseHTTPRequestHandler]:
    page = resources.files("shorts").joinpath("storyboard.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = "shorts-storyboard"

        def log_message(self, fmt, *args):  # quiet: only errors go to the log
            pass

        def _send(self, code: int, data: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _route(self, method: str) -> None:
            path = unquote(urlparse(self.path).path)
            try:
                if method == "GET" and path in ("/", "/index.html"):
                    return self._send(200, page, "text/html; charset=utf-8")
                if path.startswith("/api/"):
                    body = {}
                    if method == "POST":
                        n = int(self.headers.get("Content-Length") or 0)
                        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
                    code, obj = sb.handle(method, [p for p in path[5:].split("/") if p], body)
                    return self._json(code, obj)
                if method == "GET" and path.startswith("/files/"):
                    run_id, _, rel = path[7:].partition("/")
                    return self._file(sb.file(run_id, rel))
                self._json(404, {"error": "not found"})
            except ConnectionError:  # the browser dropped the connection (seeking, switching scenes): normal
                self.close_connection = True
                return
            except FileNotFoundError as e:
                self._json(404, {"error": str(e)})
            except (ValueError, KeyError) as e:
                self._json(400, {"error": f"{type(e).__name__}: {e}"})
            except Exception as e:  # noqa: BLE001
                log.error("%s %s: %s", method, path, traceback.format_exc())
                try:
                    self._json(500, {"error": f"{type(e).__name__}: {e}"})
                except ConnectionError:
                    self.close_connection = True

        def _file(self, p: Path) -> None:
            """Serve a file in chunks, with byte ranges (browsers seek audio/video with range requests)."""
            size = p.stat().st_size
            ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            start, end, code = 0, size - 1, 200
            rng = (self.headers.get("Range") or "").strip()
            if rng.startswith("bytes=") and "," not in rng:
                a, _, b = rng[6:].partition("-")
                try:
                    if a:
                        start, end = int(a), min(int(b), size - 1) if b else size - 1
                    else:
                        start = max(0, size - int(b))
                except ValueError:
                    start, end = 0, size - 1
                if start >= size or start > end:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                code = HTTPStatus.PARTIAL_CONTENT
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            if code == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(p, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)

        def do_GET(self):  # noqa: N802
            self._route("GET")

        def do_HEAD(self):  # noqa: N802
            self._route("GET")

        def do_POST(self):  # noqa: N802
            self._route("POST")

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError):  # client went away: nothing to report
            return
        super().handle_error(request, client_address)


def serve(host: str = "127.0.0.1", port: int = 8765, env_file: Path | None = None) -> ThreadingHTTPServer:
    sb = Storyboard(env_file)
    httpd = _Server((host, port), make_handler(sb))
    httpd.storyboard = sb  # type: ignore[attr-defined]
    return httpd
