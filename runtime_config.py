from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse


BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config.settings import settings


MANIM_PLUGIN_ROOT = Path(__file__).resolve().parent


def get_manim_settings():
    return settings.manim


def get_manim_service_bind() -> tuple[str, int]:
    parsed = urlparse(settings.manim.service_url)
    host = parsed.hostname or "0.0.0.0"
    port = parsed.port or 8010
    return host, port


def get_manim_tts_global_cache_dir() -> Path:
    raw = settings.manim.tts_global_cache_dir.strip()
    if raw:
        return Path(raw)
    return MANIM_PLUGIN_ROOT / ".a4l_tts_cache"


def get_manim_runs_dir() -> Path:
    raw = settings.manim.runs_output_dir.strip()
    if raw:
        return Path(raw)
    return BACKEND_ROOT.parent / "runtime_data" / "manim" / "runs"
