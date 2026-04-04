"""
Layer 2 鈥?VLM semantic judgment.

Two VLM stages:
  2a. Segment visual review       - per-segment hard_bug / soft_layout_note
  2b. Task correctness review     鈥?whole-video Content Accuracy,
                                    Pedagogical Clarity, Engagement

Supports OpenAI-compatible APIs.
API key is injected at runtime via PipelineConfig.vlm.api_key or env var.
"""

from __future__ import annotations

import base64
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from typing import Dict, List, Optional  # noqa: F811 鈥?Optional used for video_path

from .config import VLMConfig
from .cv_features import SegmentFeatures
from .utils import make_openai_client


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


VLM_SEGMENT_MAX_WORKERS = max(1, _int_env("EVAL_VLM_SEGMENT_WORKERS", 3))


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
    vlm_verdict: str        # PASS | FAIL
    vlm_confidence: float   # 0-1
    vlm_reason: str
    vlm_issues: List[Dict[str, Any]] = field(default_factory=list)
    raw_response: str = ""


@dataclass
class WholeVideoMediaCache:
    encoded_keyframes: List[Dict[str, str]] = field(default_factory=list)
    encoded_video_input: Optional[Dict[str, str]] = None


def _video_mime_type(video_path: Path) -> str:
    suffix = video_path.suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
    }.get(suffix, "video/mp4")


def _encode_keyframes(keyframe_paths: Optional[List[Path]]) -> List[Dict[str, str]]:
    encoded: List[Dict[str, str]] = []
    for img_path in keyframe_paths or []:
        if not img_path.exists():
            continue
        raw = img_path.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        encoded.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"})
    return encoded


def _encode_video_input(video_path: Optional[Path]) -> Optional[Dict[str, str]]:
    if video_path is None or not video_path.exists():
        return None
    video_bytes = video_path.read_bytes()
    video_b64 = base64.b64encode(video_bytes).decode("ascii")
    mime = _video_mime_type(video_path)
    return {"type": "input_video", "video_url": f"data:{mime};base64,{video_b64}"}


def prepare_whole_video_media_cache(
    *,
    video_path: Optional[Path] = None,
    keyframe_paths: Optional[List[Path]] = None,
) -> WholeVideoMediaCache:
    """Encode whole-video media payloads once for reuse across Stage 2 reviews."""
    return WholeVideoMediaCache(
        encoded_keyframes=_encode_keyframes(keyframe_paths),
        encoded_video_input=_encode_video_input(video_path),
    )


def _clone_media_items(items: Optional[List[Dict[str, str]]]) -> List[Dict[str, str]]:
    return [dict(item) for item in (items or [])]


def _clone_media_item(item: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    return dict(item) if item else None


# =====================================================================
# Prompt construction
# =====================================================================

WHOLE_VIDEO_VISUAL_REVIEW_PROMPT = """\
You are a STRICT visual reviewer for Manim-rendered educational videos.
You will receive keyframes sampled evenly across the entire video.

Review the same keyframes on TWO independent dimensions:

1. overlap_review
Check EVERY keyframe for overlap / occlusion / rendering issues:
- Two or more filled shapes overlapping each other
- Text or formulas partially covered by shapes or other text
- Elements extending beyond the visible canvas (truncated / cut off)
- Garbled text, duplicated formula fragments, broken LaTeX

2. anchor_binding_review
Check EVERY keyframe for anchor-binding mistakes: annotations that do not
actually point to, cover, or stay attached to the thing they claim to reference.
Flag only clear visible mismatches such as:
- A label or numeric value sitting nearer to the wrong object / tick / point
- An arrow, brace, connector, or callout endpoint missing its intended target
- A highlighted interval, range box, underline, or bracket not matching the
  range / object set named in nearby text
- A stale overlay that stayed behind after a transform / layout shift and now
  explains the wrong object
- An explanatory note block whose referenced region is visually ambiguous or wrong

Do NOT flag:
- Legal staged reveals where the final binding becomes clear
- Pure style preferences if the target is still unambiguous
- Generic crowding unless it causes the binding itself to become wrong

Return JSON only with exactly this shape:
{
  "overlap_review": {
    "has_overlap": true | false,
    "overlap_frames": <int>,
    "total_frames": <int>,
    "overlap_ratio": <float 0-1>,
    "issues": [
      {
        "frame_index": <int>,
        "description": "<what's wrong>",
        "severity": "minor" | "moderate" | "severe"
      }
    ],
    "reason": "<one-sentence summary>"
  },
  "anchor_binding_review": {
    "has_binding_issue": true | false,
    "binding_issue_frames": <int>,
    "total_frames": <int>,
    "binding_issue_ratio": <float 0-1>,
    "issues": [
      {
        "frame_index": <int>,
        "taxonomy": "anchor_target_mismatch" | "range_span_mismatch" | "connector_endpoint_miss" | "detached_label" | "stale_overlay_after_layout",
        "description": "<what is visibly bound to the wrong thing>",
        "severity": "minor" | "moderate" | "severe"
      }
    ],
    "reason": "<one-sentence summary>"
  }
}
"""

SEGMENT_REVIEW_PROMPT = """\
You are a strict visual QA reviewer for Manim-rendered educational videos.
Your task is to review one flagged segment and return only actionable visual issues.

Important judging rules:
- A staged reveal is legal. If one panel, note block, or label appears first and
  another companion element appears later on the same stable page, do NOT flag
  that as a layout problem.
- Do NOT invent issues just because you would prefer a different composition.
- Report only issues that are clearly visible in the provided keyframes.

Use exactly these issue taxonomies:
- `hard_bug`
  Use this for clear rendering or readability bugs: text covered by shapes,
  garbled text, truncated objects, drifted transform targets, overlays covering
  labels, severe collisions, or other plainly broken visuals.
- `soft_layout_note`
  Use this only for mild polish issues that remain readable: slightly cramped
  layout, awkward arrow placement, tight spacing, or similar local refinements.

Verdict policy:
- Return `FAIL` if the segment contains one or more `hard_bug` issues.
- Return `PASS` if the segment has no hard bug. A segment with only
  `soft_layout_note` issues should still return `PASS`.

Return JSON only with exactly these keys:
{
  "verdict": "PASS" | "FAIL",
  "confidence": <float 0-1>,
  "reason": "<one-sentence explanation>",
  "issues": [
    {
      "taxonomy": "hard_bug" | "soft_layout_note",
      "severity": "low" | "medium" | "high",
      "description": "<specific visible problem>",
      "confidence": <float 0-1>
    }
  ]
}
"""


@dataclass
class OverlapReviewVerdict:
    has_overlap: bool
    overlap_frames: int
    total_frames: int
    overlap_ratio: float
    issues: List[Dict]
    reason: str
    raw_response: str = ""


@dataclass
class AnchorBindingVerdict:
    has_binding_issue: bool
    binding_issue_frames: int
    total_frames: int
    binding_issue_ratio: float
    issues: List[Dict]
    reason: str
    raw_response: str = ""


@dataclass
class WholeVideoVisualReviewResult:
    overlap_review: OverlapReviewVerdict
    anchor_binding_review: AnchorBindingVerdict
    raw_response: str = ""


def _normalize_overlap_issues(raw_issues: Any) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    if not isinstance(raw_issues, list):
        return issues

    allowed_severity = {"minor", "moderate", "severe"}
    for item in raw_issues:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description", "")).strip()
        if not description:
            continue
        severity = str(item.get("severity", "moderate")).strip().lower()
        if severity not in allowed_severity:
            severity = "moderate"
        try:
            frame_index = int(item.get("frame_index", -1))
        except (TypeError, ValueError):
            frame_index = -1
        issues.append(
            {
                "frame_index": frame_index,
                "description": description,
                "severity": severity,
            }
        )
    return issues


def _normalize_anchor_binding_issues(raw_issues: Any) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    if not isinstance(raw_issues, list):
        return issues

    allowed_taxonomies = {
        "anchor_target_mismatch",
        "range_span_mismatch",
        "connector_endpoint_miss",
        "detached_label",
        "stale_overlay_after_layout",
    }
    allowed_severity = {"minor", "moderate", "severe"}

    for item in raw_issues:
        if not isinstance(item, dict):
            continue
        taxonomy = str(item.get("taxonomy", "")).strip()
        if taxonomy not in allowed_taxonomies:
            continue
        description = str(item.get("description", "")).strip()
        if not description:
            continue
        severity = str(item.get("severity", "moderate")).strip().lower()
        if severity not in allowed_severity:
            severity = "moderate"
        try:
            frame_index = int(item.get("frame_index", -1))
        except (TypeError, ValueError):
            frame_index = -1
        issues.append(
            {
                "frame_index": frame_index,
                "taxonomy": taxonomy,
                "description": description,
                "severity": severity,
            }
        )
    return issues


def review_whole_video_visual_keyframes(
    keyframe_paths: List[Path],
    vlm_cfg: VLMConfig,
    video_name: str = "",
    *,
    encoded_keyframes: Optional[List[Dict[str, str]]] = None,
) -> WholeVideoVisualReviewResult:
    """Review whole-video keyframes once for both overlap and anchor-binding issues."""
    if not keyframe_paths:
        overlap = OverlapReviewVerdict(False, 0, 0, 0.0, [], "no keyframes", "")
        anchor = AnchorBindingVerdict(False, 0, 0, 0.0, [], "no keyframes", "")
        return WholeVideoVisualReviewResult(overlap_review=overlap, anchor_binding_review=anchor, raw_response="")

    api_key = vlm_cfg.api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("No API key.")

    client = make_openai_client(api_key=api_key, base_url=vlm_cfg.base_url, timeout=120.0)

    content: list = [{"type": "input_text", "text": WHOLE_VIDEO_VISUAL_REVIEW_PROMPT}]
    content.append({
        "type": "input_text",
        "text": (
            f"Video: {video_name}\n"
            f"Below are {len(keyframe_paths)} keyframes sampled evenly across the full video.\n"
            "Judge both overlap/rendering problems and anchor-binding mistakes on the same frames."
        ),
    })

    content.extend(_clone_media_items(encoded_keyframes or _encode_keyframes(keyframe_paths)))

    raw_text = _call_vlm(client, vlm_cfg, content)
    parsed = _parse_vlm_json(raw_text)

    overlap_raw = parsed.get("overlap_review", {})
    if not isinstance(overlap_raw, dict):
        overlap_raw = {}
    overlap_issues = _normalize_overlap_issues(overlap_raw.get("issues", []))
    try:
        overlap_frames = int(overlap_raw.get("overlap_frames", 0))
    except (TypeError, ValueError):
        overlap_frames = 0
    if overlap_frames <= 0 and overlap_issues:
        frame_indices = {
            int(issue.get("frame_index", -1))
            for issue in overlap_issues
            if int(issue.get("frame_index", -1)) >= 0
        }
        overlap_frames = len(frame_indices) if frame_indices else len(overlap_issues)
    try:
        overlap_total_frames = int(overlap_raw.get("total_frames", len(keyframe_paths)))
    except (TypeError, ValueError):
        overlap_total_frames = len(keyframe_paths)
    try:
        overlap_ratio = float(overlap_raw.get("overlap_ratio", 0.0))
    except (TypeError, ValueError):
        overlap_ratio = 0.0
    if overlap_ratio <= 0.0 and overlap_total_frames > 0 and overlap_frames > 0:
        overlap_ratio = overlap_frames / overlap_total_frames
    overlap_verdict = OverlapReviewVerdict(
        has_overlap=bool(overlap_raw.get("has_overlap", bool(overlap_issues))),
        overlap_frames=max(0, overlap_frames),
        total_frames=max(0, overlap_total_frames),
        overlap_ratio=max(0.0, min(1.0, overlap_ratio)),
        issues=overlap_issues,
        reason=str(overlap_raw.get("reason", "")),
        raw_response=raw_text,
    )

    anchor_raw = parsed.get("anchor_binding_review", {})
    if not isinstance(anchor_raw, dict):
        anchor_raw = {}
    issues = _normalize_anchor_binding_issues(anchor_raw.get("issues", []))

    try:
        binding_issue_frames = int(anchor_raw.get("binding_issue_frames", 0))
    except (TypeError, ValueError):
        binding_issue_frames = 0
    if binding_issue_frames <= 0 and issues:
        frame_indices = {
            int(issue.get("frame_index", -1))
            for issue in issues
            if int(issue.get("frame_index", -1)) >= 0
        }
        binding_issue_frames = len(frame_indices) if frame_indices else len(issues)

    try:
        total_frames = int(anchor_raw.get("total_frames", len(keyframe_paths)))
    except (TypeError, ValueError):
        total_frames = len(keyframe_paths)

    try:
        binding_issue_ratio = float(anchor_raw.get("binding_issue_ratio", 0.0))
    except (TypeError, ValueError):
        binding_issue_ratio = 0.0
    if binding_issue_ratio <= 0.0 and total_frames > 0 and binding_issue_frames > 0:
        binding_issue_ratio = binding_issue_frames / total_frames

    anchor_verdict = AnchorBindingVerdict(
        has_binding_issue=bool(anchor_raw.get("has_binding_issue", bool(issues))),
        binding_issue_frames=max(0, binding_issue_frames),
        total_frames=max(0, total_frames),
        binding_issue_ratio=max(0.0, min(1.0, binding_issue_ratio)),
        issues=issues,
        reason=str(anchor_raw.get("reason", "")),
        raw_response=raw_text,
    )
    return WholeVideoVisualReviewResult(
        overlap_review=overlap_verdict,
        anchor_binding_review=anchor_verdict,
        raw_response=raw_text,
    )


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
        "\nJudge from these keyframes and CV metadata.\n"
        "A normal staged reveal on a stable page is legal and should not be flagged."
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
    import re as _re
    text = text.strip()
    # Strip markdown code fences properly (```json ... ``` or ``` ... ```)
    fence_match = _re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, _re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
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


def _normalize_segment_issues(raw_issues: Any) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    if not isinstance(raw_issues, list):
        return issues

    for item in raw_issues:
        if not isinstance(item, dict):
            continue
        taxonomy = str(item.get("taxonomy", "")).strip()
        if taxonomy not in {"hard_bug", "soft_layout_note"}:
            continue
        severity = str(item.get("severity", "medium")).strip().lower()
        if severity not in {"low", "medium", "high"}:
            severity = "medium"
        description = str(item.get("description", "")).strip()
        if not description:
            continue
        try:
            confidence = float(item.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        confidence = max(0.0, min(1.0, confidence))
        issues.append(
            {
                "taxonomy": taxonomy,
                "severity": severity,
                "description": description,
                "confidence": confidence,
            }
        )
    return issues


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
    each with multiple chronological keyframes.
    """

    # Resolve API key
    api_key = vlm_cfg.api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "No API key provided. Set VLMConfig.api_key or configure OPENAI_API_KEY in .env/env."
        )

    # Segments are pre-filtered by the caller; just apply max limit.
    to_review = list(segments)
    if vlm_cfg.max_segments > 0:
        to_review = to_review[: vlm_cfg.max_segments]

    if not to_review:
        return []

    client_local = threading.local()

    def _get_segment_client():
        client = getattr(client_local, "client", None)
        if client is None:
            client = make_openai_client(
                api_key=api_key,
                base_url=vlm_cfg.base_url,
                timeout=120.0,
            )
            client_local.client = client
        return client

    def _review_one(index: int, seg: SegmentFeatures) -> tuple[int, VLMVerdict]:
        local_client = _get_segment_client()
        seg_dir = frames_dir / seg.segment_id
        images = sorted(seg_dir.glob("*.jpg")) if seg_dir.exists() else []
        user_content = _build_user_content(seg, images, video_name)
        full_content = [{"type": "input_text", "text": SEGMENT_REVIEW_PROMPT}] + user_content
        raw_text = _call_vlm(local_client, vlm_cfg, full_content)
        parsed = _parse_vlm_json(raw_text)
        normalized_issues = _normalize_segment_issues(parsed.get("issues", []))
        parsed_verdict = str(parsed.get("verdict", "")).strip().upper()
        has_hard_bug = any(
            issue["taxonomy"] == "hard_bug" for issue in normalized_issues
        )
        inferred_verdict = "FAIL" if has_hard_bug else "PASS"
        if parsed_verdict == "FAIL" and not has_hard_bug:
            # Preserve an explicit FAIL when the model reason is strong but the
            # issues array is missing or malformed. Fusion can still fall back
            # to a single hard_bug entry from the segment reason.
            inferred_verdict = "FAIL"
        try:
            parsed_confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            parsed_confidence = 0.0
        verdict = VLMVerdict(
            segment_id=seg.segment_id,
            start_sec=seg.start_sec,
            end_sec=seg.end_sec,
            cv_label=seg.label,
            cv_score=seg.score,
            vlm_verdict=inferred_verdict,
            vlm_confidence=max(0.0, min(1.0, parsed_confidence)),
            vlm_reason=str(parsed.get("reason", "")),
            vlm_issues=normalized_issues,
            raw_response=raw_text,
        )
        return index, verdict

    verdict_pairs: List[tuple[int, VLMVerdict]] = []
    max_workers = min(VLM_SEGMENT_MAX_WORKERS, len(to_review))

    if max_workers <= 1:
        for i, seg in enumerate(to_review):
            verdict_pairs.append(_review_one(i, seg))
            if progress_callback:
                progress_callback(i + 1, len(to_review))
    else:
        completed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(_review_one, i, seg): i
                for i, seg in enumerate(to_review)
            }
            for future in as_completed(future_map):
                verdict_pairs.append(future.result())
                completed += 1
                if progress_callback:
                    progress_callback(completed, len(to_review))

    verdict_pairs.sort(key=lambda item: item[0])
    return [verdict for _, verdict in verdict_pairs]


# =====================================================================
# AV Alignment MLLM review (Stage 2c)
# =====================================================================

AV_ALIGNMENT_PROMPT = """\
You are an expert reviewer evaluating the audio-visual alignment of a
Manim-rendered educational video.  The video has synthesised narration (TTS)
and animated visuals.  Watch/listen to the video carefully and rate it on
THREE aspects of audio-visual synchronisation.

## Aspects

1. **Semantic Alignment** (integer 1-5)
   Does the narration match what is being shown on screen at each moment?
   e.g. when the narrator says "consider this triangle", is a triangle visible?
   1 = completely mismatched, 3 = mostly aligned but some drift, 5 = perfect match

2. **Temporal Pacing** (integer 1-5)
   Are transitions, formula appearances, and scene changes well-synchronised
   with the speech rhythm?  Is there dead time where nothing happens while
   audio plays, or vice versa?
   1 = severe desync, 3 = acceptable, 5 = perfectly paced

3. **Narration Naturalness** (integer 1-5)
   Does the TTS audio sound natural and clear?  Are there pronunciation
   errors, unnatural pauses, or robotic artefacts?
   1 = unintelligible, 3 = acceptable TTS, 5 = natural and clear

Return a JSON object with exactly these keys:
{
  "semantic_alignment": <int 1-5>,
  "semantic_alignment_reason": "<one-sentence explanation>",
  "temporal_pacing": <int 1-5>,
  "temporal_pacing_reason": "<one-sentence explanation>",
  "narration_naturalness": <int 1-5>,
  "narration_naturalness_reason": "<one-sentence explanation>"
}
"""


@dataclass
class AVAlignmentVerdict:
    semantic_alignment: int           # 1-5
    semantic_alignment_reason: str
    temporal_pacing: int              # 1-5
    temporal_pacing_reason: str
    narration_naturalness: int        # 1-5
    narration_naturalness_reason: str
    raw_response: str = ""


def review_av_alignment(
    vlm_cfg: VLMConfig,
    video_name: str = "",
    video_path: Optional[Path] = None,
    keyframe_paths: Optional[List[Path]] = None,
    *,
    encoded_keyframes: Optional[List[Dict[str, str]]] = None,
    encoded_video_input: Optional[Dict[str, str]] = None,
) -> AVAlignmentVerdict:
    """
    Send video (with audio) to MLLM for semantic AV alignment review.

    Prefers direct video upload (MLLM can hear the audio).
    Falls back to keyframes if the API doesn't support video input,
    but keyframe mode cannot evaluate audio-side alignment.
    """
    if video_path is None and not keyframe_paths:
        raise ValueError("Provide video_path or keyframe_paths for AV alignment review.")

    api_key = vlm_cfg.api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("No API key for AV alignment VLM review.")

    client = make_openai_client(api_key=api_key, base_url=vlm_cfg.base_url, timeout=180.0)

    # Build content 鈥?prefer video (MLLM can hear audio)
    content: list = [{"type": "input_text", "text": AV_ALIGNMENT_PROMPT}]

    use_video = False
    video_input = _clone_media_item(encoded_video_input or _encode_video_input(video_path))
    if video_input is not None:
        content.append({
            "type": "input_text",
            "text": (
                f"Video: {video_name}\n\n"
                "Watch and listen to the full video below.  Pay close attention "
                "to whether the narration and the visuals are synchronised."
            ),
        })
        content.append(video_input)
        use_video = True

    if not use_video and keyframe_paths:
        content.append({
            "type": "input_text",
            "text": (
                f"Video: {video_name}\n\n"
                "Below are keyframes from the video.  Audio is not available in "
                "this mode 鈥?evaluate visual pacing and layout transitions only.\n"
                "For narration_naturalness, return 3 (neutral) since audio is not provided."
            ),
        })
        content.extend(_clone_media_items(encoded_keyframes or _encode_keyframes(keyframe_paths)))

    raw_text = _call_vlm(client, vlm_cfg, content)

    # Fallback to keyframes if video mode failed
    if use_video and raw_text.startswith("API_ERROR") and keyframe_paths:
        print("    Video input failed for AV alignment, falling back to keyframes ...")
        fb: list = [{"type": "input_text", "text": AV_ALIGNMENT_PROMPT}]
        fb.append({
            "type": "input_text",
            "text": (
                f"Video: {video_name}\n\n"
                "Below are keyframes from the video.  Audio is not available.\n"
                "For narration_naturalness, return 3 (neutral)."
            ),
        })
        fb.extend(_clone_media_items(encoded_keyframes or _encode_keyframes(keyframe_paths)))
        raw_text = _call_vlm(client, vlm_cfg, fb)

    parsed = _parse_vlm_json(raw_text)

    return AVAlignmentVerdict(
        semantic_alignment=max(1, min(5, int(parsed.get("semantic_alignment", 3)))),
        semantic_alignment_reason=str(parsed.get("semantic_alignment_reason", "")),
        temporal_pacing=max(1, min(5, int(parsed.get("temporal_pacing", 3)))),
        temporal_pacing_reason=str(parsed.get("temporal_pacing_reason", "")),
        narration_naturalness=max(1, min(5, int(parsed.get("narration_naturalness", 3)))),
        narration_naturalness_reason=str(parsed.get("narration_naturalness_reason", "")),
        raw_response=raw_text,
    )


# =====================================================================
# I/O
# =====================================================================

# =====================================================================
# Task Correctness VLM review (Stage 2b)
# =====================================================================

TASK_CORRECTNESS_PROMPT = """\
You are an expert evaluator for Manim-rendered educational math/science videos.

You will receive:
- The original topic / student question
- A structured teaching plan (sections with goals and key takeaways)
- The video itself (or keyframes sampled from it)

Evaluate on THREE dimensions.

## Dimensions

1. **Content Accuracy** (binary: true/false)
   Is the teaching content factually correct?
   - If a teaching plan is provided: are the concepts, formulas, diagrams, and
     explanations in the video consistent with the plan's goals and key takeaways?
   - If the topic is a student question / problem: is the solution / answer correct?
   Check: formulas, diagrams, labels, numerical values, logical reasoning.

2. **Pedagogical Clarity** (integer 0-100)
   How clear and logically coherent is the instructional presentation?
   Does the video follow the teaching plan's intended narrative arc?
   Consider whether the visuals are as simple as they can be while still
   teaching the point clearly. Unnecessary visual complexity, overly complete
   diagrams, duplicated heavy figures, or too many simultaneous details should
   lower this score.
   0 = incomprehensible, 40 = confusing, 60 = acceptable, 80 = clear, 100 = excellent

3. **Engagement** (integer 0-100)
   To what degree does the output engage viewers and sustain attention?
   Consider animation quality, pacing, visual appeal, narrative flow.
   Prefer dynamic clarity over visual busyness: a clean visual that reveals one
   idea at a time is stronger than a crowded, overly complex frame.
   0 = boring/static, 40 = dull, 60 = adequate, 80 = engaging, 100 = captivating

Return a JSON object with exactly these keys:
{
  "content_accuracy": true | false,
  "content_accuracy_reason": "<one-sentence explanation>",
  "pedagogical_clarity": <int 0-100>,
  "pedagogical_clarity_reason": "<one-sentence explanation>",
  "engagement": <int 0-100>,
  "engagement_reason": "<one-sentence explanation>"
}
"""


VISUAL_COVERAGE_PROMPT = """\
You are an expert evaluator for Manim-rendered educational videos.

You will receive:
- A structured teaching plan with numbered sections
- The video itself (or keyframes sampled across the full video)

Your task: check whether the video **covers every section** of the teaching plan.
For each section, determine if its key content appears in the video.

Return a JSON object with exactly these keys:
{
  "sections_covered": [
    {"section_id": "section_1", "covered": true|false, "evidence": "<brief note>"},
    ...
  ],
  "coverage_ratio": <float 0-1>,
  "missing_sections": ["section_3", ...],
  "coverage_reason": "<one-sentence summary>"
}
"""


@dataclass
class TaskCorrectnessVerdict:
    content_accuracy: bool
    content_accuracy_reason: str
    pedagogical_clarity: float     # 0-1 (normalised from 0-100)
    pedagogical_clarity_reason: str
    engagement: float              # 0-1 (normalised from 0-100)
    engagement_reason: str
    raw_response: str = ""


def review_task_correctness(
    topic: str,
    vlm_cfg: VLMConfig,
    video_name: str = "",
    video_path: Optional[Path] = None,
    keyframe_paths: Optional[List[Path]] = None,
    teaching_plan: Optional[Dict] = None,
    *,
    encoded_keyframes: Optional[List[Dict[str, str]]] = None,
    encoded_video_input: Optional[Dict[str, str]] = None,
) -> TaskCorrectnessVerdict:
    """
    Evaluate task correctness via VLM.

    *teaching_plan*: the structured teaching plan dict (sections, goals, etc.).
    Content Accuracy is checked against the plan if provided, or against
    the topic/question if it's a problem-solving scenario.
    """
    if video_path is None and not keyframe_paths:
        raise ValueError("Provide video_path or keyframe_paths for task correctness review.")

    api_key = vlm_cfg.api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("No API key for task correctness VLM review.")

    client = make_openai_client(api_key=api_key, base_url=vlm_cfg.base_url, timeout=180.0)

    # Build teaching plan summary for prompt
    plan_text = ""
    if teaching_plan:
        sections = teaching_plan.get("sections", [])
        plan_text = f"Lesson goal: {teaching_plan.get('lesson_goal', '')}\n"
        plan_text += f"Teaching promise: {teaching_plan.get('teaching_promise', '')}\n\n"
        plan_text += "Sections:\n"
        for sec in sections:
            plan_text += (
                f"  - {sec.get('id', '')}: {sec.get('title', '')}\n"
                f"    Goal: {sec.get('teacher_goal', '')}\n"
                f"    Key takeaway: {sec.get('key_takeaway', '')}\n"
            )

    context_block = f"Video: {video_name}\nTopic/Prompt: {topic}\n"
    if plan_text:
        context_block += f"\n--- Teaching Plan ---\n{plan_text}\n--- End Teaching Plan ---\n"

    # Build content
    content: list = [{"type": "input_text", "text": TASK_CORRECTNESS_PROMPT}]

    # Mode 1: Direct video
    use_video = False
    video_input = _clone_media_item(encoded_video_input or _encode_video_input(video_path))
    if video_input is not None:
        content.append({
            "type": "input_text",
            "text": context_block + "\nThe full video is attached below.",
        })
        content.append(video_input)
        use_video = True

    # Mode 2: Keyframe fallback
    if not use_video and keyframe_paths:
        content.append({
            "type": "input_text",
            "text": (
                context_block +
                f"\nBelow are {len(keyframe_paths)} keyframes sampled across the full video."
            ),
        })
        content.extend(_clone_media_items(encoded_keyframes or _encode_keyframes(keyframe_paths)))

    # Call VLM (try direct video first, fallback to keyframes on error)
    raw_text = _call_vlm(client, vlm_cfg, content)

    # If video mode failed with an API error that hints at unsupported input,
    # retry with keyframes
    if use_video and raw_text.startswith("API_ERROR") and keyframe_paths:
        print("    Video input failed, falling back to keyframes ...")
        fallback_content: list = [{"type": "input_text", "text": TASK_CORRECTNESS_PROMPT}]
        fallback_content.append({
            "type": "input_text",
            "text": (
                f"Video: {video_name}\n"
                f"Topic/Prompt: {topic}\n\n"
                f"Below are {len(keyframe_paths)} keyframes sampled across the full video.\n"
                "Please evaluate content accuracy, pedagogical clarity, and engagement."
            ),
        })
        fallback_content.extend(_clone_media_items(encoded_keyframes or _encode_keyframes(keyframe_paths)))
        raw_text = _call_vlm(client, vlm_cfg, fallback_content)

    parsed = _parse_vlm_json(raw_text)

    def _to_0_1(val, default: int = 60) -> float:
        try:
            return max(0.0, min(1.0, float(val) / 100.0))
        except (TypeError, ValueError):
            return default / 100.0

    return TaskCorrectnessVerdict(
        content_accuracy=bool(parsed.get("content_accuracy", False)),
        content_accuracy_reason=str(parsed.get("content_accuracy_reason", "")),
        pedagogical_clarity=_to_0_1(parsed.get("pedagogical_clarity", 60)),
        pedagogical_clarity_reason=str(parsed.get("pedagogical_clarity_reason", "")),
        engagement=_to_0_1(parsed.get("engagement", 60)),
        engagement_reason=str(parsed.get("engagement_reason", "")),
        raw_response=raw_text,
    )


def _call_vlm(client, vlm_cfg: VLMConfig, content: list) -> str:
    """Send content to VLM and return raw text response."""
    try:
        with client.responses.stream(
            model=vlm_cfg.model,
            input=[{"role": "user", "content": content}],
            max_output_tokens=vlm_cfg.max_tokens,
            service_tier="priority",
        ) as stream:
            return stream.get_final_response().output_text.strip()
    except Exception as exc:
        return f"API_ERROR: {exc}"


# =====================================================================
# Visual Content Consistency (coverage check)
# =====================================================================

@dataclass
class VisualCoverageVerdict:
    """Whether the video covers all sections of the teaching plan."""
    coverage_ratio: float                            # 0-1
    missing_sections: List[str]                      # list of section IDs
    sections_detail: List[Dict]                      # per-section covered/evidence
    coverage_reason: str
    raw_response: str = ""


def review_visual_coverage(
    vlm_cfg: VLMConfig,
    teaching_plan: Dict,
    video_name: str = "",
    video_path: Optional[Path] = None,
    keyframe_paths: Optional[List[Path]] = None,
    *,
    encoded_keyframes: Optional[List[Dict[str, str]]] = None,
    encoded_video_input: Optional[Dict[str, str]] = None,
) -> VisualCoverageVerdict:
    """
    Check whether the video covers every section of the teaching plan.

    Returns a coverage ratio (0-1) and list of missing sections.
    """
    sections = teaching_plan.get("sections", [])
    if not sections:
        return VisualCoverageVerdict(1.0, [], [], "no sections in plan", "")

    if video_path is None and not keyframe_paths:
        raise ValueError("Provide video_path or keyframe_paths.")

    api_key = vlm_cfg.api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("No API key.")

    client = make_openai_client(api_key=api_key, base_url=vlm_cfg.base_url, timeout=180.0)

    # Build plan summary
    plan_text = "Teaching Plan Sections:\n"
    for sec in sections:
        plan_text += (
            f"  {sec.get('id', '')}: {sec.get('title', '')}\n"
            f"    Key takeaway: {sec.get('key_takeaway', '')}\n"
        )

    content: list = [{"type": "input_text", "text": VISUAL_COVERAGE_PROMPT}]

    use_video = False
    video_input = _clone_media_item(encoded_video_input or _encode_video_input(video_path))
    if video_input is not None:
        content.append({"type": "input_text", "text": f"Video: {video_name}\n\n{plan_text}"})
        content.append(video_input)
        use_video = True

    if not use_video and keyframe_paths:
        content.append({
            "type": "input_text",
            "text": f"Video: {video_name}\n\n{plan_text}\n\nKeyframes from the full video:",
        })
    content.extend(_clone_media_items(encoded_keyframes or _encode_keyframes(keyframe_paths)))

    raw_text = _call_vlm(client, vlm_cfg, content)

    if use_video and raw_text.startswith("API_ERROR") and keyframe_paths:
        print("    Video failed for coverage check, falling back to keyframes ...")
        fb: list = [{"type": "input_text", "text": VISUAL_COVERAGE_PROMPT}]
        fb.append({"type": "input_text", "text": f"Video: {video_name}\n\n{plan_text}\n\nKeyframes:"})
        fb.extend(_clone_media_items(encoded_keyframes or _encode_keyframes(keyframe_paths)))
        raw_text = _call_vlm(client, vlm_cfg, fb)

    parsed = _parse_vlm_json(raw_text)

    return VisualCoverageVerdict(
        coverage_ratio=float(parsed.get("coverage_ratio", 0.0)),
        missing_sections=[str(s) for s in parsed.get("missing_sections", [])],
        sections_detail=parsed.get("sections_covered", []),
        coverage_reason=str(parsed.get("coverage_reason", "")),
        raw_response=raw_text,
    )


# =====================================================================
# Semantic Coherence (no teaching plan needed)
# =====================================================================

SEMANTIC_COHERENCE_PROMPT = """\
You are evaluating an educational animation video for **semantic coherence**.
You do NOT have a teaching plan 鈥?judge purely from what you see.

Score the video on these criteria (each 0-100):

1. **topic_consistency**: Does the video stick to a single clear topic throughout,
   or does it jump randomly between unrelated subjects?
   100 = perfectly focused on one topic; 0 = random unrelated content.

2. **logical_progression**: Do the visuals follow a logical order
   (e.g., introduce concept 鈫?explain 鈫?example 鈫?summary)?
   100 = clear logical flow; 0 = chaotic ordering.

3. **visual_relevance**: Are the on-screen visuals (graphs, diagrams, formulas)
   relevant to what is being explained?
   100 = every visual supports the narrative; 0 = visuals are unrelated to content.

Return JSON (no markdown fences):
{
  "topic_consistency": <int 0-100>,
  "logical_progression": <int 0-100>,
  "visual_relevance": <int 0-100>,
  "coherence_reason": "<one sentence explaining your overall judgment>"
}
"""


@dataclass
class SemanticCoherenceVerdict:
    """Semantic coherence score when no teaching plan is available."""
    score: float                    # 0-1 aggregate
    topic_consistency: float        # 0-1
    logical_progression: float      # 0-1
    visual_relevance: float         # 0-1
    reason: str
    raw_response: str = ""


def review_semantic_coherence(
    vlm_cfg: VLMConfig,
    video_name: str = "",
    video_path: Optional[Path] = None,
    keyframe_paths: Optional[List[Path]] = None,
    *,
    encoded_keyframes: Optional[List[Dict[str, str]]] = None,
    encoded_video_input: Optional[Dict[str, str]] = None,
) -> SemanticCoherenceVerdict:
    """
    VLM-based semantic coherence check 鈥?no teaching plan required.
    Judges whether the video presents a coherent educational narrative.
    """
    if video_path is None and not keyframe_paths:
        raise ValueError("Provide video_path or keyframe_paths.")

    api_key = vlm_cfg.api_key or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("No API key.")

    client = make_openai_client(api_key=api_key, base_url=vlm_cfg.base_url, timeout=180.0)

    content: list = [{"type": "input_text", "text": SEMANTIC_COHERENCE_PROMPT}]

    use_video = False
    video_input = _clone_media_item(encoded_video_input or _encode_video_input(video_path))
    if video_input is not None:
        content.append({"type": "input_text", "text": f"Video: {video_name}"})
        content.append(video_input)
        use_video = True

    if not use_video and keyframe_paths:
        content.append({
            "type": "input_text",
            "text": f"Video: {video_name}\n\nKeyframes from the full video (in chronological order):",
        })
        content.extend(_clone_media_items(encoded_keyframes or _encode_keyframes(keyframe_paths)))

    raw_text = _call_vlm(client, vlm_cfg, content)

    if use_video and raw_text.startswith("API_ERROR") and keyframe_paths:
        print("    Video failed for coherence check, falling back to keyframes ...")
        fb: list = [{"type": "input_text", "text": SEMANTIC_COHERENCE_PROMPT}]
        fb.append({"type": "input_text", "text": f"Video: {video_name}\n\nKeyframes:"})
        fb.extend(_clone_media_items(encoded_keyframes or _encode_keyframes(keyframe_paths)))
        raw_text = _call_vlm(client, vlm_cfg, fb)

    parsed = _parse_vlm_json(raw_text)

    def _to_0_1_coh(val, default: int = 50) -> float:
        try:
            return max(0.0, min(1.0, float(val) / 100.0))
        except (TypeError, ValueError):
            return default / 100.0

    topic = _to_0_1_coh(parsed.get("topic_consistency", 50))
    progression = _to_0_1_coh(parsed.get("logical_progression", 50))
    relevance = _to_0_1_coh(parsed.get("visual_relevance", 50))
    score = 0.35 * topic + 0.30 * progression + 0.35 * relevance

    return SemanticCoherenceVerdict(
        score=score,
        topic_consistency=topic,
        logical_progression=progression,
        visual_relevance=relevance,
        reason=str(parsed.get("coherence_reason", "")),
        raw_response=raw_text,
    )


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
                "vlm_issues": v.vlm_issues,
                "raw_response": v.raw_response,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

