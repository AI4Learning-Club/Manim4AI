"""
Centralised configuration for the evaluation pipeline.

All thresholds, model settings, and output paths live here so that
experiments are reproducible by swapping a single YAML / dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Layer 1 – Classic CV feature extraction
# ---------------------------------------------------------------------------

@dataclass
class CVConfig:
    """Parameters for per-frame CV feature extraction."""

    # --- HSV masks (text / solid / dark) ---
    text_max_sat: int = 48
    text_min_val: int = 145
    solid_min_sat: int = 20
    solid_min_val: int = 20
    solid_open_k: int = 5
    solid_close_k: int = 9
    dark_max_sat: int = 80
    dark_max_val: int = 55

    # --- Frame-level candidate thresholds ---
    candidate_min_pixels: int = 90
    change_diff_thresh: float = 16.0
    motion_diff_thresh: float = 12.0
    motion_active_pixels: int = 30
    motion_static_pixels: int = 18

    # --- BBox IoU overlap (colour-agnostic) ---
    bbox_iou_enabled: bool = True
    bbox_min_area: int = 120             # min component area for bbox tracking
    bbox_min_intersection: int = 100     # min intersection area in pixels
    bbox_min_iou: float = 0.08          # min IoU to count as overlap
    bbox_min_overlap_ratio: float = 0.20 # min intersection/smaller_bbox ratio

    # --- Foreground pixel-level overlap (colour-agnostic) ---
    fg_pixel_overlap_enabled: bool = True
    fg_thresh: int = 30                  # grayscale threshold for foreground
    fg_dilate_k: int = 3                 # dilate thin elements before overlap test
    fg_min_overlap_pixels: int = 50      # min overlapping fg pixels

    # --- Text-line cross detection ---
    text_line_cross_enabled: bool = True
    edge_canny_low: int = 50
    edge_canny_high: int = 150
    edge_dilate_k: int = 3              # dilate edges to create "line region"
    text_on_edge_min_pixels: int = 300  # min text pixels overlapping edge region (high to avoid self-edges)

    # --- Rendering-artifact detection (OCR consistency) ---
    ocr_enabled: bool = True
    ocr_lang: str = "chi_sim+eng"
    ocr_region_iou_thresh: float = 0.5
    ocr_levenshtein_thresh: float = 0.35   # normalised edit distance

    # --- Layout density grid ---
    layout_grid_rows: int = 6
    layout_grid_cols: int = 8
    layout_density_warn: float = 0.60       # cell occupancy warning threshold

    # --- Element lifecycle tracking ---
    lifecycle_min_area: int = 100
    lifecycle_flash_max_frames: int = 3     # "flash" = appears then vanishes

    # --- Colour consistency ---
    color_hist_bins: int = 64
    color_shift_thresh: float = 0.15        # chi-square distance between histograms

    # --- Frame sampling ---
    frame_step: int = 1                      # process every N-th frame (1 = all)

    # --- Segment merge / filter ---
    merge_gap_sec: float = 0.15
    min_segment_frames: int = 8

    # --- Segment classification ---
    risk_min_duration: float = 0.6
    risk_min_overlap: int = 420
    risk_min_active_ratio: float = 0.28
    risk_min_jitter: float = 22.0
    risk_min_occlusion_ratio: float = 0.10

    intent_min_duration: float = 2.2
    intent_max_motion: float = 22.0
    intent_min_static_ratio: float = 0.72
    intent_max_jitter: float = 18.0


# ---------------------------------------------------------------------------
# Layer 2 – VLM semantic judgment
# ---------------------------------------------------------------------------

@dataclass
class VLMConfig:
    """Parameters for VLM-based review."""

    provider: str = "openai"                # openai | zhipu | local
    model: str = "gpt-5.4"
    api_key: Optional[str] = None           # will be filled at runtime / CLI
    base_url: Optional[str] = "https://api.tabcode.cc/openai"  # custom endpoint
    temperature: float = 0.0
    max_tokens: int = 1024
    max_segments: int = 0                   # 0 = no limit
    include_cv_fail: bool = True            # also send cv_fail to VLM
    keyframes_per_segment: int = 3          # start / mid / end


# ---------------------------------------------------------------------------
# Layer 3 – Fusion scoring
# ---------------------------------------------------------------------------

@dataclass
class FusionConfig:
    """Weights for fusing CV signals and VLM verdicts into a final score."""

    # Dimension weights (must sum to 1.0)
    w_overlap: float = 0.20
    w_rendering: float = 0.20
    w_layout: float = 0.15
    w_animation: float = 0.15
    w_color_consistency: float = 0.10
    w_vlm_semantic: float = 0.20

    # Per-dimension pass thresholds (score >= thresh → pass)
    pass_overlap: float = 0.70
    pass_rendering: float = 0.70
    pass_layout: float = 0.60
    pass_animation: float = 0.60
    pass_color: float = 0.70
    pass_vlm: float = 0.60

    # Overall
    overall_pass: float = 0.65


# ---------------------------------------------------------------------------
# Top-level pipeline config
# ---------------------------------------------------------------------------

@dataclass
class PipelineConfig:
    """Root configuration aggregating all layers."""

    cv: CVConfig = field(default_factory=CVConfig)
    vlm: VLMConfig = field(default_factory=VLMConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)
    output_dir: Path = Path("eval_output")
    skip_vlm: bool = False                  # run CV-only mode
    vlm_all: bool = False                   # send ALL segments to VLM
