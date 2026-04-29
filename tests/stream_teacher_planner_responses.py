#!/usr/bin/env python3
"""Stream raw Responses SSE events for the production Manim teacher planner.

This script intentionally reuses:
1. The exact planner system prompt from
   ``plugins.manim.agent_pipeline.teaching_planner._SYSTEM_PLAN``.
2. The live analysis-stage LLM config loaded from
   ``config/app_settings/settings.toml`` via
   ``plugins.manim.agent_pipeline.llm.resolve_pipeline_llm_configs``.

It sends a raw ``/responses`` streaming request so we can inspect the full SSE
event flow in real time, including reasoning, tool-call deltas, output text,
status transitions, and any less common event families exposed by the current
Responses API.

By default the JSON body includes ``"service_tier": "priority"`` (same idea as
``plugins.manim.agent_pipeline.llm.LLMClient``). Upstream SSE snapshots may
still show ``response.service_tier == "auto"``: that value is commonly a
**server-side routing label**, not the same thing as replaying your JSON field.
Use the banner ``service_tier_in_request_json`` and ``effective_payload.service_tier``
lines to verify what left the client.

Do not combine ``--omit-service-tier`` with ``--service-tier``: omit wins, the
JSON body will not contain ``service_tier``, and a warning is printed.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
import textwrap
import time
import typing
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from plugins.manim.agent_pipeline.llm import (  # noqa: E402
    LLMConfig,
    _apply_responses_fallbacks,
    _normalize_reasoning_effort,
    resolve_pipeline_llm_configs,
)
from plugins.manim.agent_pipeline.teaching_planner import (  # noqa: E402
    _SYSTEM_PLAN,
    _extract_json_object,
    _image_to_data_url,
)

OPENAI_RESPONSES_STREAM_GUIDE_URL = (
    "https://platform.openai.com/docs/guides/streaming-response-events"
)
OPENAI_RESPONSES_STREAM_REFERENCE_URL = (
    "https://platform.openai.com/docs/api-reference/responses-streaming"
)
OPENAI_RESPONSES_CREATE_REFERENCE_URL = (
    "https://platform.openai.com/docs/api-reference/responses/create"
)

# Match production planner requests (see ``LLMClient._call_responses_api``).
DEFAULT_RESPONSES_SERVICE_TIER = "priority"

# Seeded from the official Responses streaming docs current at implementation
# time and cross-checked against the installed OpenAI Python SDK event union.
DOCS_RESPONSE_STREAM_EVENT_TYPES: tuple[str, ...] = (
    "error",
    "response.audio.delta",
    "response.audio.done",
    "response.audio.transcript.delta",
    "response.audio.transcript.done",
    "response.code_interpreter_call.completed",
    "response.code_interpreter_call.in_progress",
    "response.code_interpreter_call.interpreting",
    "response.code_interpreter_call_code.delta",
    "response.code_interpreter_call_code.done",
    "response.completed",
    "response.content_part.added",
    "response.content_part.done",
    "response.created",
    "response.custom_tool_call_input.delta",
    "response.custom_tool_call_input.done",
    "response.failed",
    "response.file_search_call.completed",
    "response.file_search_call.in_progress",
    "response.file_search_call.searching",
    "response.function_call_arguments.delta",
    "response.function_call_arguments.done",
    "response.image_generation_call.completed",
    "response.image_generation_call.generating",
    "response.image_generation_call.in_progress",
    "response.image_generation_call.partial_image",
    "response.in_progress",
    "response.incomplete",
    "response.mcp_call.completed",
    "response.mcp_call.failed",
    "response.mcp_call.in_progress",
    "response.mcp_call_arguments.delta",
    "response.mcp_call_arguments.done",
    "response.mcp_list_tools.completed",
    "response.mcp_list_tools.failed",
    "response.mcp_list_tools.in_progress",
    "response.output_item.added",
    "response.output_item.done",
    "response.output_text.annotation.added",
    "response.output_text.delta",
    "response.output_text.done",
    "response.queued",
    "response.reasoning_summary_part.added",
    "response.reasoning_summary_part.done",
    "response.reasoning_summary_text.delta",
    "response.reasoning_summary_text.done",
    "response.reasoning_text.delta",
    "response.reasoning_text.done",
    "response.refusal.delta",
    "response.refusal.done",
    "response.web_search_call.completed",
    "response.web_search_call.in_progress",
    "response.web_search_call.searching",
)

TEXT_DELTA_EVENT_CHANNELS = {
    "response.reasoning_text.delta": "reasoning_text",
    "response.reasoning_summary_text.delta": "reasoning_summary_text",
    "response.output_text.delta": "output_text",
    "response.refusal.delta": "refusal_text",
    "response.function_call_arguments.delta": "function_call_arguments",
    "response.custom_tool_call_input.delta": "custom_tool_call_input",
    "response.mcp_call_arguments.delta": "mcp_call_arguments",
    "response.code_interpreter_call_code.delta": "code_interpreter_code",
    "response.audio.transcript.delta": "audio_transcript",
}

TEXT_DONE_EVENTS = {
    "response.reasoning_text.done",
    "response.reasoning_summary_text.done",
    "response.output_text.done",
    "response.refusal.done",
    "response.function_call_arguments.done",
    "response.custom_tool_call_input.done",
    "response.mcp_call_arguments.done",
    "response.code_interpreter_call_code.done",
    "response.audio.transcript.done",
}

LIFECYCLE_EVENTS = {
    "response.created",
    "response.queued",
    "response.in_progress",
    "response.completed",
    "response.incomplete",
    "response.failed",
    "error",
}


@dataclass
class SSEMessage:
    event: str | None
    data: str
    event_id: str | None = None
    retry: str | None = None


@dataclass
class StreamState:
    event_counts: Counter[str] = field(default_factory=Counter)
    active_channel: str | None = None
    output_text: list[str] = field(default_factory=list)
    reasoning_text: list[str] = field(default_factory=list)
    reasoning_summary_text: list[str] = field(default_factory=list)
    refusal_text: list[str] = field(default_factory=list)
    audio_transcript: list[str] = field(default_factory=list)
    function_call_arguments: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    custom_tool_call_input: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    mcp_call_arguments: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    code_interpreter_code: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    audio_delta_chunks: int = 0
    final_response: dict[str, Any] | None = None
    response_id: str | None = None
    response_status: str | None = None
    unknown_events: set[str] = field(default_factory=set)


def discover_sdk_response_stream_event_types() -> set[str]:
    """Discover event names from the installed OpenAI SDK if available."""
    try:
        from openai.types.responses import response_stream_event  # type: ignore
    except Exception:
        return set()

    discovered: set[str] = set()
    for _, cls in inspect.getmembers(response_stream_event, inspect.isclass):
        annotations = getattr(cls, "__annotations__", {})
        event_type = annotations.get("type")
        if typing.get_origin(event_type) is typing.Literal:
            for literal in typing.get_args(event_type):
                if isinstance(literal, str):
                    discovered.add(literal)
    return discovered


def resolved_known_event_types() -> list[str]:
    discovered = discover_sdk_response_stream_event_types()
    return sorted(set(DOCS_RESPONSE_STREAM_EVENT_TYPES) | discovered)


RESOLVED_RESPONSE_STREAM_EVENT_TYPES = tuple(resolved_known_event_types())
RESOLVED_RESPONSE_STREAM_EVENT_TYPE_SET = set(RESOLVED_RESPONSE_STREAM_EVENT_TYPES)


def warn_about_event_drift() -> None:
    docs_types = set(DOCS_RESPONSE_STREAM_EVENT_TYPES)
    sdk_types = discover_sdk_response_stream_event_types()
    missing_from_docs_seed = sorted(sdk_types - docs_types)
    stale_in_docs_seed = sorted(docs_types - sdk_types)
    if not missing_from_docs_seed and not stale_in_docs_seed:
        return

    print("\n[warning] Responses stream event drift detected against installed SDK.", file=sys.stderr)
    if missing_from_docs_seed:
        print(
            "[warning] Missing from docs-seeded list: " + ", ".join(missing_from_docs_seed),
            file=sys.stderr,
        )
    if stale_in_docs_seed:
        print(
            "[warning] Present in docs-seeded list but not current SDK: " + ", ".join(stale_in_docs_seed),
            file=sys.stderr,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send a raw streaming Responses request with the production Manim "
            "teacher planner prompt and print detailed SSE activity."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Examples:
              .venv/bin/python plugins/manim/tests/stream_teacher_planner_responses.py \\
                --request "讲解勾股定理为什么成立"

              .venv/bin/python plugins/manim/tests/stream_teacher_planner_responses.py \\
                --request-file /tmp/request.txt --image /tmp/problem.png --show-json

              .venv/bin/python plugins/manim/tests/stream_teacher_planner_responses.py \\
                --list-event-types
            """
        ),
    )
    parser.add_argument("--request", help="Request text passed to the planner.")
    parser.add_argument("--request-file", type=Path, help="Read request text from a file.")
    parser.add_argument("--image", type=Path, help="Optional image passed as input_image.")
    parser.add_argument(
        "--tools-file",
        type=Path,
        help="Optional JSON file containing a Responses tools array.",
    )
    parser.add_argument(
        "--show-json",
        action="store_true",
        help="Pretty print each non-text event payload after the compact summary line.",
    )
    parser.add_argument(
        "--print-system-prompt",
        action="store_true",
        help="Print the exact planner system prompt before sending the request.",
    )
    parser.add_argument(
        "--list-event-types",
        action="store_true",
        help="List the known Responses SSE event types and exit.",
    )
    parser.add_argument(
        "--no-validate-plan-json",
        action="store_true",
        help="Skip final planner JSON parsing/validation.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        help="Override the analysis-stage timeout from settings.toml.",
    )
    parser.add_argument(
        "--reasoning-effort",
        help="Override the analysis-stage reasoning effort from settings.toml.",
    )
    parser.add_argument("--model", help="Override the analysis-stage model from settings.toml.")
    parser.add_argument("--base-url", help="Override the analysis-stage base_url from settings.toml.")
    parser.add_argument(
        "--service-tier",
        dest="service_tier",
        metavar="TIER",
        default=argparse.SUPPRESS,
        help=(
            f'JSON field service_tier for POST /responses (default when this flag is omitted: '
            f'"{DEFAULT_RESPONSES_SERVICE_TIER}"). '
            "SSE may still echo response.service_tier=auto upstream. "
            "Combining with --omit-service-tier is allowed but omit wins (see warning on stderr)."
        ),
    )
    parser.add_argument(
        "--omit-service-tier",
        action="store_true",
        help=(
            "Omit service_tier from the JSON body entirely. "
            "If you also pass --service-tier, that value is ignored and a warning is printed."
        ),
    )
    parser.add_argument(
        "--print-request-json",
        action="store_true",
        help="Log the POST JSON to stderr before the request (instructions truncated for size).",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Additional request header(s). Repeatable.",
    )
    return parser.parse_args()


def resolve_service_tier_for_request(args: argparse.Namespace) -> tuple[bool, str | None]:
    """Return (omit_service_tier_from_body, tier_value_when_included).

    When omit is True, the JSON body must not contain service_tier (tier_value is None).
    When omit is False, tier_value is the string to send (never None).
    """
    omit = args.omit_service_tier
    explicit = getattr(args, "service_tier", None)
    if omit:
        if explicit is not None:
            print(
                "[stream_teacher_planner_responses] warning: --omit-service-tier is set; "
                f"--service-tier {explicit!r} is ignored. The JSON body will not contain service_tier. "
                "Drop --omit-service-tier if you want to send priority (or another tier).",
                file=sys.stderr,
                flush=True,
            )
        return True, None
    tier = (explicit or DEFAULT_RESPONSES_SERVICE_TIER).strip() or DEFAULT_RESPONSES_SERVICE_TIER
    return False, tier


def read_request_text(args: argparse.Namespace) -> str:
    if args.request:
        return args.request.strip()
    if args.request_file:
        return args.request_file.read_text(encoding="utf-8").strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    raise SystemExit("Provide --request, --request-file, or pipe request text on stdin.")


def build_user_content(request_text: str, image_path: Path | None) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": request_text}]
    if image_path:
        if not image_path.exists():
            raise SystemExit(f"Image path not found: {image_path}")
        content.append({"type": "input_image", "image_url": _image_to_data_url(image_path)})
    return content


def load_tools(tools_file: Path | None) -> list[dict[str, Any]] | None:
    if tools_file is None:
        return None
    data = json.loads(tools_file.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("--tools-file must contain a JSON array.")
    if not all(isinstance(item, dict) for item in data):
        raise SystemExit("--tools-file must contain an array of JSON objects.")
    return typing.cast(list[dict[str, Any]], data)


def build_runtime_config(args: argparse.Namespace) -> LLMConfig:
    analysis = resolve_pipeline_llm_configs()["analysis"]
    reasoning_effort = args.reasoning_effort or analysis.reasoning_effort
    timeout_sec = args.timeout_sec or analysis.timeout_sec
    return LLMConfig(
        stage=analysis.stage,
        provider=analysis.provider,
        model=args.model or analysis.model,
        api_key=analysis.api_key,
        base_url=args.base_url or analysis.base_url,
        timeout_sec=timeout_sec,
        reasoning_effort=reasoning_effort,
    )


def _payload_preview_for_logging(payload: dict[str, Any]) -> dict[str, Any]:
    preview = json.loads(json.dumps(payload))
    instr = preview.get("instructions")
    if isinstance(instr, str) and len(instr) > 200:
        preview["instructions"] = instr[:197] + "..."
    return preview


def build_headers(api_key: str, extras: Iterable[str]) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    for raw in extras:
        if "=" not in raw:
            raise SystemExit(f"Invalid --header value `{raw}`; expected KEY=VALUE.")
        key, value = raw.split("=", 1)
        headers[key.strip()] = value.strip()
    return headers


def build_request_payload(
    config: LLMConfig,
    request_text: str,
    image_path: Path | None,
    *,
    tools: list[dict[str, Any]] | None,
    service_tier: str | None,
    omit_service_tier: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": config.model,
        "instructions": _SYSTEM_PLAN.strip(),
        "input": [{"role": "user", "content": build_user_content(request_text, image_path)}],
        "stream": True,
    }
    if not omit_service_tier:
        tier = (service_tier or "").strip() or DEFAULT_RESPONSES_SERVICE_TIER
        payload["service_tier"] = tier

    reasoning_effort = _normalize_reasoning_effort(config.reasoning_effort)
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    if tools:
        payload["tools"] = tools
    return payload


def parse_error_message(body_text: str) -> str:
    try:
        body = json.loads(body_text)
    except Exception:
        return body_text
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or body_text)
        if isinstance(error, str):
            return error
    return body_text


def iter_sse_messages(response: httpx.Response) -> Iterable[SSEMessage]:
    event_name: str | None = None
    event_id: str | None = None
    retry: str | None = None
    data_lines: list[str] = []

    for raw_line in response.iter_lines():
        line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", errors="replace")
        if line == "":
            if event_name is not None or event_id is not None or retry is not None or data_lines:
                yield SSEMessage(
                    event=event_name,
                    data="\n".join(data_lines),
                    event_id=event_id,
                    retry=retry,
                )
            event_name = None
            event_id = None
            retry = None
            data_lines = []
            continue

        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
        elif field == "id":
            event_id = value
        elif field == "retry":
            retry = value

    if event_name is not None or event_id is not None or retry is not None or data_lines:
        yield SSEMessage(event=event_name, data="\n".join(data_lines), event_id=event_id, retry=retry)


def now_stamp() -> str:
    return time.strftime("%H:%M:%S")


def print_line(text: str = "") -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def ensure_channel(state: StreamState, channel: str, header: str) -> None:
    if state.active_channel == channel:
        return
    if state.active_channel is not None:
        sys.stdout.write("\n")
    print_line(f"\n=== {header} ===")
    state.active_channel = channel


def close_active_channel(state: StreamState) -> None:
    if state.active_channel is None:
        return
    sys.stdout.write("\n")
    sys.stdout.flush()
    state.active_channel = None


def compact_details(payload: dict[str, Any]) -> str:
    details: list[str] = []
    for key in (
        "sequence_number",
        "output_index",
        "content_index",
        "summary_index",
        "annotation_index",
        "item_id",
    ):
        if key in payload:
            details.append(f"{key}={payload[key]}")

    item = payload.get("item")
    if isinstance(item, dict):
        item_type = item.get("type")
        if item_type:
            details.append(f"item.type={item_type}")
        for key in ("name", "call_id", "status", "query"):
            if item.get(key):
                details.append(f"{key}={item[key]}")

    part = payload.get("part")
    if isinstance(part, dict) and part.get("type"):
        details.append(f"part.type={part['type']}")

    annotation = payload.get("annotation")
    if isinstance(annotation, dict):
        annotation_type = annotation.get("type")
        if annotation_type:
            details.append(f"annotation.type={annotation_type}")
        if annotation.get("url"):
            details.append(f"url={annotation['url']}")

    response = payload.get("response")
    if isinstance(response, dict):
        if response.get("id"):
            details.append(f"response.id={response['id']}")
        if response.get("status"):
            details.append(f"response.status={response['status']}")

    if payload.get("error"):
        details.append("error.present=true")

    return " ".join(details)


def maybe_print_json(payload: dict[str, Any], enabled: bool) -> None:
    if not enabled:
        return
    print_line(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def append_text(state: StreamState, channel: str, item_id: str | None, delta: str) -> None:
    if channel == "reasoning_text":
        state.reasoning_text.append(delta)
    elif channel == "reasoning_summary_text":
        state.reasoning_summary_text.append(delta)
    elif channel == "output_text":
        state.output_text.append(delta)
    elif channel == "refusal_text":
        state.refusal_text.append(delta)
    elif channel == "audio_transcript":
        state.audio_transcript.append(delta)
    elif channel == "function_call_arguments":
        state.function_call_arguments[item_id or "__unknown__"].append(delta)
    elif channel == "custom_tool_call_input":
        state.custom_tool_call_input[item_id or "__unknown__"].append(delta)
    elif channel == "mcp_call_arguments":
        state.mcp_call_arguments[item_id or "__unknown__"].append(delta)
    elif channel == "code_interpreter_code":
        state.code_interpreter_code[item_id or "__unknown__"].append(delta)


def delta_header(event_type: str, payload: dict[str, Any]) -> str:
    item_id = payload.get("item_id")
    if item_id:
        return f"{event_type} item_id={item_id}"
    return event_type


def handle_text_delta_event(
    state: StreamState,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    channel = TEXT_DELTA_EVENT_CHANNELS[event_type]
    item_id = typing.cast(str | None, payload.get("item_id"))
    if channel in {"function_call_arguments", "custom_tool_call_input", "mcp_call_arguments", "code_interpreter_code"}:
        channel_key = f"{channel}:{item_id or '__unknown__'}"
    else:
        channel_key = channel
    header = delta_header(event_type, payload)
    ensure_channel(state, channel_key, header)

    delta = str(payload.get("delta") or "")
    append_text(state, channel, item_id, delta)
    sys.stdout.write(delta)
    sys.stdout.flush()


def handle_text_done_event(state: StreamState, event_type: str, payload: dict[str, Any]) -> None:
    close_active_channel(state)
    details = compact_details(payload)
    suffix = f" {details}" if details else ""
    print_line(f"[{now_stamp()}] {event_type}{suffix}")


def handle_non_text_event(
    state: StreamState,
    event_type: str,
    payload: dict[str, Any],
    *,
    show_json: bool,
) -> None:
    close_active_channel(state)

    if event_type == "response.audio.delta":
        state.audio_delta_chunks += 1
        chunk_size = len(str(payload.get("delta") or ""))
        print_line(
            f"[{now_stamp()}] {event_type} sequence_number={payload.get('sequence_number')} "
            f"delta_chars={chunk_size}"
        )
        maybe_print_json(payload, show_json)
        return

    details = compact_details(payload)
    suffix = f" {details}" if details else ""
    print_line(f"[{now_stamp()}] {event_type}{suffix}")

    if event_type in LIFECYCLE_EVENTS:
        response = payload.get("response")
        if isinstance(response, dict):
            state.final_response = response
            state.response_id = typing.cast(str | None, response.get("id")) or state.response_id
            state.response_status = typing.cast(str | None, response.get("status")) or state.response_status

    if event_type == "response.completed":
        response = payload.get("response")
        if isinstance(response, dict):
            state.final_response = response
            state.response_id = typing.cast(str | None, response.get("id")) or state.response_id
            state.response_status = typing.cast(str | None, response.get("status")) or state.response_status

    if event_type not in RESOLVED_RESPONSE_STREAM_EVENT_TYPE_SET:
        state.unknown_events.add(event_type)

    force_json = show_json or event_type in {"error", "response.failed", "response.incomplete"}
    maybe_print_json(payload, force_json)


def handle_event_message(state: StreamState, message: SSEMessage, *, show_json: bool) -> bool:
    if message.data == "[DONE]":
        close_active_channel(state)
        print_line(f"[{now_stamp()}] [DONE]")
        return False

    try:
        payload = json.loads(message.data)
    except json.JSONDecodeError:
        close_active_channel(state)
        label = message.event or "unknown_sse_event"
        print_line(f"[{now_stamp()}] {label} (non-JSON data)")
        print_line(message.data)
        return True

    if not isinstance(payload, dict):
        close_active_channel(state)
        label = message.event or "unknown_sse_event"
        print_line(f"[{now_stamp()}] {label} (non-object payload)")
        print_line(json.dumps(payload, ensure_ascii=False, indent=2))
        return True

    event_type = str(payload.get("type") or message.event or "unknown")
    state.event_counts[event_type] += 1

    response = payload.get("response")
    if isinstance(response, dict):
        state.response_id = typing.cast(str | None, response.get("id")) or state.response_id
        state.response_status = typing.cast(str | None, response.get("status")) or state.response_status

    if event_type in TEXT_DELTA_EVENT_CHANNELS:
        handle_text_delta_event(state, event_type, payload)
    elif event_type in TEXT_DONE_EVENTS:
        handle_text_done_event(state, event_type, payload)
    else:
        handle_non_text_event(state, event_type, payload, show_json=show_json)
    return True


def print_banner(
    config: LLMConfig,
    endpoint_url: str,
    request_text: str,
    tools: list[dict[str, Any]] | None,
    payload: dict[str, Any],
    *,
    omit_service_tier: bool,
) -> None:
    print_line("=== Manim Teacher Planner Responses Stream Probe ===")
    print_line(f"backend_root: {BACKEND_ROOT}")
    print_line("prompt_source: plugins.manim.agent_pipeline.teaching_planner._SYSTEM_PLAN")
    print_line("config_source: config/app_settings/settings.toml [manim.llm.analysis]")
    print_line(f"endpoint: {endpoint_url}")
    print_line(f"model: {config.model}")
    print_line(f"reasoning_effort: {config.reasoning_effort}")
    print_line(f"timeout_sec: {config.timeout_sec}")
    if omit_service_tier:
        print_line("service_tier_in_request_json: no (--omit-service-tier; SSE may still show auto upstream)")
    else:
        tier = payload.get("service_tier")
        print_line(f"service_tier_in_request_json: yes ({tier!r})")
    print_line(f"tools_attached: {len(tools or [])}")
    print_line(f"request_preview: {request_text[:120]}")
    print_line(f"docs: {OPENAI_RESPONSES_STREAM_GUIDE_URL}")
    print_line(f"docs: {OPENAI_RESPONSES_STREAM_REFERENCE_URL}")
    print_line(f"docs: {OPENAI_RESPONSES_CREATE_REFERENCE_URL}")
    print_line(
        'sse_snapshot_note: streamed response.* payloads may still show '
        '"response.service_tier": "auto"; that field is upstream routing metadata and '
        "is not necessarily an echo of the POST body's service_tier. "
        "The lines above (service_tier_in_request_json / effective_payload) reflect what httpx sends."
    )


def summarize_final_output(state: StreamState, *, validate_plan_json: bool) -> int:
    close_active_channel(state)
    print_line("\n=== Final Summary ===")
    if state.response_id:
        print_line(f"response_id: {state.response_id}")
    if state.response_status:
        print_line(f"response_status: {state.response_status}")

    known_counts = sorted(state.event_counts.items())
    print_line(f"event_type_count: {len(known_counts)}")
    for event_type, count in known_counts:
        print_line(f"  {event_type}: {count}")

    print_line(f"reasoning_chars: {len(''.join(state.reasoning_text))}")
    print_line(f"reasoning_summary_chars: {len(''.join(state.reasoning_summary_text))}")
    print_line(f"output_text_chars: {len(''.join(state.output_text))}")
    print_line(f"refusal_chars: {len(''.join(state.refusal_text))}")
    print_line(f"audio_transcript_chars: {len(''.join(state.audio_transcript))}")
    print_line(f"audio_delta_chunks: {state.audio_delta_chunks}")
    print_line(f"function_call_streams: {len(state.function_call_arguments)}")
    print_line(f"custom_tool_streams: {len(state.custom_tool_call_input)}")
    print_line(f"mcp_call_streams: {len(state.mcp_call_arguments)}")
    print_line(f"code_interpreter_streams: {len(state.code_interpreter_code)}")

    if state.unknown_events:
        print_line("unknown_events:")
        for event_type in sorted(state.unknown_events):
            print_line(f"  {event_type}")

    if not validate_plan_json:
        return 0

    output_text = "".join(state.output_text).strip()
    if not output_text:
        print_line("plan_json_validation: skipped (no output_text captured)")
        return 0

    try:
        plan = _extract_json_object(output_text)
    except Exception as exc:
        print_line(f"plan_json_validation: failed ({exc})")
        return 1

    sections = plan.get("sections")
    section_count = len(sections) if isinstance(sections, list) else 0
    print_line(f"plan_json_validation: ok sections={section_count}")
    return 0


def send_streaming_request(
    config: LLMConfig,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    show_json: bool,
    validate_plan_json: bool,
    print_request_json: bool = False,
) -> int:
    if not config.api_key:
        raise SystemExit("analysis-stage api_key is empty in settings.toml")

    endpoint_url = config.base_url.rstrip("/") + "/responses"
    state = StreamState()
    timeout = httpx.Timeout(connect=20.0, read=config.timeout_sec, write=20.0, pool=20.0)
    attempt_payload = json.loads(json.dumps(payload))
    if print_request_json:
        print(
            "[stream_teacher_planner_responses] POST /responses body (instructions truncated):\n"
            + json.dumps(_payload_preview_for_logging(attempt_payload), ensure_ascii=False, indent=2),
            file=sys.stderr,
            flush=True,
        )

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(1, 4):
            with client.stream("POST", endpoint_url, headers=headers, json=attempt_payload) as response:
                if response.status_code >= 400:
                    body_text = response.read().decode("utf-8", errors="replace")
                    error_message = parse_error_message(body_text)
                    print_line(f"[{now_stamp()}] HTTP {response.status_code}: {error_message}")
                    if attempt < 3 and _apply_responses_fallbacks(attempt_payload, error_message):
                        compatibility_bits = []
                        if "service_tier" in attempt_payload:
                            compatibility_bits.append(f"service_tier={attempt_payload['service_tier']}")
                        else:
                            compatibility_bits.append("service_tier=<removed>")
                        if "reasoning" in attempt_payload:
                            compatibility_bits.append(
                                "reasoning.effort=" + str(attempt_payload["reasoning"].get("effort"))
                            )
                        else:
                            compatibility_bits.append("reasoning=<removed>")
                        print_line(
                            f"[{now_stamp()}] Retrying with compatibility fallback: "
                            + ", ".join(compatibility_bits)
                        )
                        continue
                    raise SystemExit(
                        f"Streaming request failed with HTTP {response.status_code}: {error_message}"
                    )

                print_line(f"[{now_stamp()}] HTTP {response.status_code}: stream opened")
                print_line(
                    f"[{now_stamp()}] effective_payload.service_tier: "
                    + (
                        repr(attempt_payload["service_tier"])
                        if "service_tier" in attempt_payload
                        else "<key absent (e.g. omit flag or compatibility retry removed it)>"
                    )
                )
                for message in iter_sse_messages(response):
                    should_continue = handle_event_message(state, message, show_json=show_json)
                    if not should_continue:
                        break
                break
        else:
            raise SystemExit("Failed to establish a compatible streaming request after retries.")

    return summarize_final_output(state, validate_plan_json=validate_plan_json)


def main() -> int:
    args = parse_args()
    if args.list_event_types:
        for event_type in resolved_known_event_types():
            print(event_type)
        return 0

    warn_about_event_drift()
    request_text = read_request_text(args)
    tools = load_tools(args.tools_file)
    config = build_runtime_config(args)
    omit_tier, tier_for_json = resolve_service_tier_for_request(args)
    payload = build_request_payload(
        config,
        request_text,
        args.image,
        tools=tools,
        service_tier=tier_for_json,
        omit_service_tier=omit_tier,
    )
    headers = build_headers(config.api_key, args.header)
    endpoint_url = config.base_url.rstrip("/") + "/responses"
    print_banner(config, endpoint_url, request_text, tools, payload, omit_service_tier=omit_tier)

    if args.print_system_prompt:
        print_line("\n=== Exact Planner System Prompt ===")
        print_line(_SYSTEM_PLAN.strip())

    result = send_streaming_request(
        config,
        payload,
        headers,
        show_json=args.show_json,
        validate_plan_json=not args.no_validate_plan_json,
        print_request_json=args.print_request_json,
    )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
