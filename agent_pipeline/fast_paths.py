"""Catalog-driven template classification for explanation-style Manim lesson shapes."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from textwrap import indent
from typing import Any, Callable


@dataclass(frozen=True)
class SectionBlueprint:
    segment_id: str
    scene_name: str
    method_name: str
    title: str
    narrative: str
    visual_focus: str
    board_plan: list[str]


@dataclass(frozen=True)
class FastPathBlueprint:
    lesson_goal: str
    student_profile: str
    teaching_promise: str
    hook: str
    big_idea: str
    transfer_question: str
    after_class_prompt: str
    explanation_style: str
    explanation_depth: str
    pacing: str
    key_terms: list[str]
    givens: list[str]
    target: str
    visual_marking_plan: str
    misconceptions: list[dict[str, str]]
    sections: tuple[SectionBlueprint, ...]


@dataclass(frozen=True)
class FastPathCategory:
    category_id: str
    display_name: str
    style_axis: str
    template_id: str
    status: str
    description: str
    blueprint: FastPathBlueprint
    matcher: Callable[[str], bool] | None = None


def list_fast_path_categories() -> list[dict[str, str]]:
    """Return the explanation-style taxonomy exposed to deterministic template matching."""
    return [
        {
            "category_id": category.category_id,
            "display_name": category.display_name,
            "style_axis": category.style_axis,
            "template_id": category.template_id,
            "status": category.status,
            "description": category.description,
        }
        for category in _FAST_PATH_CATEGORIES
    ]


def get_fast_path_category_by_id(category_id: str) -> FastPathCategory | None:
    normalized = str(category_id or "").strip()
    if not normalized:
        return None
    for category in _FAST_PATH_CATEGORIES:
        if category.category_id == normalized:
            return category
    return None


def build_all_fast_path_templates(*, theme_id: str = "mist_blue_focus") -> list[tuple[dict[str, str], dict[str, Any], str]]:
    """Return every explanation-style category with its generated plan and reference template."""
    built: list[tuple[dict[str, str], dict[str, Any], str]] = []
    for category in _FAST_PATH_CATEGORIES:
        teaching_plan = _build_teaching_plan(category, theme_id=theme_id)
        template_code = _build_scene_pack_template(category, theme_id=theme_id)
        built.append(
            (
                {
                    "category_id": category.category_id,
                    "display_name": category.display_name,
                    "style_axis": category.style_axis,
                    "template_id": category.template_id,
                    "status": category.status,
                },
                teaching_plan,
                template_code,
            )
        )
    return built


def maybe_build_fast_path_plan_and_code(
    request_text: str,
    *,
    output_language: str,
    selected_theme: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str] | None:
    """Return a classified teaching plan plus a reference template for fusion rewrite."""
    if output_language != "zh":
        return None
    text = (request_text or "").strip()
    if not text:
        return None

    matched = _match_category(text)
    if matched is None:
        return None

    theme_id = "mist_blue_focus"
    if isinstance(selected_theme, dict):
        theme_id = str(selected_theme.get("theme_id") or theme_id).strip() or theme_id

    teaching_plan = _build_teaching_plan(matched, theme_id=theme_id)
    template_code = _build_scene_pack_template(matched, theme_id=theme_id)
    teaching_plan["fast_path"] = {
        "template_id": matched.template_id,
        "category_id": matched.category_id,
        "category_display_name": matched.display_name,
        "matched": True,
        "mode": "template_reference_fusion",
        "rewrite_required": True,
        "taxonomy_size": len(_FAST_PATH_CATEGORIES),
    }
    return teaching_plan, template_code


def build_fast_path_plan_and_code_for_category(
    category_id: str,
    *,
    selected_theme: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str] | None:
    matched = get_fast_path_category_by_id(category_id)
    if matched is None or matched.status != "ready":
        return None

    theme_id = "mist_blue_focus"
    if isinstance(selected_theme, dict):
        theme_id = str(selected_theme.get("theme_id") or theme_id).strip() or theme_id

    teaching_plan = _build_teaching_plan(matched, theme_id=theme_id)
    template_code = _build_scene_pack_template(matched, theme_id=theme_id)
    teaching_plan["fast_path"] = {
        "template_id": matched.template_id,
        "category_id": matched.category_id,
        "category_display_name": matched.display_name,
        "matched": True,
        "mode": "template_reference_fusion",
        "rewrite_required": True,
        "taxonomy_size": len(_FAST_PATH_CATEGORIES),
    }
    return teaching_plan, template_code


def _match_category(text: str) -> FastPathCategory | None:
    if should_skip_fast_path_for_request(text):
        return None
    for category in _FAST_PATH_CATEGORIES:
        if category.status != "ready":
            continue
        if category.matcher is None:
            continue
        if category.matcher(text):
            return category
    return None


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def should_skip_fast_path_for_request(text: str) -> bool:
    return _looks_like_concrete_lesson_request(text) and not _explicit_template_style_requested(text)


def _explicit_template_style_requested(text: str) -> bool:
    style_tokens = tuple(
        dict.fromkeys(
            tuple(category.display_name for category in _FAST_PATH_CATEGORIES)
            + tuple(category.style_axis for category in _FAST_PATH_CATEGORIES)
            + (
                "例题驱动",
                "直觉优先",
                "问题驱动",
                "题目驱动",
                "框架梳理",
                "问答式",
                "易错点优先",
                "定义优先",
                "应用优先",
                "公式优先",
            )
        )
    )
    if not _contains_any(text, style_tokens):
        return False

    normalized = re.sub(r"\s+", "", text.lower())
    style_pattern = "|".join(
        re.escape(re.sub(r"\s+", "", token.lower()))
        for token in sorted(style_tokens, key=len, reverse=True)
        if token
    )
    if not style_pattern:
        return False

    explicit_verbs = r"(?:按|按照|采用|使用|套用|选择|指定|切换到|改成|请用|用)"
    style_nouns = r"(?:讲解风格|讲解方式|讲法|风格|模板|模板库|结构模板|分镜模板)"
    return bool(
        re.search(rf"{explicit_verbs}.{{0,10}}(?:{style_pattern}).{{0,10}}(?:{style_nouns})", normalized)
        or re.search(rf"{explicit_verbs}.{{0,6}}(?:{style_pattern})", normalized)
        or re.search(rf"(?:{style_pattern}).{{0,6}}(?:{style_nouns})", normalized)
        or re.search(rf"{style_nouns}.{{0,8}}(?:{style_pattern})", normalized)
    )


def _looks_like_concrete_lesson_request(text: str) -> bool:
    return _contains_any(
        text,
        (
            "教学动画",
            "动画",
            "视频",
            "manim",
            "remotion",
            "主题是",
            "讲清楚",
            "讲解",
            "公式",
            "函数",
            "导数",
            "梯度",
            "曲线",
            "曲面",
            "几何",
            "物理",
            "化学",
            "生物",
            "数学",
        ),
    ) and _contains_any(
        text,
        (
            "开头",
            "分镜",
            "画面",
            "动态",
            "例子",
            "推导",
            "演示",
            "总结",
            "迁移",
        ),
    )


def _looks_like_brief_explainer(text: str) -> bool:
    return _contains_any(text, ("简略讲解", "简要讲解", "快速讲解", "简短讲解", "速览", "快速过一遍"))


def _looks_like_detailed_explainer(text: str) -> bool:
    return _contains_any(text, ("详细讲解", "细致讲解", "深入讲解", "完整讲解", "从头讲到尾"))


def _looks_like_step_by_step_explainer(text: str) -> bool:
    return _contains_any(text, ("一步一步", "逐步讲解", "分步骤讲", "步骤拆解", "step by step"))


def _looks_like_example_driven_explainer(text: str) -> bool:
    return _contains_any(text, ("举例讲解", "例题讲解", "例题驱动", "例子驱动讲解"))


def _looks_like_contrastive_explainer(text: str) -> bool:
    return _contains_any(text, ("对比讲解", "辨析", "区别", "易混点", "容易混淆", "比较一下"))


def _looks_like_intuition_first_explainer(text: str) -> bool:
    return _contains_any(text, ("直观讲解", "直觉讲解", "直觉优先", "形象讲解", "先建立直觉", "先别上公式"))


def _looks_like_formal_proof_explainer(text: str) -> bool:
    return _contains_any(text, ("严格证明", "证明讲解", "推导讲解", "严谨推导", "形式化"))


def _looks_like_review_recap_explainer(text: str) -> bool:
    return _contains_any(text, ("复习", "串讲", "考前", "总复习", "回顾一下"))


def _looks_like_beginner_onboarding_explainer(text: str) -> bool:
    return _contains_any(text, ("零基础讲解", "入门讲解", "新手入门", "完全不会", "从最基础开始"))


def _looks_like_advanced_deep_dive(text: str) -> bool:
    return _contains_any(text, ("高阶", "进阶", "深入一点", "更深一层", "深挖"))


def _looks_like_question_led_explainer(text: str) -> bool:
    return _contains_any(text, ("题目驱动", "从题目入手", "先做题", "题目来带", "问题驱动"))


def _looks_like_concept_map_explainer(text: str) -> bool:
    return _contains_any(text, ("知识框架", "知识地图", "结构梳理", "框架讲解", "脉络"))


def _looks_like_visual_story_explainer(text: str) -> bool:
    return _contains_any(text, ("故事化", "场景化", "可视化故事", "叙事讲解", "类比故事"))


def _looks_like_faq_explainer(text: str) -> bool:
    return _contains_any(text, ("常见问题", "faq", "问答式", "一问一答", "常见误区解答"))


def _looks_like_exam_oriented_explainer(text: str) -> bool:
    return _contains_any(text, ("应试", "考试重点", "拿分", "考点", "考试导向"))


def _make_sections(section_specs: list[tuple[str, str, str, str]]) -> tuple[SectionBlueprint, ...]:
    built: list[SectionBlueprint] = []
    for idx, (segment_id, title, narrative, visual_focus) in enumerate(section_specs, start=1):
        built.append(
            SectionBlueprint(
                segment_id=segment_id,
                scene_name=f"Segment{idx:02d}{''.join(part.capitalize() for part in segment_id.split('_'))}Scene",
                method_name=segment_id,
                title=title,
                narrative=narrative,
                visual_focus=visual_focus,
                board_plan=[title, visual_focus, "关键句", "迁移提醒"],
            )
        )
    return tuple(built)


def _make_blueprint(
    *,
    display_name: str,
    explanation_style: str,
    explanation_depth: str,
    pacing: str,
    hook: str,
    big_idea: str,
    section_specs: list[tuple[str, str, str, str]],
) -> FastPathBlueprint:
    return FastPathBlueprint(
        lesson_goal=f"用“{display_name}”的讲法，让学生迅速进入正确的理解节奏。",
        student_profile="面向知道一点点背景，但需要更合适讲法来真正听懂的学生。",
        teaching_promise=f"按照“{display_name}”的节奏组织内容，让讲解方式本身成为理解抓手。",
        hook=hook,
        big_idea=big_idea,
        transfer_question=f"如果换一道新题或新概念，你还会沿用“{display_name}”这套理解路径吗？",
        after_class_prompt=f"课后请回忆“{display_name}”最有效的两个讲解动作，并尝试迁移到别的主题。",
        explanation_style=explanation_style,
        explanation_depth=explanation_depth,
        pacing=pacing,
        key_terms=[display_name, explanation_style, explanation_depth],
        givens=[f"讲解风格：{display_name}", f"节奏：{pacing}"],
        target="让学生不仅听到内容，还能感受到为什么这种讲法更容易懂。",
        visual_marking_plan=f"开场先明确这是“{display_name}”风格，再用版式和节奏把这种讲法固定下来。",
        misconceptions=[
            {
                "mistake": "以为内容一换，讲法就完全失效。",
                "teacher_response": "先识别讲解目标，再选择对应讲法，风格本身可以跨主题复用。",
            }
        ],
        sections=_make_sections(section_specs),
    )


def _opening_shape_for_fast_path(category: FastPathCategory) -> tuple[str, str, str]:
    style = category.blueprint.explanation_style
    style_map = {
        "example": ("example_first", "example_led", "task_line"),
        "contrast": ("misconception_first", "comparison_led", "visual_tags"),
        "intuition": ("visual_first", "visual_reveal", "visual_tags"),
        "proof": ("direct_first", "direct_explanation", "two_step"),
        "review": ("roadmap_first", "direct_explanation", "classic_outline"),
        "onboarding": ("example_first", "example_led", "two_step"),
        "advanced": ("direct_first", "direct_explanation", "result_path"),
        "question-led": ("question_first", "question_led", "question_chain"),
        "structure": ("roadmap_first", "direct_explanation", "visual_tags"),
        "storytelling": ("visual_first", "story_led", "visual_tags"),
        "qa": ("question_first", "question_led", "question_chain"),
        "exam": ("task_first", "problem_walkthrough", "task_line"),
        "mistake-first": ("misconception_first", "comparison_led", "two_step"),
        "definition-first": ("direct_first", "direct_explanation", "two_step"),
        "application-first": ("example_first", "example_led", "task_line"),
        "formula-first": ("result_first", "result_backwards", "result_path"),
        "conceptual": ("visual_first", "visual_reveal", "visual_tags"),
        "operational": ("task_first", "problem_walkthrough", "task_line"),
        "memory": ("example_first", "example_led", "visual_tags"),
        "analogy": ("example_first", "example_led", "visual_tags"),
        "scenario": ("example_first", "story_led", "task_line"),
        "layered": ("roadmap_first", "direct_explanation", "two_step"),
        "micro-lecture": ("direct_first", "direct_explanation", "task_line"),
        "longform": ("roadmap_first", "direct_explanation", "classic_outline"),
        "table-comparison": ("misconception_first", "comparison_led", "visual_tags"),
        "timeline": ("visual_first", "visual_reveal", "visual_tags"),
        "causal": ("phenomenon_first", "visual_reveal", "two_step"),
        "pattern": ("example_first", "example_led", "result_path"),
        "interactive": ("question_first", "question_led", "question_chain"),
        "checkpoint": ("task_first", "problem_walkthrough", "task_line"),
        "mixed": ("visual_first", "visual_reveal", "visual_tags"),
    }
    return style_map.get(style, ("visual_first", "visual_reveal", "task_line"))


def _build_teaching_plan(category: FastPathCategory, *, theme_id: str) -> dict[str, Any]:
    blueprint = category.blueprint
    sections: list[dict[str, str]] = []
    for section in blueprint.sections:
        sections.append(
            {
                "id": section.segment_id,
                "title": section.title,
                "teacher_move": section.narrative,
                "visual_strategy": section.visual_focus,
                "key_takeaway": section.narrative,
                "check_for_understanding": f"看完“{section.title}”后，你能指出画面里哪个对象或变化最关键吗？",
            }
        )

    opening_style, opening_architecture, roadmap_style = _opening_shape_for_fast_path(category)

    return {
        "lesson_goal": blueprint.lesson_goal,
        "student_profile": blueprint.student_profile,
        "teaching_promise": blueprint.teaching_promise,
        "problem_intake": {
            "is_problem_solving": False,
            "restatement": f"这次讲解采用“{category.display_name}”风格来组织内容。",
            "givens": blueprint.givens,
            "target": blueprint.target,
            "key_terms": blueprint.key_terms,
            "visual_marking_plan": blueprint.visual_marking_plan,
        },
        "hook": blueprint.hook,
        "opening": {
            "architecture": opening_architecture,
            "style": opening_style,
            "hook_line": blueprint.hook,
            "roadmap_style": roadmap_style,
        },
        "big_idea": blueprint.big_idea,
        "teacher_voice": "亲切、清晰、强调讲解方式本身的节奏感。",
        "narrative_arc": [section.title for section in blueprint.sections[:3]],
        "misconceptions": blueprint.misconceptions,
        "sections": sections,
        "closing": {
            "summary": blueprint.big_idea,
            "transfer_question": blueprint.transfer_question,
            "after_class_prompt": blueprint.after_class_prompt,
        },
        "selected_assets": [],
        "selected_theme": {"theme_id": theme_id, "display_name": theme_id, "reason": "deterministic fast path"},
        "explanation_profile": {
            "style": blueprint.explanation_style,
            "depth": blueprint.explanation_depth,
            "pacing": blueprint.pacing,
        },
    }


def _py_str(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _manifest_entry(section: SectionBlueprint) -> str:
    return f'    {{"id": "{section.segment_id}", "scene": "{section.scene_name}", "method": "{section.method_name}"}},'


def _build_board_method(section: SectionBlueprint) -> str:
    lines = [
        f"def {section.method_name}(self):",
        f"    title = self.get_text({_py_str(section.title)}, font_size=28)",
        f"    narrative = self.get_text({_py_str(section.narrative)}, font_size=20)",
        f'    visual = self.get_secondary_text({_py_str("视觉重点：" + section.visual_focus)}, font_size=18)',
        "    bullets = VGroup(",
        "        *[",
        "            self.get_text(text, font_size=18)",
        "            for text in [",
    ]
    lines.extend([f"                {_py_str(item)}," for item in section.board_plan])
    lines.extend(
        [
            "            ]",
            "        ]",
            "    ).arrange(DOWN, buff=0.14, aligned_edge=LEFT)",
            "    card = VGroup(title, narrative, visual, bullets).arrange(DOWN, buff=0.18, aligned_edge=LEFT)",
            "    panel = self.make_panel(card, padding=0.18)",
            "    body1 = VGroup(panel)",
            "    self.fit_body(body1, max_width=11.6, center=ORIGIN)",
            "    self.add(body1)",
            "    self.wait(0.1)",
        ]
    )
    return "\n".join(lines)


def _build_opening_method(category: FastPathCategory) -> str:
    blueprint = category.blueprint
    return "\n".join(
        [
            "def opening_page(self):",
            f"    title = self.get_text({_py_str(category.display_name)}, font_size=30)",
            f"    hook = self.get_warning_text({_py_str(blueprint.hook)}, font_size=22)",
            "    roadmap = VGroup(",
            "        *[",
            "            self.get_secondary_text(text, font_size=18)",
            "            for text in [",
            f"                {_py_str('1. 看到对象和目标')},",
            f"                {_py_str('2. 绑定图像与关系')},",
            f"                {_py_str('3. 走完一次关键过程')},",
            "            ]",
            "        ]",
            "    ).arrange(DOWN, buff=0.12, aligned_edge=LEFT)",
            "    body1 = VGroup(title, hook, roadmap).arrange(DOWN, buff=0.22, aligned_edge=LEFT)",
            "    panel = self.make_panel(body1, padding=0.2)",
            "    body2 = VGroup(panel)",
            "    self.fit_body(body2, max_width=11.6, center=ORIGIN)",
            "    self.add(body2)",
            "    self.wait(0.1)",
        ]
    )


def _build_closing_method(category: FastPathCategory) -> str:
    blueprint = category.blueprint
    return "\n".join(
        [
            "def closing_page(self):",
            f"    summary = self.get_text({_py_str(blueprint.big_idea)}, font_size=22)",
            f'    transfer = self.get_secondary_text({_py_str("迁移问题：" + blueprint.transfer_question)}, font_size=18)',
            f'    after_class = self.get_secondary_text({_py_str("课后练习：" + blueprint.after_class_prompt)}, font_size=18)',
            "    body1 = VGroup(summary, transfer, after_class).arrange(DOWN, buff=0.18, aligned_edge=LEFT)",
            "    panel = self.make_panel(body1, padding=0.18)",
            "    body2 = VGroup(panel)",
            "    self.fit_body(body2, max_width=11.6, center=ORIGIN)",
            "    self.add(body2)",
            "    self.wait(0.1)",
        ]
    )


def _build_scene_pack_template(category: FastPathCategory, *, theme_id: str) -> str:
    blueprint = category.blueprint
    section_methods = [_build_board_method(section) for section in blueprint.sections]
    wrapper_scenes = [
        "\n".join(
            [
                f"class {section.scene_name}(LessonBase):",
                "    def construct(self):",
                f"        self.{section.method_name}()",
            ]
        )
        for section in blueprint.sections
    ]
    manifest_entries = ['    {"id": "opening", "scene": "Segment00OpeningScene", "method": "opening_page"},']
    manifest_entries.extend(_manifest_entry(section) for section in blueprint.sections)
    manifest_entries.append(
        f'    {{"id": "closing", "scene": "Segment{len(blueprint.sections) + 1:02d}ClosingScene", "method": "closing_page"}},'
    )
    closing_wrapper = "\n".join(
        [
            f"class Segment{len(blueprint.sections) + 1:02d}ClosingScene(LessonBase):",
            "    def construct(self):",
            "        self.closing_page()",
        ]
    )
    lesson_base_methods = [_build_opening_method(category), *section_methods, _build_closing_method(category)]

    lines = [
        "```python",
        "from manim import *",
        "from plugins.manim.colortest.ai4learning_theme import AI4LearningBaseScene",
        "",
        "SCENE_MANIFEST = [",
        *manifest_entries,
        "]",
        "",
        "class LessonBase(AI4LearningBaseScene):",
        f'    theme_id = "{theme_id}"',
        "",
    ]
    for method in lesson_base_methods:
        lines.extend(indent(method, "    ").splitlines())
        lines.append("")
    lines.extend(
        [
            "class Segment00OpeningScene(LessonBase):",
            "    def construct(self):",
            "        self.opening_page()",
            "",
        ]
    )
    for wrapper in wrapper_scenes:
        lines.extend(wrapper.splitlines())
        lines.append("")
    lines.extend(closing_wrapper.splitlines())
    lines.append("```")
    return "\n".join(lines)


def _make_category(
    category_id: str,
    display_name: str,
    style_axis: str,
    description: str,
    *,
    matcher: Callable[[str], bool] | None = None,
    blueprint: FastPathBlueprint | None = None,
) -> FastPathCategory:
    return FastPathCategory(
        category_id=category_id,
        display_name=display_name,
        style_axis=style_axis,
        template_id=f"{category_id}_template_v1",
        status="ready",
        description=description,
        blueprint=blueprint or _make_blueprint(
            display_name=display_name,
            explanation_style=style_axis,
            explanation_depth="medium",
            pacing="steady",
            hook=f"如果内容没变，只是讲法换了，理解体验会差多少？",
            big_idea=f"{display_name}的核心不是多讲内容，而是用合适的讲法把理解路径变短。",
            section_specs=[
                (
                    "section_one_object_goal",
                    "对象和目标先入场",
                    "先把本节真正要理解的对象、起点和目标放到同一张图里。",
                    "对象标注、起始状态、目标箭头",
                ),
                (
                    "section_two_core_relation",
                    "核心关系绑到图像",
                    "把关键量之间的关系和画面对象一一对应起来。",
                    "图像-公式绑定、颜色高亮",
                ),
                (
                    "section_three_dynamic_walkthrough",
                    "走完一次关键过程",
                    "用一次完整动态演示串起状态变化和因果链。",
                    "步骤推进、状态更新、轨迹变化",
                ),
                (
                    "section_four_misconception_check",
                    "校正常见误解",
                    "把最容易混淆的方向、条件或边界并排对比。",
                    "正误对照、边界提醒、反馈回路",
                ),
                (
                    "section_five_transfer_summary",
                    "迁移成可复用方法",
                    "把本节具体画面压缩成学生能带走的通用步骤。",
                    "方法卡、迁移例子、收束句",
                ),
            ],
        ),
        matcher=matcher,
    )


_FAST_PATH_CATEGORIES: tuple[FastPathCategory, ...] = (
    _make_category(
        "brief_explainer",
        "简略讲解",
        "brevity",
        "短时长、低负担、快速建立整体印象的讲法。",
        matcher=_looks_like_brief_explainer,
        blueprint=_make_blueprint(
            display_name="简略讲解",
            explanation_style="high-level summary",
            explanation_depth="light",
            pacing="fast",
            hook="如果只剩几分钟，怎样讲才能让学生先抓住大意？",
            big_idea="简略讲解不是少讲，而是只保留最能支撑理解的骨架。",
            section_specs=[
                ("section_one_big_picture", "先给全景", "先把整件事的大轮廓抛出来。", "总览图、主线、结论先行"),
                ("section_two_key_terms", "只保留关键词", "只讲最关键的词和关系，不展开旁枝。", "关键词、箭头关系"),
                ("section_three_main_example", "给一个代表性例子", "用一个极小例子承接整体印象。", "一个例子、一条主线"),
                ("section_four_common_confusion", "顺手排一个误区", "快速指出最常见误解，避免学生带错印象离开。", "误区对比"),
                ("section_five_one_sentence", "一句话收口", "最后收成一句能带走的话。", "一句话总结"),
            ],
        ),
    ),
    _make_category(
        "detailed_explainer",
        "详细讲解",
        "depth",
        "展开充分、层层铺垫、强调来龙去脉的讲法。",
        matcher=_looks_like_detailed_explainer,
        blueprint=_make_blueprint(
            display_name="详细讲解",
            explanation_style="deep walkthrough",
            explanation_depth="high",
            pacing="slow",
            hook="为什么有些内容必须讲细，学生才不会只记住表面？",
            big_idea="详细讲解的价值在于把隐含台阶全部点亮，让学生不靠猜。",
            section_specs=[
                ("section_one_context", "补背景", "先把学生缺失的上下文补足。", "前置知识、边界条件"),
                ("section_two_define", "把定义讲透", "概念、条件、对象都逐一定清楚。", "定义卡、限制条件"),
                ("section_three_reasoning", "展示推理链", "不给跳步，把为什么成立一路说清。", "推理链、阶段节点"),
                ("section_four_edge_cases", "补边界与反例", "专门处理细节、反例、易错点。", "反例、边界情况"),
                ("section_five_reconstruct", "让学生重建一遍", "收尾时让学生能自己再说一遍。", "重构图、复述框架"),
            ],
        ),
    ),
    _make_category("step_by_step_explainer", "逐步讲解", "procedure", "一步一步推进、强步骤感的讲法。", matcher=_looks_like_step_by_step_explainer),
    _make_category("example_driven_explainer", "例题驱动讲解", "example", "通过具体例子带出规则的讲法。", matcher=_looks_like_example_driven_explainer),
    _make_category("contrastive_explainer", "对比辨析讲解", "contrast", "通过比较、��分、辨析来建立理解。", matcher=_looks_like_contrastive_explainer),
    _make_category("intuition_first_explainer", "直觉优先讲解", "intuition", "先建立画面感和直觉，再上公式。", matcher=_looks_like_intuition_first_explainer),
    _make_category("formal_proof_explainer", "严格推导讲解", "proof", "强调证明、推导与逻辑完备性。", matcher=_looks_like_formal_proof_explainer),
    _make_category("review_recap_explainer", "复习串讲", "review", "面向回顾、整理、压缩记忆。", matcher=_looks_like_review_recap_explainer),
    _make_category("beginner_onboarding_explainer", "零基础入门讲解", "onboarding", "默认学生几乎没有前置知识。", matcher=_looks_like_beginner_onboarding_explainer),
    _make_category("advanced_deep_dive_explainer", "进阶深挖讲解", "advanced", "在已有基础上继续深入。", matcher=_looks_like_advanced_deep_dive),
    _make_category("question_led_explainer", "题目驱动讲解", "question-led", "从题目、问题或任务切入。", matcher=_looks_like_question_led_explainer),
    _make_category("concept_map_explainer", "框架梳理讲解", "structure", "强调知识地图和结构脉络。", matcher=_looks_like_concept_map_explainer),
    _make_category("visual_story_explainer", "故事化讲解", "storytelling", "通过故事、场景、叙事带内容。", matcher=_looks_like_visual_story_explainer),
    _make_category("faq_explainer", "问答式讲解", "qa", "围绕一连串问题来推进讲解。", matcher=_looks_like_faq_explainer),
    _make_category("exam_oriented_explainer", "应试导向讲解", "exam", "以考点、得分点、应试效率为导向。", matcher=_looks_like_exam_oriented_explainer),
    _make_category("mistake_first_explainer", "易错点优先讲解", "mistake-first", "先讲学生最容易错的地方，再讲正解。"),
    _make_category("definition_first_explainer", "定义优先讲解", "definition-first", "从定义、对象、边界先切入。"),
    _make_category("application_first_explainer", "应用优先讲解", "application-first", "先给应用场景，再回到原理。"),
    _make_category("formula_first_explainer", "公式优先讲解", "formula-first", "先给公式框架，再解释每个部分。"),
    _make_category("conceptual_explainer", "概念型讲解", "conceptual", "强调理解概念之间的关系。"),
    _make_category("operational_explainer", "操作型讲解", "operational", "强调怎么做、怎么操作、怎么执行。"),
    _make_category("mnemonic_explainer", "记忆法讲解", "memory", "强调口诀、压缩记忆与快速提取。"),
    _make_category("visual_analogy_explainer", "类比型讲解", "analogy", "用类比物帮助建立理解。"),
    _make_category("scenario_explainer", "场景化讲解", "scenario", "把知识放进具体场景里讲。"),
    _make_category("layered_explainer", "分层递进讲解", "layered", "由浅入深分层推进。"),
    _make_category("micro_lecture_explainer", "微课式讲解", "micro-lecture", "短时强节奏、信息密度高。"),
    _make_category("longform_lecture_explainer", "长课式讲解", "longform", "完整长时段展开。"),
    _make_category("comparison_table_explainer", "表格式对照讲解", "table-comparison", "用对照表格压缩差异信息。"),
    _make_category("timeline_explainer", "时间线讲解", "timeline", "沿时间顺序或演化过程推进。"),
    _make_category("cause_effect_explainer", "因果链讲解", "causal", "强调因果关系和触发链。"),
    _make_category("rule_pattern_explainer", "规律归纳讲解", "pattern", "从若干例子归纳出规律。"),
    _make_category("interactive_prompt_explainer", "引导互动式讲解", "interactive", "不断��问题、引导学生参与。"),
    _make_category("checkpoint_explainer", "检查点式讲解", "checkpoint", "每一小段都停下来做确认。"),
    _make_category("mixed_mode_explainer", "混合模式讲解", "mixed", "综合多种讲法，按段切换。"),
)
