from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import uvicorn
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

_JOB_STREAM_SUPPRESSED_EVENT_TYPES = frozenset({"analysis_delta", "code_delta"})
_MANIM_JOB_KEY_HEADER = "X-Manim-Job-Key"


class StreamableHTTPASGIApp:
    def __init__(self, session_manager: StreamableHTTPSessionManager):
        self.session_manager = session_manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.session_manager.handle_request(scope, receive, send)


def _normalize_http_path(path: str) -> str:
    normalized = (path or "/mcp").strip()
    if not normalized:
        return "/mcp"
    if not normalized.startswith("/"):
        return f"/{normalized}"
    return normalized


def _resolve_managed_video_path(managed_video_dir: Path, file_name: str) -> Path | None:
    if not file_name or "/" in file_name or "\\" in file_name:
        return None
    candidate = (managed_video_dir / file_name).resolve()
    if candidate.parent != managed_video_dir.resolve():
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate


def _coerce_resume_index(raw_value: str | None) -> int:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return 0
    try:
        return max(0, int(normalized))
    except (TypeError, ValueError):
        return 0


def _resolve_initial_after_index(request: Request) -> int:
    query_value = request.query_params.get("after_index")
    if query_value is not None:
        return _coerce_resume_index(query_value)
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id is None:
        return 0
    return _coerce_resume_index(last_event_id) + 1


def _should_emit_job_stream_event(event: Any) -> bool:
    if not isinstance(event, dict):
        return True
    event_type = str(event.get("type") or "").strip().lower()
    return event_type not in _JOB_STREAM_SUPPRESSED_EVENT_TYPES


def _is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def create_streamable_http_app(
    server: Server,
    *,
    managed_video_dir: Path,
    submit_job: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    get_job_status: Callable[[str], dict[str, Any]] | None = None,
    get_job_events: Callable[[str, int], dict[str, Any]] | None = None,
    path: str = "/mcp",
    json_response: bool = False,
    stateless: bool = False,
    debug: bool = False,
    job_api_key: str = "",
) -> Starlette:
    http_session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=json_response,
        stateless=stateless,
    )
    mcp_path = _normalize_http_path(path)
    streamable_http_app = StreamableHTTPASGIApp(http_session_manager)

    async def serve_video(request: Request):
        file_name = str(request.path_params.get("file_name") or "").strip()
        target = _resolve_managed_video_path(managed_video_dir, file_name)
        if target is None:
            return JSONResponse({"error": "video not found"}, status_code=404)
        return FileResponse(target, media_type="video/mp4", filename=target.name)

    def _job_auth_error(request: Request) -> JSONResponse | None:
        expected = str(job_api_key or "").strip()
        supplied = str(request.headers.get(_MANIM_JOB_KEY_HEADER) or "").strip()
        if not expected or supplied != expected:
            return JSONResponse({"error": "job endpoint unauthorized"}, status_code=403)
        return None

    async def serve_job_status(request: Request):
        if auth_error := _job_auth_error(request):
            return auth_error
        if get_job_status is None:
            return JSONResponse({"error": "job endpoint not configured"}, status_code=404)
        job_id = str(request.path_params.get("job_id") or "").strip()
        try:
            payload = get_job_status(job_id)
        except KeyError:
            return JSONResponse({"error": "job not found"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(payload, status_code=200)

    async def submit_job_request(request: Request):
        if auth_error := _job_auth_error(request):
            return auth_error
        if submit_job is None:
            return JSONResponse({"error": "job submission endpoint not configured"}, status_code=404)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json body"}, status_code=400)
        if not isinstance(payload, dict):
            return JSONResponse({"error": "invalid json body"}, status_code=400)
        try:
            result = submit_job(payload)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result, status_code=202)

    async def stream_job_events(request: Request):
        if auth_error := _job_auth_error(request):
            return auth_error
        if get_job_events is None:
            return JSONResponse({"error": "job stream endpoint not configured"}, status_code=404)
        job_id = str(request.path_params.get("job_id") or "").strip()
        initial_after_index = _resolve_initial_after_index(request)

        async def _generator() -> AsyncIterator[str]:
            next_index = initial_after_index
            while True:
                try:
                    batch = get_job_events(job_id, next_index)
                except KeyError:
                    yield 'data: {"type":"error","message":"Job not found"}\n\n'
                    return
                except ValueError as exc:
                    yield f"data: {json.dumps({'type': 'error', 'message': str(exc)}, ensure_ascii=False)}\n\n"
                    return

                for index, event in enumerate(batch["events"], start=next_index):
                    if not _should_emit_job_stream_event(event):
                        continue
                    payload = {
                        "type": "manim_job_event",
                        "job_id": job_id,
                        "event": event,
                        "status": batch["status"],
                    }
                    yield f"id: {index}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

                next_index = int(batch["next_index"])
                if batch["terminal"]:
                    yield f'id: {next_index}\ndata: {{"type":"end","job_id":"{job_id}"}}\n\n'
                    return

                await asyncio.sleep(0.5)

        return StreamingResponse(
            _generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with http_session_manager.run():
            yield

    return Starlette(
        debug=debug,
        routes=[
            Mount(mcp_path, app=streamable_http_app, name="mcp_streamable_http"),
            Route("/videos/{file_name:str}", endpoint=serve_video, methods=["GET"], name="manim_video"),
            Route("/jobs", endpoint=submit_job_request, methods=["POST"], name="manim_job_submit"),
            Route("/jobs/{job_id:str}", endpoint=serve_job_status, methods=["GET"], name="manim_job_status"),
            Route("/jobs/{job_id:str}/stream", endpoint=stream_job_events, methods=["GET"], name="manim_job_stream"),
        ],
        lifespan=lifespan,
    )


def run_streamable_http_server(
    server: Server,
    *,
    managed_video_dir: Path,
    submit_job: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    get_job_status: Callable[[str], dict[str, Any]] | None = None,
    get_job_events: Callable[[str, int], dict[str, Any]] | None = None,
    host: str = "127.0.0.1",
    port: int = 8010,
    path: str = "/mcp",
    json_response: bool = False,
    stateless: bool = False,
    debug: bool = False,
    log_level: str = "info",
    allow_non_loopback_host: bool = False,
    job_api_key: str = "",
) -> None:
    if not allow_non_loopback_host and not _is_loopback_host(host):
        raise RuntimeError(
            "Manim HTTP service must bind to a loopback host because auxiliary "
            "/videos and /jobs routes are authenticated by the main backend proxy."
        )
    app = create_streamable_http_app(
        server,
        managed_video_dir=managed_video_dir,
        submit_job=submit_job,
        get_job_status=get_job_status,
        get_job_events=get_job_events,
        path=path,
        json_response=json_response,
        stateless=stateless,
        debug=debug,
        job_api_key=job_api_key,
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
    )
