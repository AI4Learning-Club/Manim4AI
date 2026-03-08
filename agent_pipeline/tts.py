"""
TTS module using edge-tts (Microsoft free TTS).

Generates narration audio from text, then merges with video using ffmpeg.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple


VOICE_ZH = "zh-CN-YunxiNeural"
VOICE_EN = "en-US-AriaNeural"


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


def generate_audio(
    text: str,
    output_path: Path,
    voice: str = VOICE_ZH,
    rate: str = "+0%",
) -> bool:
    """Synchronous wrapper for edge-tts audio generation."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(_generate_audio_async(text, output_path, voice, rate))
        return output_path.exists() and output_path.stat().st_size > 0
    except Exception as exc:
        print(f"  TTS error: {exc}")
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
        return result.returncode == 0 and output_path.exists()
    except Exception as exc:
        print(f"  ffmpeg error: {exc}")
        return False
