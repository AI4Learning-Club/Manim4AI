from __future__ import annotations

import json
import mimetypes
import os
import threading
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from agent_pipeline.main import RUNS_DIR, run_pipeline


HOST = os.environ.get("APP_HOST", "0.0.0.0")
PORT = int(os.environ.get("APP_PORT", "8000"))


@dataclass
class Job:
    job_id: str
    request: str
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    run_dir: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


_jobs: Dict[str, Job] = {}
_jobs_lock = threading.Lock()


def _job_to_response(job: Job, base_url: str) -> Dict[str, Any]:
    payload = asdict(job)
    summary = job.summary or {}
    video_path = summary.get("final_video_with_audio") or summary.get("final_video")
    payload["video_url"] = f"{base_url}/videos/{job.job_id}" if video_path else None
    payload["status_url"] = f"{base_url}/jobs/{job.job_id}"
    return payload


def _run_job(job_id: str) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        job.status = "running"
        job.started_at = datetime.now().isoformat(timespec="seconds")

    run_dir = RUNS_DIR / f"api_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{job_id[:8]}"
    try:
        summary = run_pipeline(job.request, run_dir=run_dir)
        final_video = summary.get("final_video_with_audio") or summary.get("final_video")
        with _jobs_lock:
            job = _jobs[job_id]
            job.run_dir = str(run_dir)
            job.summary = summary
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            if final_video:
                job.status = "completed"
            else:
                job.status = "failed"
                job.error = "Pipeline finished without producing a final video."
    except Exception:
        with _jobs_lock:
            job = _jobs[job_id]
            job.run_dir = str(run_dir)
            job.status = "failed"
            job.finished_at = datetime.now().isoformat(timespec="seconds")
            job.error = traceback.format_exc()


class AppHandler(BaseHTTPRequestHandler):
    server_version = "ManimEduAgentAPI/1.0"

    def _base_url(self) -> str:
        host = self.headers.get("Host") or f"{HOST}:{PORT}"
        return f"http://{host}"

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _send_json(self, payload: Dict[str, Any], status: int = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json({"error": "video not found"}, status=HTTPStatus.NOT_FOUND)
            return
        mime, _ = mimetypes.guess_type(str(path))
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]

        if parsed.path in {"/", ""}:
            self._send_json(
                {
                    "service": "manim-edu-agent",
                    "endpoints": {
                        "submit_job": {"method": "POST", "path": "/jobs", "body": {"request": "讲解需求"}},
                        "job_status": {"method": "GET", "path": "/jobs/{job_id}"},
                        "video_download": {"method": "GET", "path": "/videos/{job_id}"},
                    },
                }
            )
            return

        if len(parts) == 2 and parts[0] == "jobs":
            job_id = parts[1]
            with _jobs_lock:
                job = _jobs.get(job_id)
            if not job:
                self._send_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json(_job_to_response(job, self._base_url()))
            return

        if len(parts) == 2 and parts[0] == "videos":
            job_id = parts[1]
            with _jobs_lock:
                job = _jobs.get(job_id)
            if not job:
                self._send_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
                return
            summary = job.summary or {}
            video_path = summary.get("final_video_with_audio") or summary.get("final_video")
            if not video_path:
                self._send_json(
                    {"error": "video not ready", "status": job.status},
                    status=HTTPStatus.CONFLICT,
                )
                return
            self._send_file(Path(video_path))
            return

        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/jobs":
            self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return

        try:
            payload = self._read_json()
        except json.JSONDecodeError:
            self._send_json({"error": "invalid json body"}, status=HTTPStatus.BAD_REQUEST)
            return

        request_text = str(payload.get("request", "")).strip()
        if not request_text:
            self._send_json({"error": "request is required"}, status=HTTPStatus.BAD_REQUEST)
            return

        job_id = uuid.uuid4().hex
        job = Job(job_id=job_id, request=request_text)
        with _jobs_lock:
            _jobs[job_id] = job

        worker = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
        worker.start()

        self._send_json(_job_to_response(job, self._base_url()), status=HTTPStatus.ACCEPTED)


def main() -> int:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"API server listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
