"""Teaching-planning agent used before Manim code generation."""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Any

from .llm import LLMClient, LLMConfig, LLMDeltaCallback, LLMEventCallback, StreamTerminated

OPENING_STYLES = {
    "question_first",
    "misconception_first",
    "example_first",
    "visual_first",
    "phenomenon_first",
    "result_first",
    "task_first",
    "direct_first",
    "roadmap_first",
}

OPENING_ARCHITECTURES = {
    "question_led",
    "example_led",
    "visual_reveal",
    "direct_explanation",
    "problem_walkthrough",
    "result_backwards",
    "comparison_led",
    "story_led",
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
Return teaching decisions only. Do NOT write Manim code, detailed derivations,
tentative calculations, storyboard-level proof details, or self-correcting
drafts. The final video should feel like a skilled teacher choosing the right
lesson architecture for this topic: sometimes question-led, sometimes
example-led, sometimes visual-first, sometimes direct and concise. It should
never feel like a repeated opening template.

Think like an excellent classroom teacher, not like a textbook outline writer.
First choose the lesson's scene architecture, then tell the next agent HOW the
teacher will lead the student:
- which opening architecture fits this lesson and why,
- whether the first beat should be a question, a concrete example, a visual reveal,
  a result preview, a task read-in, or a direct explanation,
- what intuition to build before any formal statement,
- what misconception to surface and correct when it is actually useful,
- what visual moment should make the student say "哦，原来是这样",
- and how to transition naturally from one step to the next.

Output contract:
- Start output immediately with `{`.
- The first emitted non-whitespace character must be `{`.
- Return EXACTLY ONE top-level JSON object.
- After the matching final `}` of the top-level object, stop immediately.
- Do not emit markdown fences, commentary, prefaces, suffixes, or a second draft.
- Never emit markdown fences, commentary, prefaces, suffixes, scratch work,
  second drafts, self-corrections, or concatenated JSON objects.
- Resolve uncertainty internally before output; never show exploratory drafts.

Return EXACTLY ONE top-level JSON object with this field order and structure:
{
  "lesson_goal": "...",
  "student_profile": "...",
  "teaching_promise": "...",
  "problem_intake": {
    "is_problem_solving": true,
    "restatement": "...",
    "givens": ["...", "..."],
    "target": "...",
    "key_terms": ["...", "..."],
    "visual_marking_plan": "..."
  },
  "hook": "...",
  "opening": {
    "architecture": "question_led|example_led|visual_reveal|direct_explanation|problem_walkthrough|result_backwards|comparison_led|story_led",
    "style": "question_first|misconception_first|example_first|visual_first|phenomenon_first|result_first|task_first|direct_first|roadmap_first",
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

Field semantics:
- `problem_intake.restatement` is the first spoken read-in for a problem-solving
  lesson. It must be 1-2 concise spoken sentences in student language.
- `problem_intake.restatement` is NOT a long prompt copy, a meta summary, a
  derivation preview, or a strategy slogan.
- `opening.architecture` is the whole opening/storyboard structure, not a
  cosmetic label. It should control the first 15-30 seconds of the lesson.
- `opening.hook_line` is the first meaningful opening beat for that structure.
  It may be a question, a concrete example, a visual instruction, a result
  preview, a task statement, or a direct teaching sentence. It is NOT always a
  question.
- For problem-solving lessons, `hook` and `opening.hook_line` come AFTER the
  student has heard the concise read-in and seen the opening marking setup.

Rules:
- JSON string fields must contain final teaching decisions only. Do not include
  visible brainstorming phrases such as "这还不够直接", "换一种", "最终采用",
  "更标准的整理解法", or "课堂中直接采用".
- Use Chinese for all natural-language fields.
- Make 4-6 sections.
- Keep fields short and execution-ready. Prefer 1-2 sentences per string field.
- Do NOT include detailed derivations, tentative数值猜测, competing proof paths,
  or self-correction narrative inside plan fields.
- Every lesson must include an `opening` object.
- Choose the opening architecture deliberately. Do NOT force a question-led
  opening. Use `question_first` / `question_chain` only when a real question is
  the best way into this particular topic.
- Avoid stacking multiple rhetorical questions at the beginning. One strong
  opening beat is usually better than several similar questions.
- Every lesson must include a `problem_intake` object. Set
  `problem_intake.is_problem_solving` to true for concrete exercises, proofs,
  calculations, geometry questions, or image-based problem statements. Set it
  to false for pure concept explanations.
- For problem-solving requests, use `problem_intake` to restate the problem,
  separate givens from the target, list key terms/variables/diagram relations,
  and specify how the first scene should visually mark the important information
  before solving. For pure concept lessons, use it only to name the learner's
  central question and key terms; do not invent a fake exercise.
- For multi-part problems, `problem_intake.restatement` must briefly cover every
  sub-question plus the overall objective in 1-2 spoken sentences, and the
  first scene must use one compact reconstructed题面 card that covers the
  sub-questions before formal analysis begins.
- `opening.architecture` must be exactly one of:
  `question_led`, `example_led`, `visual_reveal`, `direct_explanation`,
  `problem_walkthrough`, `result_backwards`, `comparison_led`, `story_led`.
- `opening.style` must be exactly one of:
  `question_first`, `misconception_first`, `example_first`, `visual_first`,
  `phenomenon_first`, `result_first`, `task_first`, `direct_first`,
  `roadmap_first`.
- `opening.roadmap_style` must be exactly one of:
  `task_line`, `question_chain`, `visual_tags`, `two_step`,
  `result_path`, `classic_outline`.
- For problem-solving lessons, the opening order keeps the题面 safety line:
  1. concise spoken题目复述 via `problem_intake.restatement`
  2. visual marking of 已知 / 要求 / 关键关系 on a compact题面 card
  3. the chosen opening beat via `opening.hook_line` when it helps
  4. roadmap or structure cue
- Do NOT invert this order. Do NOT lead with meta commentary such as
  “先别急着算” or a strategy slogan before the concise read-in.
- Every lesson still needs a roadmap, but the roadmap must explain how THIS
  lesson will proceed. It must not be empty motivation or vague slogans.
- Do NOT default to a numbered "我们将看懂三件事" outline.
  `classic_outline` is only one roadmap style, not the default.
- The first section must follow the selected architecture. It may motivate
  through a question, a concrete example, a visual reveal, a result preview, a
  task read-in, or a direct first explanation.
- For a concrete exercise, proof, calculation, geometry problem, or image-based
  problem, the first section must be a "题面导入" beat: restate/analyze the
  problem in student language, identify 已知条件 / 目标问题 / 关键变量或图形关系,
  and plan sequential circles, boxes, underlines, arrows, or color highlights on
  the key information before any derivation starts.
- Do NOT write generic section titles like "定义", "性质", "应用" unless they are made specific.
- Each section must have a clear teacher intention, not just a concept label.
- Each section must include a real teacher move, such as: 提问, 对比, 预测, 纠错, 拆解, 回扣, 总结.
- Prefer concrete examples, causal reasoning, misconception correction, and natural transitions.
- The plan should feel spoken and classroom-like, not like a chapter outline.
- At least 2 sections should include a student-facing prediction or check question.
- At least 1 misconception should be corrected inside the main lesson, not only listed abstractly.
- `visual_strategy` and `board_plan` must be specific enough that a code generator can turn them into a clean scene.
- When the lesson starts from a problem statement, the first section's
  `visual_strategy` and `board_plan` must explicitly say what will be circled,
  boxed, underlined, arrow-labeled, or color-highlighted on the problem text,
  diagram, formula, or reconstructed题面 card.
- Keep the lesson progression natural. For problem-solving lessons use:
  read-in -> marking -> hook -> intuition -> mechanism -> conclusion -> transfer.
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


def _extract_json_object(text: str) -> dict:
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


class _FirstJsonObjectStreamGuard:
    """Forward only the first complete top-level JSON object from a stream."""

    def __init__(self, downstream: LLMDeltaCallback | None = None) -> None:
        self.downstream = downstream
        self.accepted_text: str | None = None
        self._buffer: list[str] = []
        self._started = False
        self._completed = False
        self._depth = 0
        self._in_string = False
        self._escaped = False

    def __call__(self, delta: str) -> None:
        if self._completed:
            raise StreamTerminated(text_override=self.accepted_text)
        if not delta:
            return

        accepted_delta: list[str] = []
        for char in delta:
            if self._completed:
                break
            if not self._started:
                if char == "{":
                    self._started = True
                    self._depth = 1
                    self._buffer.append(char)
                    accepted_delta.append(char)
                continue

            self._buffer.append(char)
            accepted_delta.append(char)
            if self._in_string:
                if self._escaped:
                    self._escaped = False
                elif char == "\\":
                    self._escaped = True
                elif char == '"':
                    self._in_string = False
                continue

            if char == '"':
                self._in_string = True
            elif char == "{":
                self._depth += 1
            elif char == "}":
                self._depth -= 1
                if self._depth == 0:
                    self._completed = True
                    self.accepted_text = "".join(self._buffer)
                    break

        if accepted_delta and self.downstream is not None:
            self.downstream("".join(accepted_delta))
        if self._completed:
            raise StreamTerminated(text_override=self.accepted_text)


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


def _string_list(value: Any, *, max_items: int = 6) -> list[str]:
    if isinstance(value, list):
        items = [_text(item) for item in value]
    else:
        text = _text(value)
        items = [text] if text else []
    return [item for item in items if item][:max_items]


def _normalize_opening(opening: Any, hook_fallback: str) -> dict[str, str]:
    opening = opening if isinstance(opening, dict) else {}
    hook_line = _text(
        opening.get("hook_line"),
        hook_fallback or "先用一个具体画面、例子或直接句子把学生带进这节课。",
    )
    return {
        "architecture": _enum_choice(opening.get("architecture"), OPENING_ARCHITECTURES, "visual_reveal"),
        "style": _enum_choice(opening.get("style"), OPENING_STYLES, "visual_first"),
        "hook_line": hook_line,
        "roadmap_style": _enum_choice(
            opening.get("roadmap_style"),
            ROADMAP_STYLES,
            "task_line",
        ),
    }


def _normalize_problem_intake(problem_intake: Any) -> dict[str, Any]:
    problem_intake = problem_intake if isinstance(problem_intake, dict) else {}
    givens = _string_list(problem_intake.get("givens"))
    key_terms = _string_list(problem_intake.get("key_terms"))
    raw_is_problem_solving = problem_intake.get("is_problem_solving")
    is_problem_solving = raw_is_problem_solving if isinstance(raw_is_problem_solving, bool) else False
    return {
        "is_problem_solving": is_problem_solving,
        "restatement": _text(
            problem_intake.get("restatement"),
            "先用1到2句学生能听懂的话把题目和总任务读清楚，再进入正式分析。",
        ),
        "givens": givens or ["题目给出的已知条件和限制" if is_problem_solving else "学生已经知道或容易误解的前提"],
        "target": _text(
            problem_intake.get("target"),
            "明确这道题最终要求什么。" if is_problem_solving else "明确这段讲解要解决的核心疑问。",
        ),
        "key_terms": key_terms or (
            ["关键条件", "目标问题", "核心变量或图形关系"]
            if is_problem_solving
            else ["核心概念", "易混点", "观察入口"]
        ),
        "visual_marking_plan": _text(
            problem_intake.get("visual_marking_plan"),
            "开场先呈现简洁题面卡，依次用圆圈、描边框、下划线、箭头或颜色高亮标出已知条件、目标问题和关键变量，再进入 hook 和正式解答。"
            if is_problem_solving
            else "开场先呈现一个简洁主题卡，标出本节最关键的概念词和易混点，再进入解释。",
        ),
    }


def _normalize_misconceptions(items: Any) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
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


def _normalize_sections(items: Any) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    if not isinstance(items, list):
        items = []

    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        sections.append({
            "id": _text(item.get("id"), f"section_{idx}"),
            "title": _text(item.get("title"), f"第{idx}步"),
            "teacher_goal": _text(item.get("teacher_goal"), "让学生在这一段真正看懂当前关键点。"),
            "teacher_move": _text(item.get("teacher_move"), "选择最合适的动作：可以直接演示、举例、对比或提问，再把结论落到画面上。"),
            "student_question": _text(item.get("student_question"), "学生此刻最需要抓住的关注点是什么？"),
            "why_this_step_now": _text(item.get("why_this_step_now"), "这一步承接上一步的重点，继续推进理解。"),
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


def _normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    closing_raw = plan.get("closing") if isinstance(plan.get("closing"), dict) else {}
    hook = _text(plan.get("hook"), "先用最适合本课的开场动作把学生带入。")
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
        "problem_intake": _normalize_problem_intake(plan.get("problem_intake")),
        "hook": _text(plan.get("hook"), opening["hook_line"]),
        "opening": opening,
        "big_idea": _text(plan.get("big_idea"), "把现象、图像和结论连成一条因果线。"),
        "teacher_voice": _text(
            plan.get("teacher_voice"),
            "像经验丰富的老师，按内容选择提问、举例、演示或直接讲解的节奏。",
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
            "title": "题面导入",
            "teacher_goal": "先用精炼读题把任务讲清楚，再圈出解题入口，最后进入选定的开场节奏。",
            "teacher_move": "先用1到2句复述题目，再逐个标出已知条件、目标和关键变量，随后用问题、例子或直接说明推进。",
            "student_question": "这到底在讲什么，为什么会这样？",
            "why_this_step_now": "学生先得知道自己为什么要继续看下去。",
            "expected_student_reaction": "先看清题目给了什么、问什么，再愿意跟着老师继续往下看。",
            "concrete_example": "从题面中的关键条件、目标问题或图形关系切入。",
            "visual_strategy": "先呈现简洁题面卡，再用圆圈、描边框、下划线、箭头或颜色高亮依次标出重点信息，之后才进入 hook 和路线说明。",
            "board_plan": "左侧或上方放题面卡，旁边整理“已知 / 要求 / 关键关系”，标注完成后再进入 hook 与解题路径。",
            "narration_goal": "像老师在黑板前先用1到2句读清题意、再划重点，最后提出为什么这样解。",
            "key_takeaway": "先读懂题目和解题入口，再进入正式推理。",
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
        image_path: Path | None = None,
        *,
        on_delta: LLMDeltaCallback | None = None,
        on_event: LLMEventCallback | None = None,
    ) -> dict:
        content = [{"type": "input_text", "text": request_text}]
        if image_path and image_path.exists():
            content.append({
                "type": "input_image",
                "image_url": _image_to_data_url(image_path),
            })

        system_prompt = _SYSTEM_PLAN

        for attempt in range(3):
            try:
                json_stream_guard = _FirstJsonObjectStreamGuard(on_delta)
                try:
                    if on_event is None:
                        text = self.client.generate_text(
                            system_prompt,
                            content,
                            max_retries=1,
                            on_delta=json_stream_guard,
                        )
                    else:
                        text = self.client.generate_text(
                            system_prompt,
                            content,
                            max_retries=1,
                            on_delta=json_stream_guard,
                            on_event=on_event,
                        )
                except StreamTerminated as exc:
                    text = exc.text_override or json_stream_guard.accepted_text or ""
                if json_stream_guard.accepted_text:
                    text = json_stream_guard.accepted_text
                return _normalize_plan(_extract_json_object(text))
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))

        raise RuntimeError("Teaching plan generation failed")
