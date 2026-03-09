"""
Layer 2 – VLM semantic judgment.

Sends suspicious segments (cv_fail / needs_vlm) to a Vision-Language Model
for semantic review.  Supports OpenAI-compatible APIs.

The VLM receives:
  - Segment metadata (time range, CV label, CV score)
  - 3 keyframe images (start / mid / end)
  - A structured prompt asking for PASS / FAIL / INTENTIONAL + reason

API key is injected at runtime via PipelineConfig.vlm.api_key or env var.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .config import VLMConfig
from .cv_features import SegmentFeatures


# =====================================================================
# Data container
# =====================================================================

@dataclass
class VLMVerdict:
    segment_id: str
    start_sec: float
    end_sec: float
    cv_label: str
    cv_score: float
    vlm_verdict: str        # PASS | FAIL | INTENTIONAL
    vlm_confidence: float   # 0-1
    vlm_reason: str
    raw_response: str = ""


# =====================================================================
# Prompt construction
# =====================================================================

SYSTEM_PROMPT = """\
You are a STRICT quality reviewer for Manim-rendered educational math/physics videos.
Your task is to judge whether a flagged video segment contains a genuine rendering bug.

CRITICAL — you must be STRICT about overlap.  When in doubt, verdict should be FAIL.

## FAIL conditions (any ONE means FAIL):
- Two or more FILLED shapes overlap visually (e.g. a red square covering part of
  a blue square).  Even in educational contexts this is a layout BUG.
- Text or formulas are partially covered by shapes or other text.
- Elements extend beyond the visible canvas (truncated/cut off).
- Garbled text, duplicated formula fragments (e.g. "y²y²"), broken LaTeX.

## INTENTIONAL conditions (ALL must be true):
- Elements are deliberately placed side-by-side (not overlapping).
- Text annotations are clearly readable and not occluded.
- The layout serves an obvious pedagogical purpose.
- No filled shapes overlap each other in area.

## PASS conditions:
- No overlap issues at all; clean layout.

IMPORTANT: For geometry demonstrations (Pythagorean theorem, area proofs, etc.),
auxiliary shapes (squares on triangle sides) MUST NOT overlap each other.
If they do, it is a BUG, not intentional design.

Return a JSON object with exactly these keys:
{
  "verdict": "PASS" | "FAIL" | "INTENTIONAL",
  "confidence": <float 0-1>,
  "reason": "<one-sentence explanation>",
  "severity": "none" | "minor" | "moderate" | "severe",
  "dimensions_affected": ["overlap", "rendering", "layout", "animation"]
}
"""


def _build_user_content(
    seg: SegmentFeatures,
    image_paths: List[Path],
    video_name: str = "",
) -> list:
    """Build the multi-modal user message content list."""
    content: list = []

    # Text metadata
    meta = (
        f"Video: {video_name}\n"
        f"Segment: {seg.segment_id}\n"
        f"Time: {seg.start_sec:.2f}s -> {seg.end_sec:.2f}s "
        f"(duration {seg.duration_sec:.2f}s)\n"
        f"CV label: {seg.label} (score={seg.score:.3f}, reason={seg.reason})\n"
        f"CV metrics:\n"
        f"  overlap_max={seg.overlap_max}, overlap_avg={seg.overlap_avg:.0f}\n"
        f"  occlusion_ratio={seg.occlusion_ratio:.2f}, "
        f"text_dominance={seg.text_dominance:.2f}\n"
        f"  motion_avg={seg.motion_avg:.0f}, active_ratio={seg.active_ratio:.2f}\n"
        f"  centroid_jitter={seg.centroid_jitter:.1f}\n"
        f"  layout_density_avg={seg.layout_max_density_avg:.2f}\n"
        f"  color_shift_max={seg.color_shift_max:.4f}\n"
        f"  ocr_artifact_frames={seg.ocr_artifact_frames}\n"
        f"  flash_events={seg.total_flash_events}\n"
        "\nJudge from these keyframes and CV metadata."
    )
    content.append({"type": "input_text", "text": meta})

    # Keyframe images
    for img_path in image_paths:
        if not img_path.exists():
            continue
        raw = img_path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        content.append({
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{b64}",
        })

    return content


def _parse_vlm_json(text: str) -> Dict:
    """Extract JSON object from model response (handles markdown fences)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    # Try whole text
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # Fallback: find first {...}
    left = text.find("{")
    right = text.rfind("}")
    if left >= 0 and right > left:
        try:
            obj = json.loads(text[left: right + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    # Last resort: return raw
    return {"verdict": "UNKNOWN", "confidence": 0.0, "reason": text}


# =====================================================================
# Main review function
# =====================================================================

def review_segments(
    segments: List[SegmentFeatures],
    frames_dir: Path,
    vlm_cfg: VLMConfig,
    video_name: str = "",
    progress_callback=None,
) -> List[VLMVerdict]:
    """
    Send each segment to the VLM and collect verdicts.

    *frames_dir* should contain sub-directories named by segment_id,
    each with start_*.jpg, mid_*.jpg, end_*.jpg keyframes.
    """

    # Resolve API key
    api_key = vlm_cfg.api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "No API key provided. Set VLMConfig.api_key or configure OPENAI_API_KEY in .env/env."
        )

    # Import OpenAI SDK
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "openai package not installed.  Run: pip install openai"
        ) from exc

    client_kwargs: Dict = {"api_key": api_key, "timeout": 120.0}
    if vlm_cfg.base_url:
        client_kwargs["base_url"] = vlm_cfg.base_url
    client = OpenAI(**client_kwargs)

    # Segments are pre-filtered by the caller; just apply max limit.
    to_review = list(segments)
    if vlm_cfg.max_segments > 0:
        to_review = to_review[: vlm_cfg.max_segments]

    verdicts: List[VLMVerdict] = []

    for i, seg in enumerate(to_review):
        # Collect keyframe images
        seg_dir = frames_dir / seg.segment_id
        images = sorted(seg_dir.glob("*.jpg")) if seg_dir.exists() else []

        user_content = _build_user_content(seg, images, video_name)

        # Prepend system prompt into user content
        full_content = [{"type": "input_text", "text": SYSTEM_PROMPT}] + user_content

        try:
            resp = client.responses.create(
                model=vlm_cfg.model,
                input=[{"role": "user", "content": full_content}],
                stream=True,
            )
            # Accumulate streamed text
            raw_text = ""
            for event in resp:
                if hasattr(event, "type") and event.type == "response.output_text.delta":
                    raw_text += event.delta
            raw_text = raw_text.strip()
        except Exception as exc:
            raw_text = f"API_ERROR: {exc}"

        parsed = _parse_vlm_json(raw_text)

        verdict = VLMVerdict(
            segment_id=seg.segment_id,
            start_sec=seg.start_sec,
            end_sec=seg.end_sec,
            cv_label=seg.label,
            cv_score=seg.score,
            vlm_verdict=str(parsed.get("verdict", "UNKNOWN")).upper(),
            vlm_confidence=float(parsed.get("confidence", 0.0)),
            vlm_reason=str(parsed.get("reason", "")),
            raw_response=raw_text,
        )
        verdicts.append(verdict)

        if progress_callback:
            progress_callback(i + 1, len(to_review))

    return verdicts


# =====================================================================
# I/O
# =====================================================================

def save_verdicts_jsonl(verdicts: List[VLMVerdict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for v in verdicts:
            row = {
                "segment_id": v.segment_id,
                "start_sec": round(v.start_sec, 3),
                "end_sec": round(v.end_sec, 3),
                "cv_label": v.cv_label,
                "cv_score": round(v.cv_score, 4),
                "vlm_verdict": v.vlm_verdict,
                "vlm_confidence": round(v.vlm_confidence, 3),
                "vlm_reason": v.vlm_reason,
                "raw_response": v.raw_response,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
