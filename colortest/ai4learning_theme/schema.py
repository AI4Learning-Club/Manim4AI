from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ThemeBackground:
    asset_path: Path | None = None
    background_type: str = "image"
    tint_color: str | None = None
    tint_opacity: float = 0.0


@dataclass
class ThemeBehavior:
    recommended_opening_style: tuple[str, ...] = ()
    recommended_scene_density: str = "medium"
    preferred_topics: tuple[str, ...] = ()


@dataclass
class ThemePack:
    theme_id: str
    display_name: str
    brightness: str
    background: ThemeBackground
    tokens: dict[str, Any]
    behavior: ThemeBehavior = field(default_factory=ThemeBehavior)
    notes: str = ""

    def token(self, name: str, fallback: Any = None) -> Any:
        return self.tokens.get(name, fallback)
