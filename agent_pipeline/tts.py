"""
TTS module with online edge-tts and offline macOS `say` fallback.

Generates narration audio from text, then merges with video using ffmpeg.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional


VOICE_ZH = "zh-CN-YunxiNeural"
VOICE_EN = "en-US-AriaNeural"
LOCAL_VOICE_ZH = "Tingting"
LOCAL_VOICE_EN = "Samantha"


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
    """Generate audio with edge-tts, or fall back to local macOS `say`."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(_generate_audio_async(text, output_path, voice, rate))
        return _is_valid_audio_file(output_path)
    except Exception as exc:
        print(f"  edge-tts error: {exc}")
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
