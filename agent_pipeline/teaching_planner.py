"""Teaching-planning agent used before Manim code generation."""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Dict, Optional

from openai import OpenAI


_SYSTEM_PLAN = """\
You are a master teacher designing a short educational animation lesson.

Your job is NOT to write Manim code yet. Your job is to plan the teaching.
The final video should feel like a skilled teacher is guiding a student from
confusion to understanding, instead of dumping concepts directly.

Return ONLY a JSON object with this structure:
{
  "lesson_goal": "...",
  "student_profile": "...",
  "hook": "...",
  "big_idea": "...",
  "misconceptions": ["...", "..."],
  "sections": [
    {
      "id": "section_1",
      "title": "...",
      "teacher_goal": "...",
      "student_question": "...",
      "visual_strategy": "...",
      "board_plan": "...",
      "narration_goal": "...",
      "check_for_understanding": "..."
    }
  ],
  "closing": {
    "summary": "...",
    "transfer_question": "..."
  }
}

Rules:
- Use Chinese for all natural-language fields.
- Make 4-6 sections.
- The first section must motivate the problem and tell the student what they will learn.
- Each section must have a clear teacher intention, not just a concept label.
- Prefer concrete examples, causal reasoning, and misconception correction.
- visual_strategy and board_plan must be specific enough that a code generator can turn them into a clean scene.
- Keep the lesson progression natural: hook -> intuition -> mechanism -> conclusion -> transfer.
"""


def _image_to_data_url(path: Path) -> str:
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    suffix = path.suffix.lower().lstrip(".")
    mime = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(suffix, "image/png")
    return f"data:{mime};base64,{b64}"


def _extract_json_object(text: str) -> Dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        data = json.loads(match.group(0))
        if isinstance(data, dict):
            return data

    raise ValueError("Failed to parse teaching plan JSON from model output")


class TeachingPlannerAgent:
    """LLM-backed agent that plans the lesson before code generation."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.tabcode.cc/openai",
        model: str = "gpt-5.4",
    ):
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=180.0)

    def plan(self, request_text: str, image_path: Optional[Path] = None) -> Dict:
        content = [{"type": "input_text", "text": request_text}]
        if image_path and image_path.exists():
            content.append({
                "type": "input_image",
                "image_url": _image_to_data_url(image_path),
            })

        full_content = [{"type": "input_text", "text": _SYSTEM_PLAN}] + content
        for attempt in range(3):
            try:
                resp = self.client.responses.create(
                    model=self.model,
                    input=[{"role": "user", "content": full_content}],
                    stream=True,
                )
                text = ""
                last_chunk = time.time()
                for event in resp:
                    if time.time() - last_chunk > 180:
                        break
                    if hasattr(event, "type") and event.type == "response.output_text.delta":
                        text += event.delta
                        last_chunk = time.time()
                return _extract_json_object(text)
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))

        raise RuntimeError("Teaching plan generation failed")