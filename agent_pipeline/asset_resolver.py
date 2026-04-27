from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

from .llm import LLMClient, LLMConfig
from plugins.manim.runtime_config import get_manim_settings


ROOT_DIR = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT_DIR / "icon"
ICON_SUFFIXES = {".png", ".svg", ".jpg", ".jpeg", ".webp"}

_SYSTEM_SELECT_ASSETS = """\
You are selecting local icon files for an educational animation pipeline.

Your job is to choose useful icon filenames from the provided local icon
directory. The selected icons must help explain concrete real-world objects or
recognizable devices in the lesson. If icons would not clearly help, return an
empty list.

Selection rules:
- Only choose filenames from the provided local icon list.
- Prefer introduction, intuition, example, or application sections.
- Prefer real objects such as tools, devices, vehicles, animals, classroom items,
  money, scientific equipment, or everyday objects.
- Do NOT choose icons for abstract concepts, formulas, graphs, arrows, generic
  geometric shapes, or purely symbolic math ideas.
- Keep icon usage modest. Choose only the icons that clearly help teaching.
- Avoid clutter from selecting too many icons for one lesson.

Return ONLY valid JSON in this exact shape:
{
  "selected_assets": [
    {
      "filename": "calculator.png",
      "section_id": "section_1",
      "purpose": "在导入里展示真实物体",
      "why_helpful": "让学生先看到现实中的对象"
    }
  ]
}
"""


def _extract_json_object(text: str) -> Dict[str, Any]:
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

    left = text.find("{")
    right = text.rfind("}")
    if left >= 0 and right > left:
        try:
            data = json.loads(text[left : right + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return {"selected_assets": []}


def _summarize_sections(teaching_plan: Dict[str, Any]) -> List[Dict[str, str]]:
    sections = teaching_plan.get("sections", [])
    summarized: List[Dict[str, str]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        summarized.append(
            {
                "id": str(section.get("id", "")).strip(),
                "title": str(section.get("title", "")).strip(),
                "teacher_goal": str(section.get("teacher_goal", "")).strip(),
                "concrete_example": str(section.get("concrete_example", "")).strip(),
                "visual_strategy": str(section.get("visual_strategy", "")).strip(),
            }
        )
    return summarized


def list_local_icons(icon_dir: Path = ICON_DIR) -> List[str]:
    if not icon_dir.exists():
        return []
    return sorted(
        file.name
        for file in icon_dir.iterdir()
        if file.is_file() and file.suffix.lower() in ICON_SUFFIXES
    )


def _call_asset_selector(
    request_text: str,
    teaching_plan: Dict[str, Any],
    available_icons: List[str],
    *,
    api_key: str,
    base_url: str,
    model: str,
    max_retries: int = 2,
) -> Dict[str, Any]:
    if not api_key or not available_icons:
        return {"selected_assets": []}

    client = LLMClient(
        LLMConfig(
            stage="adhoc",
            model=model,
            api_key=api_key,
            base_url=base_url,
        ),
    )
    user_text = (
        "## Student request\n"
        f"{request_text}\n\n"
        "## Teaching plan section summary\n"
        f"{json.dumps(_summarize_sections(teaching_plan), ensure_ascii=False, indent=2)}\n\n"
        "## Available local icon filenames\n"
        f"{json.dumps(available_icons, ensure_ascii=False)}"
    )

    for attempt in range(max_retries):
        try:
            text = client.generate_text(
                _SYSTEM_SELECT_ASSETS,
                [{"type": "input_text", "text": user_text}],
                max_retries=1,
            )
            if text.strip():
                return _extract_json_object(text)
        except Exception:
            if attempt >= max_retries - 1:
                break
            time.sleep(2 * (attempt + 1))

    return {"selected_assets": []}


def _validate_selected_assets(
    raw_assets: Any,
    *,
    available_icons: List[str],
    teaching_plan: Dict[str, Any],
    icon_dir: Path,
) -> List[Dict[str, str]]:
    if not isinstance(raw_assets, list):
        return []

    section_titles = {
        str(section.get("id", "")).strip(): str(section.get("title", "")).strip()
        for section in teaching_plan.get("sections", [])
        if isinstance(section, dict)
    }
    available_set = set(available_icons)
    validated: List[Dict[str, str]] = []
    used_filenames: set[str] = set()

    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename", "")).strip()
        section_id = str(item.get("section_id", "")).strip()
        if filename not in available_set or section_id not in section_titles:
            continue
        if filename in used_filenames:
            continue

        validated.append(
            {
                "filename": filename,
                "section_id": section_id,
                "section_title": section_titles[section_id],
                "purpose": str(item.get("purpose", "")).strip() or "辅助展示真实物体",
                "why_helpful": str(item.get("why_helpful", "")).strip() or "帮助学生把抽象讲解和真实对象联系起来",
                "absolute_path": str((icon_dir / filename).resolve()),
            }
        )
        used_filenames.add(filename)

    return validated


def resolve_local_assets(
    request_text: str,
    teaching_plan: Dict[str, Any],
    *,
    llm_config: LLMConfig | None = None,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    icon_dir: Path = ICON_DIR,
) -> Dict[str, Any]:
    if llm_config is not None:
        api_key = llm_config.api_key
        base_url = llm_config.base_url
        model = llm_config.model
    available_icons = list_local_icons(icon_dir)
    raw = _call_asset_selector(
        request_text,
        teaching_plan,
        available_icons,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
    selected_assets = _validate_selected_assets(
        raw.get("selected_assets", []),
        available_icons=available_icons,
        teaching_plan=teaching_plan,
        icon_dir=icon_dir,
    )

    return {
        "enabled": get_manim_settings().use_local_icons,
        "icon_dir": str(icon_dir.resolve()),
        "available_icon_count": len(available_icons),
        "selected_assets": selected_assets,
    }
