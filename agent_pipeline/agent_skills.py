"""Project-local skill resources used by the Manim4AI agent system."""

from __future__ import annotations

from functools import cache
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


@cache
def _skill_text(*parts: str) -> str:
    return _read_text(SKILLS_DIR.joinpath(*parts))


def build_manim_skill_prompt(teaching_plan: dict | None = None) -> str:
    parts = [
        "## Local agent skill: Math-To-Manim",
        _skill_text("math-to-manim", "SKILL.md"),
        _skill_text("math-to-manim", "references", "routing.md"),
    ]
    if teaching_plan:
        routes = teaching_plan.get("hybrid_routes") if isinstance(teaching_plan.get("hybrid_routes"), dict) else {}
        manim_sections = routes.get("manim_section_ids") if isinstance(routes.get("manim_section_ids"), list) else []
        if manim_sections:
            parts.append(
                "## Hybrid routing note\n"
                "Only generate Manim content for these routed section ids:\n- "
                + "\n- ".join(str(section_id) for section_id in manim_sections)
            )
    return "\n\n".join(part for part in parts if part).strip()


def build_remotion_skill_prompt() -> str:
    return "\n\n".join(
        [
            "## Local agent skill: Remotion",
            _skill_text("remotion", "SKILL.md"),
            _skill_text("remotion", "references", "hybrid.md"),
        ]
    ).strip()
