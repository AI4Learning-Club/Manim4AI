from __future__ import annotations

from .schema import ThemePack
from .themes import (
    CHARCOAL_BOARD,
    DEEP_SPACE_BOARD,
    MIST_BLUE_FOCUS,
    SOFT_GLASS_WHITE,
)


THEMES: dict[str, ThemePack] = {
    theme.theme_id: theme
    for theme in (
        MIST_BLUE_FOCUS,
        CHARCOAL_BOARD,
        SOFT_GLASS_WHITE,
        DEEP_SPACE_BOARD,
    )
}

DEFAULT_THEME_ID = "charcoal_board"
DEFAULT_THEME = THEMES[DEFAULT_THEME_ID]


def get_theme(theme_id: str | None = None) -> ThemePack:
    if not theme_id:
        return DEFAULT_THEME
    return THEMES.get(theme_id, DEFAULT_THEME)
