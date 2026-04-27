"""Teaching-planning agent used before Manim code generation."""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .llm import LLMClient, LLMConfig, LLMDeltaCallback


OPENING_STYLES = {
    "question_first",
    "misconception_first",
    "visual_first",
    "result_first",
    "task_first",
    "roadmap_first",
}

ROADMAP_STYLES = {
    "task_line",
    "question_chain",
    "visual_tags",
    "two_step",
    "result_path",
    "classic_outline",
}


_SYSTEM_PLAN = """\
You are a master teacher designing a short educational animation lesson.

Your job is NOT to write Manim code yet. Your job is to plan the teaching.
The final video should feel like a skilled teacher guiding a student from
confusion to understanding, instead of dumping concepts directly.

Think like an excellent classroom teacher, not like a textbook outline writer.
Your plan must tell the next agent HOW the teacher will lead the student:
- what question to ask first,
- what intuition to build before any formal statement,
- what misconception to surface and correct,
- what visual moment should make the student say "哦，原来是这样",
- and how to transition naturally from one step to the next.

Return ONLY a JSON object with this structure:
{
  "lesson_goal": "...",
  "student_profile": "...",
  "teaching_promise": "...",
  "hook": "...",
  "opening": {
    "style": "question_first|misconception_first|visual_first|result_first|task_first|roadmap_first",
    "hook_line": "...",
    "roadmap_style": "task_line|question_chain|visual_tags|two_step|result_path|classic_outline"
  },
  "big_idea": "...",
  "teacher_voice": "...",
  "narrative_arc": ["...", "...", "..."],
  "misconceptions": [
    {
      "mistake": "...",
      "why_student_thinks_so": "...",
      "teacher_response": "..."
    }
  ],
  "sections": [
    {
      "id": "section_1",
      "title": "...",
      "teacher_goal": "...",
      "teacher_move": "...",
      "student_question": "...",
      "why_this_step_now": "...",
      "expected_student_reaction": "...",
      "concrete_example": "...",
      "visual_strategy": "...",
      "board_plan": "...",
      "narration_goal": "...",
      "key_takeaway": "...",
      "check_for_understanding": "...",
      "transition": "..."
    }
  ],
  "closing": {
    "summary": "...",
    "transfer_question": "...",
    "after_class_prompt": "..."
  }
}

Rules:
- Use Chinese for all natural-language fields.
- Make 4-6 sections.
- Every lesson must include an `opening` object.
- `opening.style` must be exactly one of:
  `question_first`, `misconception_first`, `visual_first`,
  `result_first`, `task_first`, `roadmap_first`.
- `opening.roadmap_style` must be exactly one of:
  `task_line`, `question_chain`, `visual_tags`, `two_step`,
  `result_path`, `classic_outline`.
- Every lesson still needs a roadmap, but the roadmap must explain how THIS
  lesson will proceed. It must not be empty motivation or vague slogans.
- Do NOT default to a numbered "我们将看懂三件事" outline.
  `classic_outline` is only one roadmap style, not the default.
- The first section must motivate the problem and establish a concrete path into the lesson.
- Do NOT write generic section titles like "定义", "性质", "应用" unless they are made specific.
- Each section must have a clear teacher intention, not just a concept label.
- Each section must include a real teacher move, such as: 提问, 对比, 预测, 纠错, 拆解, 回扣, 总结.
- Prefer concrete examples, causal reasoning, misconception correction, and natural transitions.
- The plan should feel spoken and classroom-like, not like a chapter outline.
- At least 2 sections should include a student-facing prediction or check question.
- At least 1 misconception should be corrected inside the main lesson, not only listed abstractly.
- `visual_strategy` and `board_plan` must be specific enough that a code generator can turn them into a clean scene.
- Keep the lesson progression natural: hook -> intuition -> mechanism -> conclusion -> transfer.
- Keep every field concise but specific. Avoid empty slogans like "帮助学生理解".
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


def _text(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        value = value.strip()
        return value or default
    if value is None:
        return default
    return str(value).strip() or default


def _enum_choice(value: Any, choices: set[str], default: str) -> str:
    choice = _text(value, default).lower()
    if choice in choices:
        return choice
    return default


def _normalize_opening(opening: Any, hook_fallback: str) -> Dict[str, str]:
    opening = opening if isinstance(opening, dict) else {}
    hook_line = _text(
        opening.get("hook_line"),
        hook_fallback or "先用一句具体问题把学生带进这节课。",
    )
    return {
        "style": _enum_choice(opening.get("style"), OPENING_STYLES, "question_first"),
        "hook_line": hook_line,
        "roadmap_style": _enum_choice(
            opening.get("roadmap_style"),
            ROADMAP_STYLES,
            "task_line",
        ),
    }


def _normalize_misconceptions(items: Any) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    if not isinstance(items, list):
        items = []
    for idx, item in enumerate(items, start=1):
        if isinstance(item, dict):
            normalized.append({
                "mistake": _text(item.get("mistake"), f"常见误区 {idx}"),
                "why_student_thinks_so": _text(
                    item.get("why_student_thinks_so"),
                    "学生容易被表面现象带偏。",
                ),
                "teacher_response": _text(
                    item.get("teacher_response"),
                    "先顺着这个直觉走一步，再用图像或反例把它纠正。",
                ),
            })
        else:
            mistake = _text(item, f"常见误区 {idx}")
            normalized.append({
                "mistake": mistake,
                "why_student_thinks_so": "学生容易根据字面意思或局部观察直接下结论。",
                "teacher_response": "老师先承认这个直觉为什么合理，再用图像或对比把它翻过来。",
            })
    return normalized


def _normalize_sections(items: Any) -> List[Dict[str, str]]:
    sections: List[Dict[str, str]] = []
    if not isinstance(items, list):
        items = []

    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        sections.append({
            "id": _text(item.get("id"), f"section_{idx}"),
            "title": _text(item.get("title"), f"第{idx}步"),
            "teacher_goal": _text(item.get("teacher_goal"), "让学生在这一段真正看懂当前关键点。"),
            "teacher_move": _text(item.get("teacher_move"), "先提问，再用图像带学生自己得出结论。"),
            "student_question": _text(item.get("student_question"), "学生此刻最可能会问什么？"),
            "why_this_step_now": _text(item.get("why_this_step_now"), "这一步承接上一步的疑问，继续推进理解。"),
            "expected_student_reaction": _text(
                item.get("expected_student_reaction"),
                "学生会从模糊转向能描述出关键关系。",
            ),
            "concrete_example": _text(item.get("concrete_example"), "给一个具体、直观、可画出来的例子。"),
            "visual_strategy": _text(item.get("visual_strategy"), "用一个干净的图像或动画展示核心变化。"),
            "board_plan": _text(item.get("board_plan"), "黑板上只保留这一段最关键的图和一句结论。"),
            "narration_goal": _text(item.get("narration_goal"), "旁白要像老师在带着学生看，不是念定义。"),
            "key_takeaway": _text(item.get("key_takeaway"), "这一段结束时，学生应能用自己的话说出关键结论。"),
            "check_for_understanding": _text(
                item.get("check_for_understanding"),
                "停一下，问学生能不能预测下一步会发生什么。",
            ),
            "transition": _text(item.get("transition"), "顺着这个发现，自然进入下一段。"),
        })

    return sections


def _normalize_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    closing_raw = plan.get("closing") if isinstance(plan.get("closing"), dict) else {}
    hook = _text(plan.get("hook"), "先从学生最困惑的一句话切入。")
    opening = _normalize_opening(plan.get("opening"), hook)

    normalized = {
        "lesson_goal": _text(plan.get("lesson_goal"), "帮助学生真正看懂这个问题背后的核心关系。"),
        "student_profile": _text(
            plan.get("student_profile"),
            "学生知道关键词，但缺少直观画面和因果链条。",
        ),
        "teaching_promise": _text(
            plan.get("teaching_promise"),
            "这节短课会先把画面感建立起来，再把结论讲透。",
        ),
        "hook": _text(plan.get("hook"), opening["hook_line"]),
        "opening": opening,
        "big_idea": _text(plan.get("big_idea"), "把现象、图像和结论连成一条因果线。"),
        "teacher_voice": _text(
            plan.get("teacher_voice"),
            "像经验丰富的老师，先共情困惑，再一步步带学生看懂。",
        ),
        "narrative_arc": (
            plan.get("narrative_arc")
            if isinstance(plan.get("narrative_arc"), list)
            else [
                "先让学生知道为什么要学",
                "再把抽象概念变成画面",
                "最后把画面收束成稳定结论",
            ]
        ),
        "misconceptions": _normalize_misconceptions(plan.get("misconceptions")),
        "sections": _normalize_sections(plan.get("sections")),
        "closing": {
            "summary": _text(closing_raw.get("summary"), "最后把整节内容收成一句学生记得住的话。"),
            "transfer_question": _text(
                closing_raw.get("transfer_question"),
                "换一个情境时，这个思路还能怎么用？",
            ),
            "after_class_prompt": _text(
                closing_raw.get("after_class_prompt"),
                "留一个很短的问题，让学生课后还能回想今天的核心画面。",
            ),
        },
    }

    if not normalized["sections"]:
        normalized["sections"] = [{
            "id": "section_1",
            "title": "问题导入",
            "teacher_goal": "先抓住学生真正的疑问，再给出这节课的推进路径。",
            "teacher_move": "用一个贴近学生困惑的问题开场。",
            "student_question": "这到底在讲什么，为什么会这样？",
            "why_this_step_now": "学生先得知道自己为什么要继续看下去。",
            "expected_student_reaction": "愿意跟着老师继续往下看。",
            "concrete_example": "从最直观的现象或生活画面切入。",
            "visual_strategy": "用一个简单画面建立问题情境。",
            "board_plan": "先摆出问题，再给出这节课的观察路径。",
            "narration_goal": "像老师在黑板前先把任务交代清楚。",
            "key_takeaway": "先抓住问题，再进入理解过程。",
            "check_for_understanding": "你现在最想先弄懂哪一步？",
            "transition": "接下来把这个问题拆开看。",
        }]

    return normalized


class TeachingPlannerAgent:
    """LLM-backed agent that plans the lesson before code generation."""

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

    def plan(
        self,
        request_text: str,
        image_path: Optional[Path] = None,
        *,
        on_delta: LLMDeltaCallback | None = None,
    ) -> Dict:
        content = [{"type": "input_text", "text": request_text}]
        if image_path and image_path.exists():
            content.append({
                "type": "input_image",
                "image_url": _image_to_data_url(image_path),
            })

        for attempt in range(3):
            try:
                text = self.client.generate_text(
                    _SYSTEM_PLAN,
                    content,
                    max_retries=1,
                    on_delta=on_delta,
                )
                return _normalize_plan(_extract_json_object(text))
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))

        raise RuntimeError("Teaching plan generation failed")
