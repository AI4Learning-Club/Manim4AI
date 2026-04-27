"""Select a stable theme pack for the current lesson."""

from __future__ import annotations

import re
from typing import Any

from plugins.manim.colortest.ai4learning_theme import THEMES, get_theme


FALLBACK_THEME_ID = "soft_glass_white"

_PLAN_FIELDS = (
    "lesson_goal",
    "student_profile",
    "teaching_promise",
    "hook",
    "big_idea",
    "teacher_voice",
)

_SECTION_FIELDS = (
    "title",
    "teacher_goal",
    "teacher_move",
    "student_question",
    "why_this_step_now",
    "expected_student_reaction",
    "concrete_example",
    "visual_strategy",
    "board_plan",
    "narration_goal",
    "key_takeaway",
    "check_for_understanding",
    "transition",
)

_TOPIC_SYNONYMS: dict[str, tuple[str, ...]] = {
    "physics": ("physics", "物理", "力学", "运动", "轨迹", "速度", "加速度", "电磁", "实验"),
    "function": ("function", "函数", "图像", "曲线", "坐标", "坐标系", "斜率"),
    "coordinate": ("coordinate", "坐标", "平面直角坐标系", "数轴", "网格", "曲线"),
    "proof": ("proof", "证明", "严谨", "命题", "推理"),
    "derivation": ("derivation", "推导", "公式变形", "一步一步", "化简"),
    "summary": ("summary", "总结", "归纳", "复盘", "结论页"),
    "algebra": ("algebra", "代数", "方程", "不等式", "数列", "恒等式"),
    "calculus": ("calculus", "微积分", "导数", "积分", "极限"),
    "general_math": ("math", "数学", "题目", "解题", "例题", "几何", "概率"),
    "concept": ("concept", "概念", "本质", "理解", "原理", "为什么"),
    "macro_view": ("macro", "宏观", "全局", "框架", "经济", "宏观经济"),
    "ai": ("ai", "人工智能", "机器学习", "神经网络", "大模型", "算法"),
}

_THEME_KEYWORDS: dict[str, dict[str, float]] = {
    "mist_blue_focus": {
        "物理": 3.0,
        "轨迹": 2.8,
        "坐标": 2.6,
        "坐标系": 3.0,
        "函数图像": 3.0,
        "图像": 1.8,
        "曲线": 2.0,
        "运动": 2.2,
        "速度": 1.8,
        "加速度": 1.8,
        "graph": 2.2,
        "function": 2.6,
        "physics": 2.8,
        "coordinate": 2.8,
    },
    "charcoal_board": {
        "证明": 3.2,
        "推导": 3.0,
        "推理": 2.4,
        "公式变形": 2.6,
        "步骤": 2.0,
        "一步一步": 2.4,
        "归纳": 2.0,
        "总结": 2.2,
        "复盘": 2.4,
        "黑板": 2.0,
        "derivation": 2.8,
        "proof": 3.0,
        "summary": 2.0,
    },
    "soft_glass_white": {
        "数学": 1.8,
        "代数": 2.6,
        "方程": 2.4,
        "不等式": 2.2,
        "数列": 2.2,
        "导数": 2.4,
        "积分": 2.4,
        "极限": 2.2,
        "例题": 2.0,
        "解题": 2.0,
        "algebra": 2.6,
        "calculus": 2.6,
        "general_math": 2.0,
    },
    "deep_space_board": {
        "概念": 2.6,
        "本质": 2.2,
        "原理": 2.4,
        "为什么": 1.8,
        "宏观": 3.2,
        "宏观经济": 3.6,
        "经济": 2.4,
        "人工智能": 3.0,
        "机器学习": 3.0,
        "神经网络": 3.0,
        "大模型": 3.0,
        "框架": 2.0,
        "全局": 2.0,
        "concept": 2.4,
        "macro": 3.0,
        "ai": 3.0,
    },
    "slate_mist": {
        "复习": 2.8,
        "复盘": 3.0,
        "梳理": 2.8,
        "整理": 2.4,
        "对比": 2.6,
        "比较": 2.4,
        "总览": 2.2,
        "结构": 2.2,
        "review": 2.8,
        "recap": 2.8,
        "comparison": 2.6,
        "compare": 2.2,
        "overview": 2.2,
        "structure": 2.2,
        "clarity": 1.8,
    },
}


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(_normalize_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_normalize_text(item) for item in value.values())
    text = str(value).strip().lower()
    return re.sub(r"\s+", " ", text)


def _collect_theme_source_text(request_text: str, teaching_plan: dict[str, Any]) -> str:
    parts = [_normalize_text(request_text)]

    for field in _PLAN_FIELDS:
        parts.append(_normalize_text(teaching_plan.get(field)))

    closing = teaching_plan.get("closing")
    if isinstance(closing, dict):
        parts.append(_normalize_text(closing))

    for section in teaching_plan.get("sections", []):
        if not isinstance(section, dict):
            continue
        for field in _SECTION_FIELDS:
            parts.append(_normalize_text(section.get(field)))

    return " ".join(part for part in parts if part)


def _score_theme(theme_id: str, source_text: str) -> tuple[float, list[str]]:
    theme = get_theme(theme_id)
    score = 0.0
    matched: list[str] = []

    for phrase in (
        theme_id,
        theme.display_name.lower(),
        theme.display_name.lower().replace(" ", "_"),
    ):
        if phrase and phrase in source_text:
            score += 8.0
            matched.append(phrase)

    for keyword, weight in _THEME_KEYWORDS.get(theme_id, {}).items():
        if keyword.lower() in source_text:
            score += weight
            matched.append(keyword)

    for topic in theme.behavior.preferred_topics:
        for synonym in _TOPIC_SYNONYMS.get(topic, (topic,)):
            if synonym.lower() in source_text:
                score += 1.8
                matched.append(synonym)
                break

    density = theme.behavior.recommended_scene_density
    if density in {"medium_low", "low_medium"} and any(
        marker in source_text for marker in ("总结", "归纳", "概念", "框架")
    ):
        score += 0.8
    if density == "medium" and any(
        marker in source_text for marker in ("例题", "解题", "函数", "坐标", "图像")
    ):
        score += 0.6

    return score, list(dict.fromkeys(matched))


def resolve_theme(request_text: str, teaching_plan: dict[str, Any]) -> dict[str, Any]:
    """Resolve a theme pack from the request and teaching plan."""
    source_text = _collect_theme_source_text(request_text, teaching_plan)
    scores: dict[str, float] = {}
    matches_by_theme: dict[str, list[str]] = {}

    for theme_id in THEMES:
        score, matched = _score_theme(theme_id, source_text)
        scores[theme_id] = score
        matches_by_theme[theme_id] = matched

    best_theme_id = max(scores, key=scores.get) if scores else FALLBACK_THEME_ID
    if scores.get(best_theme_id, 0.0) <= 0:
        best_theme_id = FALLBACK_THEME_ID

    theme = get_theme(best_theme_id)
    matched_keywords = matches_by_theme.get(best_theme_id, [])

    if matched_keywords:
        reason = (
            f"Matched lesson signals {matched_keywords[:6]} and aligned with "
            f"{theme.behavior.preferred_topics or ('general teaching',)}."
        )
    else:
        reason = (
            "No strong theme-specific keyword match was found, so the resolver "
            "fell back to the most general clean lesson theme."
        )

    return {
        "theme_id": theme.theme_id,
        "display_name": theme.display_name,
        "brightness": theme.brightness,
        "background_asset": str(theme.background.asset_path) if theme.background.asset_path else None,
        "notes": theme.notes,
        "recommended_opening_style": list(theme.behavior.recommended_opening_style),
        "recommended_scene_density": theme.behavior.recommended_scene_density,
        "preferred_topics": list(theme.behavior.preferred_topics),
        "matched_keywords": matched_keywords[:8],
        "scores": {theme_id: round(score, 2) for theme_id, score in scores.items()},
        "reason": reason,
    }
