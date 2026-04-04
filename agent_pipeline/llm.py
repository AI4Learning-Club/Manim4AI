"""Shared OpenAI LLM routing for the agent pipeline."""

from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, FrozenSet, List

from openai import OpenAI

# Responses API stream events that carry user-visible text deltas.
_STREAM_TEXT_DELTA_TYPES: FrozenSet[str] = frozenset({
    "response.output_text.delta",
    "response.reasoning_text.delta",
    "response.reasoning_summary_text.delta",
})


def _print_stream_text_deltas(event: Any) -> None:
    """Print incremental model text to stdout so CLI runs feel live."""
    et = getattr(event, "type", None)
    if et not in _STREAM_TEXT_DELTA_TYPES:
        return
    delta = getattr(event, "delta", None) or ""
    if delta:
        print(delta, end="", flush=True)


def _log_api(message: str) -> None:
    """Timestamped API trace (start/end/duration), same style as main._log."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [API] {message}", flush=True)


DEFAULT_OPENAI_BASE_URL = "https://api2.tabcode.cc/openai"


def _first_non_empty(*values: str | None) -> str:
    for value in values:
        if value and str(value).strip():
            return str(value).strip()
    return ""


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class LLMConfig:
    stage: str
    model: str
    api_key: str
    base_url: str
    provider: str = "openai"
    timeout_sec: float = 180.0
    max_tokens: int = 32000

    def summary(self) -> Dict[str, Any]:
        data = asdict(self)
        data["api_key"] = "***" if self.api_key else ""
        return data


def _resolve_stage_config(stage: str) -> LLMConfig:
    prefix = f"A4L_{stage.upper()}"
    api_key = _first_non_empty(
        os.environ.get(f"{prefix}_API_KEY"),
        os.environ.get("OPENAI_API_KEY"),
    )
    base_url = _first_non_empty(
        os.environ.get(f"{prefix}_BASE_URL"),
        os.environ.get("OPENAI_BASE_URL"),
        DEFAULT_OPENAI_BASE_URL,
    )
    model = _first_non_empty(
        os.environ.get(f"{prefix}_MODEL"),
        os.environ.get("OPENAI_MODEL"),
        "gpt-5.4",
    )
    return LLMConfig(
        stage=stage,
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout_sec=_float_env(f"{prefix}_TIMEOUT_SEC", 180.0),
        max_tokens=max(1024, _int_env(f"{prefix}_MAX_TOKENS", 32000)),
    )


def resolve_pipeline_llm_configs() -> Dict[str, LLMConfig]:
    return {
        "analysis": _resolve_stage_config("analysis"),
        "code": _resolve_stage_config("code"),
    }


def validate_pipeline_llm_configs(configs: Dict[str, LLMConfig]) -> None:
    for stage, config in configs.items():
        if not config.api_key:
            env_hint = f"A4L_{stage.upper()}_API_KEY or OPENAI_API_KEY"
            raise RuntimeError(f"{stage} stage requires an API key. Set {env_hint}.")


class LLMClient:
    """Thin OpenAI wrapper shared by planner, asset selector, and codegen."""

    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = OpenAI(
            api_key=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_sec,
        )

    def _call_openai(self, system: str, user_content: List[Dict[str, Any]]) -> str:
        stage = self.config.stage
        model = self.config.model
        _log_api(f"start stage={stage} model={model}")
        t0 = time.time()
        try:
            full_content = [{"type": "input_text", "text": system}] + user_content
            print(f"\n[LLM · {stage}] {model}\n", flush=True)
            with self.client.responses.stream(
                model=self.config.model,
                input=[{"role": "user", "content": full_content}],
                max_output_tokens=self.config.max_tokens,
                service_tier="priority",
            ) as stream:
                for event in stream:
                    _print_stream_text_deltas(event)
                final = stream.get_final_response()
                text = (final.output_text or "").strip()
            print(flush=True)
            return text
        finally:
            elapsed = time.time() - t0
            _log_api(f"end stage={stage} model={model} duration_sec={elapsed:.2f}")

    def generate_text(
        self,
        system: str,
        user_content: List[Dict[str, Any]],
        *,
        max_retries: int = 3,
    ) -> str:
        for attempt in range(max_retries):
            try:
                text = self._call_openai(system, user_content)
                if text:
                    return text
                raise TimeoutError("Empty response from API")
            except Exception as exc:
                if attempt >= max_retries - 1:
                    _log_api(
                        f"failed stage={self.config.stage} model={self.config.model} "
                        f"after {max_retries} attempt(s): {exc!r}"
                    )
                    raise
                wait = 5 * (attempt + 1)
                _log_api(
                    f"retry stage={self.config.stage} in {wait}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(wait)
        raise RuntimeError("LLM call failed")
