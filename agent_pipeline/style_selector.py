"""LLM-backed explanation-style selector for Manim fast-path templates."""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from .fast_paths import list_fast_path_categories
from .llm import LLMClient, LLMConfig, LLMDeltaCallback, LLMEventCallback


_SYSTEM_STYLE_SELECT = """\
You are selecting the best explanation-style template for an educational animation request.

You are NOT choosing the topic. You are choosing HOW the lesson should be explained.

Rules:
- Read the full list of candidate explanation styles.
- Choose the single best `category_id` from that list.
- Base the decision on the user's requested explanation style, depth, pacing, and presentation preference.
- If the request does not explicitly say a style, infer the most helpful explanation style from phrasing and tone.
- Prefer explanation-mode fit over topic words.
- Return EXACTLY ONE JSON object.
- The first emitted non-whitespace character must be `{`.
- Do not emit markdown fences or commentary.

Return:
{
  "category_id": "...",
  "reason": "...",
  "confidence": "low|medium|high"
}
"""


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError("Failed to parse style selector JSON")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("Style selector output is not a JSON object")
    return data


class ExplanationStyleSelector:
    def __init__(
        self,
        api_key: str | LLMConfig,
        base_url: str = "https://api2.tabcode.cc/openai",
        model: str = "gpt-5.4",
    ):
        if isinstance(api_key, LLMConfig):
            llm_config = api_key
            self.model = llm_config.model
            self.client = LLMClient(llm_config)
        else:
            self.model = model
            self.client = LLMClient(
                LLMConfig(
                    stage="adhoc",
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                ),
            )

    def select(
        self,
        request_text: str,
        *,
        on_delta: LLMDeltaCallback | None = None,
        on_event: LLMEventCallback | None = None,
    ) -> Dict[str, Any]:
        categories = list_fast_path_categories()
        content: List[Dict[str, str]] = [
            {
                "type": "input_text",
                "text": (
                    "## User request\n"
                    f"{request_text}\n\n"
                    "## Explanation style candidates\n"
                    f"{json.dumps(categories, ensure_ascii=False, indent=2)}"
                ),
            }
        ]
        for attempt in range(3):
            try:
                if on_event is None:
                    text = self.client.generate_text(
                        _SYSTEM_STYLE_SELECT,
                        content,
                        max_retries=1,
                        on_delta=on_delta,
                    )
                else:
                    text = self.client.generate_text(
                        _SYSTEM_STYLE_SELECT,
                        content,
                        max_retries=1,
                        on_delta=on_delta,
                        on_event=on_event,
                    )
                parsed = _extract_json_object(text)
                category_id = str(parsed.get("category_id") or "").strip()
                if not category_id:
                    raise ValueError("Style selector returned empty category_id")
                return {
                    "category_id": category_id,
                    "reason": str(parsed.get("reason") or "").strip(),
                    "confidence": str(parsed.get("confidence") or "").strip() or "medium",
                }
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))
        raise RuntimeError("Style selection failed")

