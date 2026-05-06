#!/usr/bin/env python3
from __future__ import annotations

import argparse

from config.logger_config import get_mcp_logger
from plugins.manim.manim_mcp_runtime import create_manim_runtime, get_managed_videos_dir
from plugins.manim.mcp_server import run_streamable_http_server
from plugins.manim.runtime_config import get_manim_service_bind


def _build_arg_parser() -> argparse.ArgumentParser:
    host, port = get_manim_service_bind()
    parser = argparse.ArgumentParser(description="Run Manim MCP server over Streamable HTTP.")
    parser.add_argument("--host", default=host, help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=port, help="HTTP bind port.")
    parser.add_argument("--path", default="/mcp", help="Streamable HTTP endpoint path.")
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="uvicorn log level.",
    )
    parser.add_argument(
        "--json-response",
        action="store_true",
        help="Prefer JSON HTTP responses instead of SSE streams when possible.",
    )
    parser.add_argument(
        "--stateless",
        action="store_true",
        help="Create a fresh Streamable HTTP transport for each request.",
    )
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="Allow binding auxiliary HTTP routes on a non-loopback host for trusted internal deployments.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable Starlette debug mode.")
    return parser


def main() -> None:
    args = _build_arg_parser().parse_args()
    log = get_mcp_logger("manim_http")
    log.info(
        "[Manim][HTTP] 服务进程已启动: http://%s:%s%s （等待 MCP 请求）",
        args.host,
        args.port,
        args.path,
    )
    runtime = create_manim_runtime()
    run_streamable_http_server(
        runtime.server,
        managed_video_dir=get_managed_videos_dir(),
        submit_job=lambda payload: runtime.renderer.submit_render_video(
            request=str(payload.get("request") or ""),
            language=str(payload.get("language") or ""),
            render_backend=str(payload.get("render_backend") or "manim"),
            quality=str(payload.get("quality") or "default"),
            conversation_id=str(payload.get("conversation_id") or ""),
            idempotency_key=str(payload.get("idempotency_key") or ""),
        ),
        get_job_status=lambda job_id: runtime.renderer.get_job_status(job_id=job_id),
        get_job_events=lambda job_id, after_index: runtime.renderer.get_job_events(job_id=job_id, after_index=after_index),
        host=args.host,
        port=args.port,
        path=args.path,
        json_response=args.json_response,
        stateless=args.stateless,
        debug=args.debug,
        log_level=args.log_level,
        allow_non_loopback_host=args.allow_non_loopback,
    )


if __name__ == "__main__":
    main()
