from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Callable

from mcp.server import Server
from mcp.types import TextContent, Tool

from .tools import build_tool_definitions, handle_tool_call

RenderTeachingVideo = Callable[..., dict[str, Any]]
SubmitTeachingVideoJob = Callable[..., dict[str, Any]]
GetTeachingVideoJobStatus = Callable[..., dict[str, Any]]


def create_server(
    *,
    server_name: str,
    render_teaching_video: RenderTeachingVideo,
    submit_teaching_video_job: SubmitTeachingVideoJob,
    get_teaching_video_job_status: GetTeachingVideoJobStatus,
) -> Server:
    server = Server(server_name)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return build_tool_definitions()

    @server.call_tool()
    async def call_tool(
        name: str,
        arguments: dict[str, object],
    ) -> Sequence[TextContent]:
        return await handle_tool_call(
            name,
            arguments,
            render_teaching_video=render_teaching_video,
            submit_teaching_video_job=submit_teaching_video_job,
            get_teaching_video_job_status=get_teaching_video_job_status,
        )

    return server
