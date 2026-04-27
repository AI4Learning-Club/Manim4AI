"""
TTS module: Microsoft edge-tts or Doubao TTS, with offline macOS `say` fallback.

Generates narration audio from text, then merges with video using ffmpeg.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import List, Optional

from .output_language import normalize_output_language
from plugins.manim.runtime_config import get_manim_settings, get_manim_tts_global_cache_dir


_MANIM_SETTINGS = get_manim_settings()
VOICE_ZH = _MANIM_SETTINGS.tts_voice_zh
VOICE_EN = _MANIM_SETTINGS.tts_voice_en
LOCAL_VOICE_ZH = _MANIM_SETTINGS.tts_local_voice_zh
LOCAL_VOICE_EN = _MANIM_SETTINGS.tts_local_voice_en
SCENE_TTS_RATE = _MANIM_SETTINGS.tts_rate
_SCENE_TTS_CONFIG = {
    "voice": "",
    "rate": SCENE_TTS_RATE,
    "global_cache_dir": get_manim_tts_global_cache_dir(),
}

# Limit concurrent edge-tts WebSocket calls; too many parallel calls can yield
# "No audio was received" for zh voices (Microsoft endpoint flakiness).
_edge_tts_sem: threading.Semaphore | None = None
_edge_tts_sem_cap: int = -1
_EDGE_TTS_ATTEMPTS = 3
_EDGE_TTS_RETRY_BASE_S = 0.45
_DOUBAO_TTS_ATTEMPTS = 1
_DOUBAO_TTS_RETRY_BASE_S = 0.6


def _edge_tts_semaphore() -> threading.Semaphore:
    global _edge_tts_sem, _edge_tts_sem_cap
    cap = max(1, get_manim_settings().tts_edge_max_concurrency)
    if _edge_tts_sem is None or cap != _edge_tts_sem_cap:
        _edge_tts_sem = threading.Semaphore(cap)
        _edge_tts_sem_cap = cap
    return _edge_tts_sem


_doubao_tts_sem: threading.Semaphore | None = None
_doubao_tts_sem_cap: int = -1


def _doubao_tts_semaphore() -> threading.Semaphore:
    global _doubao_tts_sem, _doubao_tts_sem_cap
    cap = max(1, get_manim_settings().doubao_tts_max_concurrency)
    if _doubao_tts_sem is None or cap != _doubao_tts_sem_cap:
        _doubao_tts_sem = threading.Semaphore(cap)
        _doubao_tts_sem_cap = cap
    return _doubao_tts_sem


def _effective_voice_for_cache(voice: str) -> str:
    ms = get_manim_settings()
    if ms.tts_provider != "doubao":
        return voice
    v = (voice or "").strip()
    if v.startswith("zh") or v == VOICE_ZH or "zh-" in v.lower():
        return f"doubao:{ms.doubao_tts_speaker_zh}|{ms.doubao_tts_resource_id}"
    return f"doubao:{ms.doubao_tts_speaker_en}|{ms.doubao_tts_resource_id}"


def doubao_speaker_for_edge_voice(voice: str, text: str) -> str:
    ms = get_manim_settings()
    v = (voice or "").strip()
    if v.startswith("zh") or v == VOICE_ZH or "zh-" in v.lower():
        return str(ms.doubao_tts_speaker_zh).strip()
    if v.startswith("en") or v == VOICE_EN or "en-" in v.lower():
        return str(ms.doubao_tts_speaker_en).strip()
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return str(ms.doubao_tts_speaker_zh).strip()
    return str(ms.doubao_tts_speaker_en).strip()


def _edge_rate_to_doubao_speech_rate(rate: str) -> int:
    try:
        raw = str(rate).strip().rstrip("%")
        sign = 1
        if raw.startswith("-"):
            sign = -1
            raw = raw[1:]
        elif raw.startswith("+"):
            raw = raw[1:]
        pct = int(float(raw)) * sign
        return max(-50, min(100, pct))
    except ValueError:
        return 0

# region agent log
_DEBUG_LOG_PATH = (
    Path(__file__).resolve().parents[3] / ".cursor" / "debug-eb5b75.log"
)


def _agent_debug_ndjson(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict,
) -> None:
    payload = {
        "sessionId": "eb5b75",
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": {
            **data,
            "thread_id": threading.get_ident(),
        },
        "timestamp": int(time.time() * 1000),
    }
    try:
        _DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


# endregion


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_global_tts_cache_dir() -> Path:
    configured = _SCENE_TTS_CONFIG.get("global_cache_dir")
    if isinstance(configured, Path):
        return configured
    if configured:
        return Path(str(configured))
    return get_manim_tts_global_cache_dir()


def configure_scene_tts(
    *,
    voice: str | None = None,
    rate: str | None = None,
    global_cache_dir: str | Path | None = None,
) -> None:
    if voice is not None:
        _SCENE_TTS_CONFIG["voice"] = voice
    if rate is not None and str(rate).strip():
        _SCENE_TTS_CONFIG["rate"] = str(rate).strip()
    if global_cache_dir is not None:
        _SCENE_TTS_CONFIG["global_cache_dir"] = Path(global_cache_dir)


def get_scene_tts_voice() -> str:
    return str(_SCENE_TTS_CONFIG.get("voice", "")).strip()


def get_scene_tts_rate() -> str:
    rate = str(_SCENE_TTS_CONFIG.get("rate", SCENE_TTS_RATE)).strip()
    return rate or SCENE_TTS_RATE


def _scene_tts_text_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _scene_tts_voice_key(text: str, voice: str, rate: str = SCENE_TTS_RATE) -> str:
    return hashlib.md5(f"{voice}|{rate}|{text}".encode("utf-8")).hexdigest()


def scene_tts_round_cache_path(
    text: str,
    cache_dir: Path | None = None,
    *,
    voice: str | None = None,
    rate: str | None = None,
) -> Path:
    cache_root = cache_dir or Path.cwd() / "tts_cache"
    ms = get_manim_settings()
    if ms.tts_provider == "doubao":
        resolved_voice = (voice or get_scene_tts_voice() or VOICE_ZH).strip()
        resolved_rate = (rate or get_scene_tts_rate()).strip()
        key = _scene_tts_voice_key(text, _effective_voice_for_cache(resolved_voice), resolved_rate)
    else:
        key = _scene_tts_text_key(text)
    return cache_root / f"{key}.mp3"


def scene_tts_global_cache_path(
    text: str,
    voice: str,
    rate: str | None = None,
    cache_dir: Path | None = None,
) -> Path:
    resolved_rate = rate or get_scene_tts_rate()
    cache_root = cache_dir or get_global_tts_cache_dir()
    effective_voice = (
        _effective_voice_for_cache(voice) if get_manim_settings().tts_provider == "doubao" else voice
    )
    return cache_root / f"{_scene_tts_voice_key(text, effective_voice, resolved_rate)}.mp3"


def voice_for_language(output_language: str) -> str:
    return VOICE_ZH if normalize_output_language(output_language) == "zh" else VOICE_EN


async def _generate_audio_async(
    text: str,
    output_path: Path,
    voice: str = VOICE_ZH,
    rate: str = "+0%",
) -> None:
    """Generate a single audio file from text using edge-tts."""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(str(output_path))


def _supports_macos_say() -> bool:
    return shutil.which("say") is not None and shutil.which("ffmpeg") is not None


def _is_valid_audio_file(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    if not shutil.which("ffprobe"):
        return True
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=codec_name,duration",
        "-of",
        "default=noprint_wrappers=1",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode == 0 and "codec_name=" in (result.stdout or "")
    except Exception:
        return False


def has_audio_stream(path: Path) -> bool:
    if not path.exists() or not shutil.which("ffprobe"):
        return False
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "default=noprint_wrappers=1",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode == 0 and "codec_type=audio" in (result.stdout or "")
    except Exception:
        return False


def _local_voice_for(text: str, voice: str) -> str:
    if "zh" in voice.lower() or any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return LOCAL_VOICE_ZH
    return LOCAL_VOICE_EN


def _say_rate(rate: str) -> str:
    base_wpm = 185
    try:
        sign = 1
        value = rate.strip()
        if value.startswith("-"):
            sign = -1
        value = value.lstrip("+-").rstrip("%")
        percent = int(value or "0") * sign
    except ValueError:
        percent = 0
    return str(max(120, min(260, int(base_wpm * (1 + percent / 100)))))


def _generate_audio_with_say(
    text: str,
    output_path: Path,
    voice: str = VOICE_ZH,
    rate: str = "+0%",
) -> bool:
    if not _supports_macos_say():
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    local_voice = _local_voice_for(text, voice)
    say_rate = _say_rate(rate)

    with tempfile.TemporaryDirectory(prefix="codex_tts_") as tmp_dir:
        aiff_path = Path(tmp_dir) / "tts.aiff"
        say_cmd = [
            "say",
            "-v",
            local_voice,
            "-r",
            say_rate,
            "-o",
            str(aiff_path),
            text,
        ]
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(aiff_path),
            str(output_path),
        ]
        try:
            say_result = subprocess.run(
                say_cmd,
                capture_output=True,
                text=True,
                timeout=120,
                encoding="utf-8",
                errors="replace",
            )
            if say_result.returncode != 0 or not aiff_path.exists():
                return False
            ffmpeg_result = subprocess.run(
                ffmpeg_cmd,
                capture_output=True,
                text=True,
                timeout=120,
                encoding="utf-8",
                errors="replace",
            )
            return ffmpeg_result.returncode == 0 and _is_valid_audio_file(output_path)
        except Exception as exc:
            print(f"  Local TTS error: {exc}")
            return False


def generate_audio(
    text: str,
    output_path: Path,
    voice: str = VOICE_ZH,
    rate: str = "+0%",
) -> bool:
    """Generate audio with edge-tts or Doubao TTS, then fall back to local macOS `say`."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ms = get_manim_settings()
    # region agent log
    _agent_debug_ndjson(
        "H1",
        "tts.generate_audio:entry",
        "tts_attempt",
        {
            "provider": ms.tts_provider,
            "voice": voice,
            "rate": rate,
            "text_len": len(text),
            "text_sha8": hashlib.sha256(text.encode("utf-8")).hexdigest()[:8],
        },
    )
    # endregion

    if ms.tts_provider == "doubao":
        from plugins.manim.agent_pipeline.doubao_tts import synthesize_doubao_mp3

        last_exc: Exception | None = None
        speech_rate = max(
            -50,
            min(100, ms.doubao_tts_speech_rate + _edge_rate_to_doubao_speech_rate(rate)),
        )
        speaker = doubao_speaker_for_edge_voice(voice, text)
        for attempt in range(_DOUBAO_TTS_ATTEMPTS):
            try:
                with _doubao_tts_semaphore():
                    fd, tmp_name = tempfile.mkstemp(
                        suffix=".mp3",
                        prefix=".tts_doubao_",
                        dir=str(output_path.parent),
                    )
                    os.close(fd)
                    tmp_path = Path(tmp_name)
                    try:
                        synthesize_doubao_mp3(
                            text,
                            tmp_path,
                            app_id=str(ms.doubao_tts_app_id).strip(),
                            access_token=str(ms.doubao_tts_access_token).strip(),
                            resource_id=str(ms.doubao_tts_resource_id).strip(),
                            speaker=speaker,
                            audio_format=str(ms.doubao_tts_format or "mp3").strip(),
                            sample_rate=int(ms.doubao_tts_sample_rate),
                            speech_rate=speech_rate,
                        )
                        if _is_valid_audio_file(tmp_path):
                            os.replace(tmp_path, output_path)
                    finally:
                        if tmp_path.exists():
                            try:
                                tmp_path.unlink()
                            except OSError:
                                pass
                if _is_valid_audio_file(output_path):
                    if attempt > 0:
                        _agent_debug_ndjson(
                            "H1",
                            "tts.generate_audio:success_after_retry",
                            "doubao_ok",
                            {"attempt": attempt, "speaker": speaker},
                        )
                    return True
            except Exception as exc:
                last_exc = exc
                _agent_debug_ndjson(
                    "H2",
                    "tts.generate_audio:except",
                    "doubao_tts_exception",
                    {
                        "exc_type": type(exc).__name__,
                        "exc_str": str(exc)[:500],
                        "speaker": speaker,
                        "attempt": attempt,
                    },
                )
            if attempt + 1 < _DOUBAO_TTS_ATTEMPTS:
                time.sleep(_DOUBAO_TTS_RETRY_BASE_S * (1.4**attempt))

        if last_exc is not None:
            print(f"  doubao-tts error: {last_exc}")
        if _generate_audio_with_say(text, output_path, voice=voice, rate=rate):
            print("  Local TTS fallback succeeded via macOS say")
            return True
        return False

    last_exc: Exception | None = None
    for attempt in range(_EDGE_TTS_ATTEMPTS):
        try:
            with _edge_tts_semaphore():
                fd, tmp_name = tempfile.mkstemp(
                    suffix=".mp3",
                    prefix=".tts_edge_",
                    dir=str(output_path.parent),
                )
                os.close(fd)
                tmp_path = Path(tmp_name)
                try:
                    asyncio.run(_generate_audio_async(text, tmp_path, voice, rate))
                    if _is_valid_audio_file(tmp_path):
                        os.replace(tmp_path, output_path)
                finally:
                    if tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
            if _is_valid_audio_file(output_path):
                # region agent log
                if attempt > 0:
                    _agent_debug_ndjson(
                        "H1",
                        "tts.generate_audio:success_after_retry",
                        "edge_ok",
                        {"attempt": attempt, "voice": voice},
                    )
                # endregion
                return True
            # region agent log
            _agent_debug_ndjson(
                "H3",
                "tts.generate_audio:post_edge",
                "edge_saved_but_invalid",
                {
                    "voice": voice,
                    "rate": rate,
                    "path_exists": output_path.exists(),
                    "attempt": attempt,
                },
            )
            # endregion
        except Exception as exc:
            last_exc = exc
            # region agent log
            _agent_debug_ndjson(
                "H2",
                "tts.generate_audio:except",
                "edge_tts_exception",
                {
                    "exc_type": type(exc).__name__,
                    "exc_str": str(exc)[:500],
                    "voice": voice,
                    "rate": rate,
                    "attempt": attempt,
                },
            )
            # endregion
        if attempt + 1 < _EDGE_TTS_ATTEMPTS:
            time.sleep(_EDGE_TTS_RETRY_BASE_S * (1.4**attempt))

    if last_exc is not None:
        print(f"  edge-tts error: {last_exc}")
    if _generate_audio_with_say(text, output_path, voice=voice, rate=rate):
        print("  Local TTS fallback succeeded via macOS say")
        return True
    return False


def generate_narration(
    script: List[str],
    output_dir: Path,
    voice: str = VOICE_ZH,
    rate: str = "+5%",
) -> Optional[Path]:
    """Generate a single narration audio file from a list of paragraphs.

    Joins all paragraphs with pauses and generates one continuous audio.
    Returns the path to the audio file, or None on failure.
    """
    full_text = "。".join(p.strip().rstrip("。") for p in script if p.strip())
    if not full_text:
        return None

    audio_path = output_dir / "narration.mp3"
    ok = generate_audio(full_text, audio_path, voice=voice, rate=rate)
    return audio_path if ok else None


def merge_audio_video(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> bool:
    """Merge audio and video using ffmpeg.

    If audio is shorter than video, it ends naturally (no looping).
    If audio is longer than video, video determines the length.
    """
    if not shutil.which("ffmpeg"):
        print("  ffmpeg not found — skipping audio merge")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-shortest",
        "-map", "0:v:0",
        "-map", "1:a:0",
        str(output_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode == 0 and output_path.exists() and has_audio_stream(output_path)
    except Exception as exc:
        print(f"  ffmpeg error: {exc}")
        return False


def merge_narration_mp3_clips(ordered_paths: list[Path], output_path: Path) -> bool:
    """Concatenate MP3 clips in order (ffmpeg concat demuxer; copy codec when possible)."""
    paths = [p for p in ordered_paths if p.exists() and p.stat().st_size > 0]
    if not paths:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if len(paths) == 1:
        shutil.copy2(paths[0], output_path)
        return _is_valid_audio_file(output_path)
    if not shutil.which("ffmpeg"):
        return False
    concat_list = output_path.parent / "tts_merge_concat.txt"

    def _line(path: Path) -> str:
        normalized = path.resolve().as_posix().replace("'", r"'\''")
        return f"file '{normalized}'\n"

    concat_list.write_text("".join(_line(p) for p in paths), encoding="utf-8")
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        str(output_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        return result.returncode == 0 and _is_valid_audio_file(output_path)
    except Exception as exc:
        print(f"  ffmpeg merge narration error: {exc}")
        return False
