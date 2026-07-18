from __future__ import annotations

MANIM_RENDER_BACKEND = "manim"
SUPPORTED_RENDER_BACKENDS = (MANIM_RENDER_BACKEND,)


def normalize_render_backend(value: object = None) -> str:
    """Return the only enabled delivery backend.

    Hybrid/Remotion delivery is intentionally disabled. Keep validation in one
    place so CLI, MCP, queued jobs, and direct Python callers cannot bypass it.
    """
    backend = str(value or MANIM_RENDER_BACKEND).strip().lower() or MANIM_RENDER_BACKEND
    if backend not in SUPPORTED_RENDER_BACKENDS:
        raise ValueError(
            "render_backend must be 'manim'; Hybrid/Remotion delivery is disabled"
        )
    return backend
