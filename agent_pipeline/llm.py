"""Shared OpenAI LLM routing for the agent pipeline."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from openai import OpenAI

from plugins.manim.agent_pipeline.tool_runtime import ToolResult
from plugins.manim.runtime_config import get_manim_settings
from service.llm_traffic_control import llm_traffic_controller

DEFAULT_OPENAI_BASE_URL = "https://api2.tabcode.cc/openai"
LLMDeltaCallback = Callable[[str], None]
LLMEventCallback = Callable[[dict[str, Any]], None]
SUPPORTED_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
DEFAULT_REASONING_SUMMARY = "auto"
_RESPONSES_TRANSIENT_RETRY_DELAYS_SEC = (2.0, 5.0)
_RESPONSES_RETRYABLE_STATUS_CODES = {408, 409, 429}
_RESPONSES_RETRYABLE_ERROR_MARKERS = (
    "upstream request failed",
    "upstream_error",
    "internalservererror",
    "internal server error",
    "bad gateway",
    "gateway timeout",
    "service unavailable",
    "temporarily unavailable",
    "too many requests",
    "rate limit",
    "capacity limit",
    "concurrency limit",
    "peer closed connection without sending complete message body",
    "incomplete chunked read",
    "incomplete read",
    "server disconnected without sending a response",
    "connection reset by peer",
    "unexpected eof",
)

# HTTP/SSE client timeout (seconds) by Responses API reasoning.effort tier.
_REASONING_TIMEOUT_SEC: dict[str, float] = {
    "none": 30.0,
    "minimal": 30.0,
    "low": 120.0,
    "medium": 240.0,
    "high": 600.0,
    "xhigh": 600.0,
}


def timeout_sec_for_reasoning_effort(reasoning_effort: str | None) -> float:
    """Derive OpenAI client timeout from reasoning.effort (30s / 2m / 4m / 10m tiers)."""
    normalized = _normalize_reasoning_effort(reasoning_effort)
    if normalized is None:
        return _REASONING_TIMEOUT_SEC["none"]
    return _REASONING_TIMEOUT_SEC.get(normalized, _REASONING_TIMEOUT_SEC["high"])


def effective_manim_stage_timeout_sec(
    *,
    reasoning_effort: str,
    timeout_sec_override: float | None,
) -> float:
    """Use explicit ``timeout_sec`` from settings when set; else derive from ``reasoning_effort``."""
    if timeout_sec_override is not None:
        return float(timeout_sec_override)
    return timeout_sec_for_reasoning_effort(reasoning_effort)


_REASONING_DELTA_EVENTS = {
    "response.reasoning_text.delta",
    "response.reasoning_summary_text.delta",
}
_REASONING_DONE_EVENTS = {
    "response.reasoning_text.done",
    "response.reasoning_summary_text.done",
}
_LIFECYCLE_EVENTS = {
    "response.created",
    "response.queued",
    "response.in_progress",
    "response.completed",
    "response.incomplete",
    "response.failed",
    "error",
}


class StreamTerminated(Exception):  # noqa: N818
    """Signal that a streaming caller has enough text and wants to stop early."""

    def __init__(
        self,
        message: str = "Stream terminated by callback",
        *,
        text_override: str | None = None,
        trim_length: int | None = None,
    ) -> None:
        super().__init__(message)
        self.text_override = text_override
        self.trim_length = trim_length


def _apply_stream_termination(text: str, exc: StreamTerminated) -> str:
    if exc.text_override is not None:
        return exc.text_override.strip()
    if exc.trim_length is not None:
        return text[: max(0, exc.trim_length)].strip()
    return text.strip()


def _emit_llm_event(callback: LLMEventCallback | None, payload: dict[str, Any]) -> None:
    if callback is None:
        return
    callback(payload)


@dataclass(frozen=True)
class LLMConfig:
    stage: str
    model: str
    api_key: str
    base_url: str
    provider: str = "openai"
    provider_name: str = ""
    traffic_provider: str = ""
    timeout_sec: float = 180.0
    reasoning_effort: str = ""

    def summary(self) -> dict[str, Any]:
        data = asdict(self)
        data["api_key"] = "***" if self.api_key else ""
        return data


def _resolve_stage_config(stage: str) -> LLMConfig:
    manim_settings = get_manim_settings()
    stage_settings = getattr(manim_settings.llm, stage, None)
    if stage_settings is None:
        raise RuntimeError(f"Unknown Manim LLM stage: {stage}")
    base_url = stage_settings.base_url or DEFAULT_OPENAI_BASE_URL
    return LLMConfig(
        stage=stage,
        provider=stage_settings.provider,
        provider_name=_resolve_traffic_provider_name(
            stage_settings.provider,
            base_url,
            traffic_provider=stage_settings.traffic_provider,
        ),
        traffic_provider=stage_settings.traffic_provider,
        model=stage_settings.model,
        api_key=stage_settings.api_key,
        base_url=base_url,
        timeout_sec=effective_manim_stage_timeout_sec(
            reasoning_effort=stage_settings.reasoning_effort,
            timeout_sec_override=stage_settings.timeout_sec,
        ),
        reasoning_effort=stage_settings.reasoning_effort,
    )


def resolve_pipeline_llm_configs() -> dict[str, LLMConfig]:
    return {
        "analysis": _resolve_stage_config("analysis"),
        "code": _resolve_stage_config("code"),
        "director": _resolve_stage_config("director"),
    }


def validate_pipeline_llm_configs(
    configs: dict[str, LLMConfig],
    *,
    required_stages: tuple[str, ...] | None = None,
) -> None:
    check_stages = required_stages or ("analysis", "code")
    for stage in check_stages:
        config = configs.get(stage)
        if config and not config.api_key:
            raise RuntimeError(
                f"{stage} stage requires an API key. "
                f"Configure settings.toml at [manim.llm.{stage}].api_key."
            )


def _flatten_user_content_for_tools(user_content: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in user_content:
        if item.get("type") == "input_text":
            parts.append(str(item.get("text", "")))
        elif item.get("type") == "input_image":
            parts.append("[image omitted in tool-repair loop]")
    return "\n\n".join(parts).strip()


def _provider_uses_openai_priority_tier(provider: str | None) -> bool:
    """Only the official OpenAI-compatible stack should send Responses `service_tier`."""
    return (provider or "").strip().lower() == "openai"


def _normalize_base_url(base_url: str | None) -> str:
    return (base_url or "").strip().rstrip("/").lower()


def _should_use_chat_completions(config: LLMConfig) -> bool:
    provider = (config.provider or "").strip().lower()
    base_url = _normalize_base_url(config.base_url)
    if provider == "deepseek":
        return True
    return base_url.startswith("https://api.deepseek.com")


def _resolve_traffic_provider_name(
    provider: str | None,
    base_url: str | None,
    *,
    traffic_provider: str | None = None,
) -> str:
    """Map Manim stage config to the shared provider limiter without changing API semantics."""
    explicit = (traffic_provider or "").strip().lower()
    if explicit:
        return explicit
    return llm_traffic_controller.resolve_provider_name_hint(provider, base_url)


def _normalize_reasoning_effort(reasoning_effort: str | None) -> str | None:
    normalized = (reasoning_effort or "").strip().lower()
    if not normalized:
        return None
    if normalized in SUPPORTED_REASONING_EFFORTS:
        return normalized
    return None


def _apply_responses_fallbacks(request_kwargs: dict[str, Any], error_message: str) -> bool:
    retry = False
    lowered = error_message.lower()

    if "Unsupported parameter" in error_message and "service_tier" in error_message:
        retry = request_kwargs.pop("service_tier", None) is not None or retry

    if "Unsupported parameter" in error_message and "reasoning" in error_message:
        retry = request_kwargs.pop("reasoning", None) is not None or retry

    reasoning = request_kwargs.get("reasoning")
    if (
        isinstance(reasoning, dict)
        and reasoning.get("effort") == "xhigh"
        and "xhigh" in lowered
        and ("unsupported" in lowered or "invalid" in lowered)
    ):
        next_reasoning = dict(reasoning)
        next_reasoning["effort"] = "high"
        request_kwargs["reasoning"] = next_reasoning
        retry = True

    return retry


def _is_retryable_responses_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code >= 500 or status_code in _RESPONSES_RETRYABLE_STATUS_CODES

    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int):
        return response_status >= 500 or response_status in _RESPONSES_RETRYABLE_STATUS_CODES

    text_parts = [str(exc)]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        text_parts.append(json.dumps(body, ensure_ascii=False))
    message = getattr(exc, "message", None)
    if isinstance(message, str):
        text_parts.append(message)
    text = " ".join(text_parts).lower()
    return any(marker in text for marker in _RESPONSES_RETRYABLE_ERROR_MARKERS)


def _is_responses_initial_event_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        "Expected to have received `response.created` before `None`" in message
        or "Expected to have received `response.created` before `NoneType`" in message
    )


class LLMClient:
    """Thin OpenAI wrapper shared by planner, asset selector, and codegen."""

    def __init__(self, config: LLMConfig):
        self.config = config
        effective_timeout = (
            llm_traffic_controller.extend_timeout(
                config.provider_name,
                base_timeout=config.timeout_sec,
            )
            or config.timeout_sec
        )
        default_headers = llm_traffic_controller.build_default_headers(config.provider_name)
        client_kwargs: dict[str, Any] = {
            "api_key": config.api_key,
            "base_url": config.base_url,
            "timeout": effective_timeout,
        }
        if default_headers:
            client_kwargs["default_headers"] = default_headers
        self.client = OpenAI(
            **client_kwargs,
        )

    def _call_responses_api_non_stream(
        self,
        request_kwargs: dict[str, Any],
        *,
        on_delta: LLMDeltaCallback | None = None,
        on_event: LLMEventCallback | None = None,
        attempt: int = 1,
        requested_service_tier: str | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        _emit_llm_event(
            on_event,
            {
                "type": "llm_request_retry",
                "stage": self.config.stage,
                "attempt": attempt,
                "next_attempt": attempt,
                "retry_kind": "responses_non_stream_fallback",
                "endpoint": "responses.create",
            },
        )
        with llm_traffic_controller.provider_scope_sync(
            self.config.provider_name,
            operation=f"manim.{self.config.stage}.responses_non_stream",
        ):
            response = self.client.responses.create(**request_kwargs, stream=False)

        text = str(getattr(response, "output_text", "") or "").strip()
        if text and on_delta is not None:
            try:
                on_delta(text)
            except StreamTerminated as exc:
                return _apply_stream_termination(text, exc)

        effective_service_tier = str(getattr(response, "service_tier", "") or "").strip()
        _emit_llm_event(
            on_event,
            {
                "type": "llm_request_completed",
                "stage": self.config.stage,
                "attempt": attempt,
                "output_chars": len(text),
                "reasoning_chars": 0,
                "reasoning_summary_chars": 0,
                "service_tier_requested": requested_service_tier or "unset",
                "service_tier_effective": effective_service_tier or "unknown",
                "reasoning_summary_requested": DEFAULT_REASONING_SUMMARY if reasoning_effort else "unset",
                "endpoint": "responses.create",
            },
        )
        return text

    def _to_chat_messages(self, system: str, user_content: list[dict[str, Any]]) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = []
        if system.strip():
            content.append({"type": "text", "text": system})

        for item in user_content:
            item_type = item.get("type")
            if item_type == "input_text":
                content.append({"type": "text", "text": item.get("text", "")})
            elif item_type == "input_image":
                image_url = item.get("image_url")
                if image_url:
                    content.append({"type": "image_url", "image_url": {"url": image_url}})

        return [{"role": "user", "content": content}]

    def _call_responses_api(
        self,
        system: str,
        user_content: list[dict[str, Any]],
        *,
        on_delta: LLMDeltaCallback | None = None,
        on_event: LLMEventCallback | None = None,
    ) -> str:
        # Responses API: system/developer text belongs in `instructions`. Many gateways
        # (and our frp proxy) return 400 "Instructions are required" if it is omitted.
        instructions = (system or "").strip()
        if not instructions:
            instructions = "You are a helpful assistant."
        content = list(user_content)
        if not content:
            content = [{"type": "input_text", "text": ""}]

        stream_kwargs: dict[str, Any] = {
            "model": self.config.model,
            "instructions": instructions,
            "input": [{"role": "user", "content": content}],
        }
        if _provider_uses_openai_priority_tier(self.config.provider):
            # Official Responses API parameter; omitted for non-openai providers.
            stream_kwargs["service_tier"] = "priority"
        requested_service_tier = stream_kwargs.get("service_tier")
        reasoning_effort = _normalize_reasoning_effort(self.config.reasoning_effort)
        if reasoning_effort is not None:
            stream_kwargs["reasoning"] = {
                "effort": reasoning_effort,
                "summary": DEFAULT_REASONING_SUMMARY,
            }

        input_text_chars = sum(
            len(str(item.get("text", "") or ""))
            for item in content
            if item.get("type") == "input_text"
        )
        input_image_count = sum(1 for item in content if item.get("type") == "input_image")

        last_error: Exception | None = None
        for attempt in range(3):
            _emit_llm_event(
                on_event,
                {
                    "type": "llm_request_started",
                    "stage": self.config.stage,
                    "provider": self.config.provider,
                    "model": self.config.model,
                    "base_url": self.config.base_url,
                    "reasoning_effort": reasoning_effort or "none",
                    "endpoint": "responses.stream",
                    "attempt": attempt + 1,
                    "input_text_chars": input_text_chars,
                    "input_image_count": input_image_count,
                    "service_tier_requested": requested_service_tier or "unset",
                    "reasoning_summary_requested": DEFAULT_REASONING_SUMMARY if reasoning_effort else "unset",
                },
            )
            try:
                with llm_traffic_controller.provider_scope_sync(
                    self.config.provider_name,
                    operation=f"manim.{self.config.stage}.responses_stream",
                ):
                    with self.client.responses.stream(**stream_kwargs) as stream:
                        text = ""
                        reasoning_active = False
                        reasoning_chars = 0
                        reasoning_summary_chars = 0
                        next_reasoning_notice = 80
                        output_started = False
                        for event in stream:
                            event_type = str(getattr(event, "type", "") or "")
                            if event_type in _LIFECYCLE_EVENTS:
                                _emit_llm_event(
                                    on_event,
                                    {
                                        "type": "llm_stream_status",
                                        "stage": self.config.stage,
                                        "event": event_type,
                                        "status": event_type.removeprefix("response."),
                                        "attempt": attempt + 1,
                                    },
                                )
                            if event_type in _REASONING_DELTA_EVENTS:
                                delta = str(getattr(event, "delta", "") or "")
                                reasoning_chars += len(delta)
                                if event_type == "response.reasoning_summary_text.delta" and delta:
                                    reasoning_summary_chars += len(delta)
                                    _emit_llm_event(
                                        on_event,
                                        {
                                            "type": "llm_reasoning_summary_delta",
                                            "stage": self.config.stage,
                                            "attempt": attempt + 1,
                                            "delta": delta,
                                            "char_count": reasoning_summary_chars,
                                        },
                                    )
                                if not reasoning_active:
                                    reasoning_active = True
                                    _emit_llm_event(
                                        on_event,
                                        {
                                            "type": "llm_reasoning_started",
                                            "stage": self.config.stage,
                                            "event": event_type,
                                            "attempt": attempt + 1,
                                        },
                                    )
                                if reasoning_chars >= next_reasoning_notice:
                                    _emit_llm_event(
                                        on_event,
                                        {
                                            "type": "llm_reasoning_progress",
                                            "stage": self.config.stage,
                                            "event": event_type,
                                            "attempt": attempt + 1,
                                            "char_count": reasoning_chars,
                                        },
                                    )
                                    next_reasoning_notice += 80
                                continue
                            if event_type in _REASONING_DONE_EVENTS and reasoning_active:
                                reasoning_active = False
                                _emit_llm_event(
                                    on_event,
                                    {
                                        "type": "llm_reasoning_completed",
                                        "stage": self.config.stage,
                                        "event": event_type,
                                        "attempt": attempt + 1,
                                        "char_count": reasoning_chars,
                                    },
                                )
                                continue
                            if event_type == "response.output_text.delta":
                                delta = str(getattr(event, "delta", "") or "")
                                if delta and not output_started:
                                    output_started = True
                                    _emit_llm_event(
                                        on_event,
                                        {
                                            "type": "llm_output_started",
                                            "stage": self.config.stage,
                                            "attempt": attempt + 1,
                                        },
                                    )
                                text += delta
                                if on_delta is not None and delta:
                                    try:
                                        on_delta(delta)
                                    except StreamTerminated as exc:
                                        return _apply_stream_termination(text, exc)
                            elif event_type == "response.output_text.done" and not text:
                                text = str(getattr(event, "text", "") or "")

                        final_response = stream.get_final_response()
                        final_text = str(final_response.output_text or "").strip()
                        effective_service_tier = str(
                            getattr(final_response, "service_tier", "") or ""
                        ).strip()
                        resolved_text = text.strip() or final_text
                        _emit_llm_event(
                            on_event,
                            {
                                "type": "llm_request_completed",
                                "stage": self.config.stage,
                                "attempt": attempt + 1,
                                "output_chars": len(resolved_text),
                                "reasoning_chars": reasoning_chars,
                                "reasoning_summary_chars": reasoning_summary_chars,
                                "service_tier_requested": requested_service_tier or "unset",
                                "service_tier_effective": effective_service_tier or "unknown",
                                "reasoning_summary_requested": DEFAULT_REASONING_SUMMARY if reasoning_effort else "unset",
                            },
                        )
                        return resolved_text
            except Exception as exc:
                last_error = exc
                if _is_responses_initial_event_error(exc):
                    return self._call_responses_api_non_stream(
                        dict(stream_kwargs),
                        on_delta=on_delta,
                        on_event=on_event,
                        attempt=attempt + 1,
                        requested_service_tier=requested_service_tier,
                        reasoning_effort=reasoning_effort,
                    )
                before_fallback = dict(stream_kwargs)
                retry = _apply_responses_fallbacks(stream_kwargs, str(exc))
                if retry:
                    removed_keys = sorted(set(before_fallback) - set(stream_kwargs))
                    changed_keys = sorted(
                        key
                        for key, value in stream_kwargs.items()
                        if before_fallback.get(key) != value
                    )
                    _emit_llm_event(
                        on_event,
                        {
                            "type": "llm_request_retry",
                            "stage": self.config.stage,
                            "attempt": attempt + 1,
                            "next_attempt": attempt + 2,
                            "error": str(exc),
                            "removed_keys": removed_keys,
                            "changed_keys": changed_keys,
                        },
                    )
                    continue
                should_retry_transient = (
                    attempt < len(_RESPONSES_TRANSIENT_RETRY_DELAYS_SEC)
                    and _is_retryable_responses_error(exc)
                )
                if should_retry_transient:
                    delay_seconds = _RESPONSES_TRANSIENT_RETRY_DELAYS_SEC[attempt]
                    _emit_llm_event(
                        on_event,
                        {
                            "type": "llm_request_retry",
                            "stage": self.config.stage,
                            "attempt": attempt + 1,
                            "next_attempt": attempt + 2,
                            "error": str(exc),
                            "removed_keys": [],
                            "changed_keys": [],
                            "retry_kind": "transient_provider_error",
                            "retry_in_seconds": delay_seconds,
                        },
                    )
                    time.sleep(delay_seconds)
                    continue
                _emit_llm_event(
                    on_event,
                    {
                        "type": "llm_request_failed",
                        "stage": self.config.stage,
                        "attempt": attempt + 1,
                        "error": str(exc),
                    },
                )
                if not retry:
                    raise
        assert last_error is not None
        raise last_error

    def _call_chat_completions_api(
        self,
        system: str,
        user_content: list[dict[str, Any]],
        *,
        on_delta: LLMDeltaCallback | None = None,
    ) -> str:
        with llm_traffic_controller.provider_scope_sync(
            self.config.provider_name,
            operation=f"manim.{self.config.stage}.chat_completion",
        ):
            resp = self.client.chat.completions.create(
                model=self.config.model,
                messages=self._to_chat_messages(system, user_content),
                stream=False,
                timeout=self.config.timeout_sec,
            )
        message = resp.choices[0].message if resp.choices else None
        content = getattr(message, "content", "") if message else ""
        text = ""
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            text = "".join(parts).strip()
        else:
            text = str(content).strip()
        if on_delta is not None and text:
            try:
                on_delta(text)
            except StreamTerminated as exc:
                return _apply_stream_termination(text, exc)
        return text

    def _call_openai(
        self,
        system: str,
        user_content: list[dict[str, Any]],
        *,
        on_delta: LLMDeltaCallback | None = None,
        on_event: LLMEventCallback | None = None,
    ) -> str:
        if _should_use_chat_completions(self.config):
            return self._call_chat_completions_api(system, user_content, on_delta=on_delta)
        if on_event is None:
            return self._call_responses_api(system, user_content, on_delta=on_delta)
        return self._call_responses_api(system, user_content, on_delta=on_delta, on_event=on_event)

    def generate_text(
        self,
        system: str,
        user_content: list[dict[str, Any]],
        *,
        max_retries: int = 3,
        on_delta: LLMDeltaCallback | None = None,
        on_event: LLMEventCallback | None = None,
    ) -> str:
        for attempt in range(max_retries):
            try:
                if on_event is None:
                    text = self._call_openai(system, user_content, on_delta=on_delta)
                else:
                    text = self._call_openai(system, user_content, on_delta=on_delta, on_event=on_event)
                if text:
                    return text
                raise TimeoutError("Empty response from API")
            except Exception:
                if attempt >= max_retries - 1:
                    raise
                time.sleep(5 * (attempt + 1))
        raise RuntimeError("LLM call failed")

    def generate_with_tool_loop(
        self,
        system: str,
        user_content: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        dispatch: Callable[[str, dict[str, Any]], ToolResult],
        max_iterations: int = 8,
    ) -> tuple[str, dict[str, Any]]:
        """Chat Completions + tool_calls loop (read/search/patch). Responses API is not used."""
        messages: list[dict[str, Any]] = []
        if (system or "").strip():
            messages.append({"role": "system", "content": system.strip()})
        messages.append({"role": "user", "content": _flatten_user_content_for_tools(user_content)})

        meta: dict[str, Any] = {
            "tool_rounds": 0,
            "tool_calls": 0,
            "fallback_required": False,
            "finish_summary": "",
            "stopped_reason": "",
        }

        for _ in range(max(1, max_iterations)):
            create_kwargs: dict[str, Any] = {
                "model": self.config.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "stream": False,
                "timeout": self.config.timeout_sec,
            }
            with llm_traffic_controller.provider_scope_sync(
                self.config.provider_name,
                operation=f"manim.{self.config.stage}.tool_loop",
            ):
                try:
                    resp = self.client.chat.completions.create(
                        **create_kwargs,
                        parallel_tool_calls=False,
                    )
                except TypeError:
                    resp = self.client.chat.completions.create(**create_kwargs)
            choice = resp.choices[0].message if resp.choices else None
            if not choice:
                meta["stopped_reason"] = "empty_message"
                meta["fallback_required"] = True
                return "", meta

            tool_calls = getattr(choice, "tool_calls", None) or []
            if not tool_calls:
                text = choice.content or ""
                if isinstance(text, str):
                    meta["stopped_reason"] = "assistant_text"
                    return text.strip(), meta
                meta["stopped_reason"] = "assistant_non_string"
                meta["fallback_required"] = True
                return "", meta

            meta["tool_rounds"] += 1
            meta["tool_calls"] += len(tool_calls)

            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": choice.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments or "{}",
                        },
                    }
                    for tc in tool_calls
                ],
            }
            messages.append(assistant_msg)

            for tc in tool_calls:
                fname = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except json.JSONDecodeError:
                    args = {}
                result = dispatch(fname, args)
                payload = {
                    "ok": result.ok,
                    "message": result.message,
                    "payload": result.payload,
                }
                if fname == "finish_repair" and result.ok:
                    meta["fallback_required"] = bool(result.payload.get("fallback_required"))
                    meta["finish_summary"] = str(result.payload.get("summary", ""))
                    meta["stopped_reason"] = "finish_repair"
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(payload, ensure_ascii=False),
                        }
                    )
                    return "", meta

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                )

        meta["stopped_reason"] = "max_iterations"
        meta["fallback_required"] = True
        return "", meta
