"""Static export helpers for the explanation-style Manim template library."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .fast_paths import build_all_fast_path_templates


def _strip_markdown_fence(code: str) -> str:
    lines = [line.rstrip() for line in (code or "").splitlines()]
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    text = "\n".join(lines).strip()
    return text + ("\n" if text else "")


def export_static_template_library(
    output_dir: Path,
    *,
    theme_id: str = "mist_blue_focus",
) -> dict[str, Any]:
    """Export the generated template library to a static on-disk directory."""
    built = build_all_fast_path_templates(theme_id=theme_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    for meta, plan, code in built:
        category_id = str(meta["category_id"])
        category_dir = output_dir / category_id
        category_dir.mkdir(parents=True, exist_ok=True)

        template_source = _strip_markdown_fence(code)
        metadata = dict(meta)
        metadata["template_file"] = "template.py"
        metadata["teaching_plan_file"] = "teaching_plan.json"
        metadata["metadata_file"] = "metadata.json"
        metadata["template_line_count"] = len(template_source.splitlines())
        metadata["section_count"] = len(plan.get("sections", []))

        (category_dir / "template.py").write_text(template_source, encoding="utf-8")
        (category_dir / "teaching_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (category_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        entries.append(metadata)

    index = {
        "theme_id": theme_id,
        "category_count": len(entries),
        "categories": entries,
    }
    (output_dir / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    readme_lines = [
        "# Static Template Library",
        "",
        f"- theme_id: `{theme_id}`",
        f"- categories: `{len(entries)}`",
        "",
        "## Categories",
    ]
    for item in entries:
        readme_lines.append(
            f"- `{item['category_id']}` - {item['display_name']} ({item['style_axis']})"
        )
    (output_dir / "README.md").write_text("\n".join(readme_lines) + "\n", encoding="utf-8")

    return index

