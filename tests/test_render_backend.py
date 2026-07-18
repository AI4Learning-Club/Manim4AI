from __future__ import annotations

import pytest

try:
    from plugins.manim.agent_pipeline.agent_skills import build_remotion_skill_prompt
    from plugins.manim.agent_pipeline.render_backend import normalize_render_backend
except ModuleNotFoundError:  # Standalone submodule test invocation.
    from agent_pipeline.agent_skills import build_remotion_skill_prompt
    from agent_pipeline.render_backend import normalize_render_backend


@pytest.mark.parametrize("value", [None, "", "manim", " MANIM "])
def test_normalize_render_backend_accepts_only_manim(value: object) -> None:
    assert normalize_render_backend(value) == "manim"


@pytest.mark.parametrize("value", ["hybrid", "remotion", "unknown"])
def test_normalize_render_backend_rejects_disabled_delivery(value: object) -> None:
    with pytest.raises(ValueError, match="Hybrid/Remotion delivery is disabled"):
        normalize_render_backend(value)


def test_remotion_skill_prompt_is_disabled() -> None:
    with pytest.raises(RuntimeError, match="Remotion skill is disabled"):
        build_remotion_skill_prompt()
