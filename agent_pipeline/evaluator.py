"""
Wrapper around eval_pipeline for programmatic video evaluation.

Runs the three-layer pipeline (CV -> VLM -> Fusion) and returns
a structured dict matching the report.json schema.

Applies frame_step scaling: for short videos (< 30s at 60fps = 1800 frames)
uses a smaller step to avoid color/motion metric distortion from large gaps.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional

import cv2

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from eval_pipeline.config import CVConfig, FusionConfig, PipelineConfig, VLMConfig
from eval_pipeline.run import evaluate_video


def _auto_frame_step(video_path: Path, target_samples: int = 120) -> int:
    """Pick a frame_step that yields roughly *target_samples* frames.

    For short Manim clips (< 30s) this produces step=5..10 instead of 30,
    keeping color/motion metrics accurate while still fast.
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return 10
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    if total <= 0:
        return 10
    step = max(1, total // target_samples)
    return min(step, 30)


def collect_keyframes(eval_dir: Path) -> List[Path]:
    """Gather all keyframe images from the eval output directory."""
    keyframes: List[Path] = []
    payload = eval_dir / "vlm_payload" / "frames"
    if not payload.exists():
        # Try the stem-based subdir structure
        for subdir in eval_dir.iterdir():
            payload = subdir / "vlm_payload" / "frames"
            if payload.exists():
                break
    if payload.exists():
        for seg_dir in sorted(payload.iterdir()):
            if seg_dir.is_dir():
                for img in sorted(seg_dir.glob("*.jpg")):
                    keyframes.append(img)
    return keyframes


def evaluate(
    video_path: Path,
    output_dir: Path,
    *,
    api_key: str,
    base_url: str = "https://api.tabcode.cc/openai",
    model: str = "gpt-5.4",
    frame_step: Optional[int] = None,
    skip_vlm: bool = False,
) -> Dict:
    """
    Run the full evaluation pipeline on *video_path*.

    If *frame_step* is None, automatically selects a value that balances
    speed and metric accuracy based on video length.

    Returns the report dict (same schema as report.json).
    """

    if frame_step is None:
        frame_step = _auto_frame_step(video_path)

    cv_cfg = CVConfig(
        frame_step=frame_step,
        ocr_enabled=False,
    )

    vlm_cfg = VLMConfig(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )

    fusion_cfg = FusionConfig(
        w_overlap=0.30,
        w_rendering=0.15,
        w_layout=0.15,
        w_animation=0.15,
        w_color_consistency=0.0,
        w_vlm_semantic=0.25,
    )

    cfg = PipelineConfig(
        cv=cv_cfg,
        vlm=vlm_cfg,
        fusion=fusion_cfg,
        output_dir=output_dir,
        skip_vlm=skip_vlm,
    )

    evaluate_video(video_path, cfg)

    report_path = output_dir / video_path.stem / "report.json"
    if report_path.exists():
        return json.loads(report_path.read_text(encoding="utf-8"))

    return {
        "video": video_path.name,
        "overall_score": 0.0,
        "overall_passed": False,
        "dimensions": [],
        "issues": [],
    }
