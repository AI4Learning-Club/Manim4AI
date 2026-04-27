from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

import uvicorn
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send


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
    get_job_status: Callable[[str], dict[str, Any]] | None = None,
    get_job_events: Callable[[str, int], dict[str, Any]] | None = None,
    path: str = "/mcp",
    json_response: bool = False,
    stateless: bool = False,
    debug: bool = False,
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

    async def serve_job_status(request: Request):
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

    async def stream_job_events(request: Request):
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
            Route("/jobs/{job_id:str}", endpoint=serve_job_status, methods=["GET"], name="manim_job_status"),
            Route("/jobs/{job_id:str}/stream", endpoint=stream_job_events, methods=["GET"], name="manim_job_stream"),
        ],
        lifespan=lifespan,
    )


def run_streamable_http_server(
    server: Server,
    *,
    managed_video_dir: Path,
    get_job_status: Callable[[str], dict[str, Any]] | None = None,
    get_job_events: Callable[[str, int], dict[str, Any]] | None = None,
    host: str = "127.0.0.1",
    port: int = 8010,
    path: str = "/mcp",
    json_response: bool = False,
    stateless: bool = False,
    debug: bool = False,
    log_level: str = "info",
) -> None:
    if not _is_loopback_host(host):
        raise RuntimeError(
            "Manim HTTP service must bind to a loopback host because auxiliary "
            "/videos and /jobs routes are authenticated by the main backend proxy."
        )
    app = create_streamable_http_app(
        server,
        managed_video_dir=managed_video_dir,
        get_job_status=get_job_status,
        get_job_events=get_job_events,
        path=path,
        json_response=json_response,
        stateless=stateless,
        debug=debug,
    )
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level,
    )
