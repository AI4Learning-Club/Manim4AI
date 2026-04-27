"""Shared OpenAI LLM routing for the agent pipeline."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional

from openai import OpenAI

from plugins.manim.agent_pipeline.tool_runtime import ToolResult
from plugins.manim.runtime_config import get_manim_settings


DEFAULT_OPENAI_BASE_URL = "https://api2.tabcode.cc/openai"
LLMDeltaCallback = Callable[[str], None]


class StreamTerminated(Exception):
    """Signal that a streaming caller has enough text and wants to stop early."""


@dataclass(frozen=True)
class LLMConfig:
    stage: str
    model: str
    api_key: str
    base_url: str
    provider: str = "openai"
    timeout_sec: float = 180.0
    max_tokens: int = 32000  # from settings; not passed to Responses/Chat APIs (gateway defaults)

    def summary(self) -> dict[str, Any]:
        data = asdict(self)
        data["api_key"] = "***" if self.api_key else ""
        return data


def _resolve_stage_config(stage: str) -> LLMConfig:
    manim_settings = get_manim_settings()
    stage_settings = getattr(manim_settings.llm, stage, None)
    if stage_settings is None:
        raise RuntimeError(f"Unknown Manim LLM stage: {stage}")
    return LLMConfig(
        stage=stage,
        provider=stage_settings.provider,
        model=stage_settings.model,
        api_key=stage_settings.api_key,
        base_url=stage_settings.base_url or DEFAULT_OPENAI_BASE_URL,
        timeout_sec=stage_settings.timeout_sec,
        max_tokens=max(1024, int(stage_settings.max_tokens)),
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
    required_stages: Optional[tuple[str, ...]] = None,
) -> None:
    check_stages = required_stages or ("analysis", "code")
    for stage in check_stages:
        config = configs.get(stage)
        if config and not config.api_key:
            raise RuntimeError(
                f"{stage} stage requires an API key. "
                f"Configure settings.toml at [manim.llm.{stage}].api_key."
            )


def _flatten_user_content_for_tools(user_content: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for item in user_content:
        if item.get("type") == "input_text":
            parts.append(str(item.get("text", "")))
        elif item.get("type") == "input_image":
            parts.append("[image omitted in tool-repair loop]")
    return "\n\n".join(parts).strip()


class LLMClient:
    """Thin OpenAI wrapper shared by planner, asset selector, and codegen."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_sec,
        )

    def _prefers_chat_completions(self) -> bool:
        provider = (self.config.provider or "").strip().lower()
        base_url = (self.config.base_url or "").strip().lower()
        return (
            provider in {"zhipu", "glm", "bigmodel", "moonshot"}
            or "bigmodel.cn" in base_url
            or "moonshot.ai" in base_url
        )

    def _to_chat_messages(self, system: str, user_content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        content: List[Dict[str, Any]] = []
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
        user_content: List[Dict[str, Any]],
        *,
        on_delta: LLMDeltaCallback | None = None,
    ) -> str:
        # Responses API: system/developer text belongs in `instructions`. Many gateways
        # (and our frp proxy) return 400 "Instructions are required" if it is omitted.
        instructions = (system or "").strip()
        if not instructions:
            instructions = "You are a helpful assistant."
        content = list(user_content)
        if not content:
            content = [{"type": "input_text", "text": ""}]

        # Do not pass max_output_tokens: several OpenAI-compatible gateways reject it or
        # only accept narrow ranges; omitting uses the upstream default.
        create_kwargs: Dict[str, Any] = {
            "model": self.config.model,
            "instructions": instructions,
            "input": [{"role": "user", "content": content}],
            "stream": True,
            # Official Responses API parameter; some gateways may not support it.
            "service_tier": "priority",
        }
        try:
            resp = self.client.responses.create(**create_kwargs)
        except Exception as exc:
            message = str(exc)
            if "Unsupported parameter" in message and "service_tier" in message:
                create_kwargs.pop("service_tier", None)
                resp = self.client.responses.create(**create_kwargs)
            else:
                raise
        text = ""
        last_chunk = time.time()
        for event in resp:
            if time.time() - last_chunk > self.config.timeout_sec:
                break
            if hasattr(event, "type") and event.type == "response.output_text.delta":
                text += event.delta
                if on_delta is not None and event.delta:
                    try:
                        on_delta(event.delta)
                    except StreamTerminated:
                        break
                last_chunk = time.time()
        return text.strip()

    def _call_chat_completions_api(
        self,
        system: str,
        user_content: List[Dict[str, Any]],
        *,
        on_delta: LLMDeltaCallback | None = None,
    ) -> str:
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
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            text = "".join(parts).strip()
        else:
            text = str(content).strip()
        if on_delta is not None and text:
            on_delta(text)
        return text

    def _call_openai(
        self,
        system: str,
        user_content: List[Dict[str, Any]],
        *,
        on_delta: LLMDeltaCallback | None = None,
    ) -> str:
        if self._prefers_chat_completions():
            return self._call_chat_completions_api(system, user_content, on_delta=on_delta)
        try:
            return self._call_responses_api(system, user_content, on_delta=on_delta)
        except Exception as exc:
            message = str(exc)
            if "/responses" in message or "Not Found" in message or "404" in message:
                return self._call_chat_completions_api(system, user_content, on_delta=on_delta)
            raise

    def generate_text(
        self,
        system: str,
        user_content: List[Dict[str, Any]],
        *,
        max_retries: int = 3,
        on_delta: LLMDeltaCallback | None = None,
    ) -> str:
        for attempt in range(max_retries):
            try:
                text = self._call_openai(system, user_content, on_delta=on_delta)
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
        user_content: List[Dict[str, Any]],
        *,
        tools: List[Dict[str, Any]],
        dispatch: Callable[[str, Dict[str, Any]], ToolResult],
        max_iterations: int = 8,
    ) -> tuple[str, Dict[str, Any]]:
        """Chat Completions + tool_calls loop (read/search/patch). Responses API is not used."""
        messages: List[Dict[str, Any]] = []
        if (system or "").strip():
            messages.append({"role": "system", "content": system.strip()})
        messages.append({"role": "user", "content": _flatten_user_content_for_tools(user_content)})

        meta: Dict[str, Any] = {
            "tool_rounds": 0,
            "tool_calls": 0,
            "fallback_required": False,
            "finish_summary": "",
            "stopped_reason": "",
        }

        for _ in range(max(1, max_iterations)):
            create_kwargs: Dict[str, Any] = {
                "model": self.config.model,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
                "stream": False,
                "timeout": self.config.timeout_sec,
            }
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

            assistant_msg: Dict[str, Any] = {
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
