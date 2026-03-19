"""
Wrapper around the new eval_pipeline for programmatic video evaluation.

Runs the full structured evaluation stack and returns the new report.json
schema produced by eval_pipeline.

Uses a fixed frame sampling rate by default: process every 30th frame.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from eval_pipeline.config import AudioConfig, CVConfig, ExternalMeta, PipelineConfig, VLMConfig
from eval_pipeline.run import evaluate_video

DEFAULT_FRAME_STEP = 30


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
    skip_audio: bool = False,
    ocr_enabled: bool = True,
    meta: Optional[Dict] = None,
    topic: Optional[str] = None,
) -> Dict:
    """
    Run the full evaluation pipeline on *video_path*.

    If *frame_step* is None, uses the fixed default sampling rate.

    Returns the new report dict written by eval_pipeline/report.json.
    """

    if frame_step is None:
        frame_step = DEFAULT_FRAME_STEP

    cv_cfg = CVConfig(
        frame_step=frame_step,
        ocr_enabled=ocr_enabled,
    )

    vlm_cfg = VLMConfig(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )

    cfg = PipelineConfig(
        cv=cv_cfg,
        audio=AudioConfig(),
        vlm=vlm_cfg,
        output_dir=output_dir,
        skip_vlm=skip_vlm,
        skip_audio=skip_audio,
    )
    if meta:
        cfg.meta = ExternalMeta(
            render_at_1=meta.get("render_at_1"),
            render_at_final=meta.get("render_at_final"),
            token_usage_mean=meta.get("token_usage_mean"),
            token_usage_std=meta.get("token_usage_std"),
            token_cost_usd=meta.get("token_cost_usd"),
            time_total_sec=meta.get("time_total_sec"),
            time_per_stage=meta.get("time_per_stage"),
            score_delta=meta.get("score_delta"),
            fix_rate=meta.get("fix_rate"),
            repair_rounds=meta.get("repair_rounds"),
            topic=meta.get("topic", "") or "",
        )
    if topic:
        cfg.meta.topic = topic

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
        "cv_fail_segments": [],
        "vlm_fail_segments": [],
    }
