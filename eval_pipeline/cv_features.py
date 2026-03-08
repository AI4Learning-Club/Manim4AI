"""
Layer 1 – Classic CV feature extraction.

Extracts per-frame and per-segment features from a rendered Manim video.
All metrics are deterministic and fully reproducible.

Dimensions covered:
  1. Overlap / occlusion detection
  2. Rendering-artifact detection (OCR consistency)
  3. Layout density analysis
  4. Element lifecycle tracking (flash detection)
  5. Motion / animation smoothness
  6. Colour-palette consistency
"""

from __future__ import annotations

import csv
import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .config import CVConfig

# Optional OCR dependency – gracefully degrade if unavailable.
try:
    import pytesseract

    _HAS_TESSERACT = True
except ImportError:
    _HAS_TESSERACT = False


# =====================================================================
# Data containers
# =====================================================================

@dataclass
class FrameFeatures:
    """Per-frame feature vector."""

    frame: int
    sec: float

    # Overlap / occlusion
    overlap_pixels: int = 0
    text_pixels: int = 0
    occlusion_pixels: int = 0
    write_pixels: int = 0

    # Motion
    motion_pixels: int = 0

    # Overlap centroid & bbox
    cx: Optional[float] = None
    cy: Optional[float] = None
    bbox_x1: Optional[int] = None
    bbox_y1: Optional[int] = None
    bbox_x2: Optional[int] = None
    bbox_y2: Optional[int] = None

    # Layout density
    layout_max_density: float = 0.0
    layout_dense_cells: int = 0

    # Element lifecycle
    num_components: int = 0
    flash_events: int = 0

    # Colour consistency
    color_shift: float = 0.0

    # BBox IoU overlap (colour-agnostic)
    bbox_overlap_pairs: int = 0
    bbox_max_iou: float = 0.0

    # Foreground pixel-level overlap (colour-agnostic)
    fg_overlap_pixels: int = 0

    # Text-on-line/curve cross detection
    text_on_edge_pixels: int = 0

    # Rendering artifact (OCR)
    ocr_artifact: bool = False
    ocr_text: str = ""

    # Candidate flag (overlap threshold) — now combines all methods
    candidate: bool = False


@dataclass
class SegmentFeatures:
    """Aggregated features for a temporal segment."""

    segment_id: str
    start_frame: int
    end_frame: int
    start_sec: float
    end_sec: float
    duration_sec: float
    frames: int

    # Overlap stats
    overlap_avg: float = 0.0
    overlap_max: int = 0
    overlap_p90: float = 0.0
    text_avg: float = 0.0
    occlusion_avg: float = 0.0
    occlusion_ratio: float = 0.0
    text_dominance: float = 0.0

    # Motion stats
    motion_avg: float = 0.0
    active_ratio: float = 0.0
    static_ratio: float = 0.0

    # Centroid jitter
    centroid_jitter: float = 0.0

    # Layout
    layout_max_density_avg: float = 0.0
    layout_dense_cell_avg: float = 0.0

    # Lifecycle
    total_flash_events: int = 0

    # Colour
    color_shift_max: float = 0.0
    color_shift_avg: float = 0.0

    # BBox IoU overlap
    bbox_overlap_frame_ratio: float = 0.0
    bbox_max_iou_max: float = 0.0

    # Foreground pixel overlap
    fg_overlap_avg: float = 0.0
    fg_overlap_max: int = 0

    # Text-on-edge
    text_on_edge_avg: float = 0.0
    text_on_edge_max: int = 0

    # OCR artifact
    ocr_artifact_frames: int = 0

    # Classification
    label: str = ""
    score: float = 0.0
    reason: str = ""


# =====================================================================
# Helper utilities
# =====================================================================

def _ensure_odd(k: int) -> int:
    k = max(1, int(k))
    return k if k % 2 == 1 else k + 1


def _mask_centroid(mask: np.ndarray) -> Tuple[Optional[float], Optional[float]]:
    ys, xs = np.where(mask)
    if xs.size == 0:
        return None, None
    return float(xs.mean()), float(ys.mean())


def _mask_bbox(mask: np.ndarray) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
    ys, xs = np.where(mask)
    if xs.size == 0:
        return None, None, None, None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _frame_motion(prev: np.ndarray, cur: np.ndarray, thresh: float) -> int:
    diff = cur.astype(np.float32) - prev.astype(np.float32)
    dist = np.linalg.norm(diff, axis=2)
    return int((dist >= thresh).sum())


def _color_histogram(frame: np.ndarray, bins: int) -> np.ndarray:
    """Compute a normalised HSV hue-saturation histogram."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [bins, bins], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten().astype(np.float32)


def _chi_square_dist(h1: np.ndarray, h2: np.ndarray) -> float:
    return float(cv2.compareHist(h1, h2, cv2.HISTCMP_CHISQR))


# =====================================================================
# Per-frame extraction
# =====================================================================

def _extract_masks(
    frame: np.ndarray, cfg: CVConfig
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (text_mask, solid_main, dark_mask) as boolean arrays."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    text = (hsv[:, :, 1] <= cfg.text_max_sat) & (hsv[:, :, 2] >= cfg.text_min_val)
    solid = (hsv[:, :, 1] >= cfg.solid_min_sat) & (hsv[:, :, 2] >= cfg.solid_min_val)
    dark = (hsv[:, :, 1] <= cfg.dark_max_sat) & (hsv[:, :, 2] <= cfg.dark_max_val)

    solid_u8 = solid.astype(np.uint8) * 255
    k_open = _ensure_odd(max(3, cfg.solid_open_k))
    k_close = _ensure_odd(max(5, cfg.solid_close_k))
    open_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_open, k_open))
    close_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k_close, k_close))
    solid_main = cv2.morphologyEx(solid_u8, cv2.MORPH_OPEN, open_k)
    solid_main = cv2.morphologyEx(solid_main, cv2.MORPH_CLOSE, close_k) > 0

    return text, solid_main, dark


def _layout_density(frame: np.ndarray, cfg: CVConfig) -> Tuple[float, int]:
    """Compute grid-based foreground density."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, fg = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)

    h, w = fg.shape
    rh = max(1, h // cfg.layout_grid_rows)
    rw = max(1, w // cfg.layout_grid_cols)

    max_density = 0.0
    dense_count = 0
    for r in range(cfg.layout_grid_rows):
        for c in range(cfg.layout_grid_cols):
            cell = fg[r * rh : (r + 1) * rh, c * rw : (c + 1) * rw]
            cell_area = max(cell.shape[0] * cell.shape[1], 1)
            density = float(np.count_nonzero(cell)) / cell_area
            if density > max_density:
                max_density = density
            if density >= cfg.layout_density_warn:
                dense_count += 1

    return max_density, dense_count


def _count_components(frame: np.ndarray, min_area: int) -> int:
    """Count foreground connected components above *min_area*."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, fg = cv2.threshold(gray, 30, 255, cv2.THRESH_BINARY)
    n_labels, _, stats, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
    count = 0
    for i in range(1, n_labels):
        if int(stats[i, cv2.CC_STAT_AREA]) >= min_area:
            count += 1
    return count


def _ocr_region_text(frame: np.ndarray, bbox: Tuple[int, int, int, int], lang: str) -> str:
    """Run Tesseract on a cropped region.  Returns empty string on failure."""
    if not _HAS_TESSERACT:
        return ""
    x1, y1, x2, y2 = bbox
    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return ""
    try:
        return pytesseract.image_to_string(crop, lang=lang, config="--psm 6").strip()
    except Exception:
        return ""


def _levenshtein_norm(a: str, b: str) -> float:
    """Normalised Levenshtein distance in [0, 1]."""
    if not a and not b:
        return 0.0
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return 1.0
    # Simple DP
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb] / max(la, lb)


# =====================================================================
# New overlap detectors (colour-agnostic)
# =====================================================================

@dataclass
class _BBoxComp:
    label: int
    x: int
    y: int
    w: int
    h: int
    area: int


def _get_fg_components(frame: np.ndarray, min_area: int, fg_thresh: int = 30) -> List[_BBoxComp]:
    """Extract foreground connected components as bounding boxes."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, fg = cv2.threshold(gray, fg_thresh, 255, cv2.THRESH_BINARY)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
    comps = []
    for i in range(1, n_labels):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        comps.append(_BBoxComp(
            label=i,
            x=int(stats[i, cv2.CC_STAT_LEFT]),
            y=int(stats[i, cv2.CC_STAT_TOP]),
            w=int(stats[i, cv2.CC_STAT_WIDTH]),
            h=int(stats[i, cv2.CC_STAT_HEIGHT]),
            area=area,
        ))
    return comps


def _bbox_iou_overlap(
    frame: np.ndarray, cfg: CVConfig
) -> Tuple[int, float]:
    """
    Detect overlapping foreground components via bounding-box IoU.
    Returns (num_overlapping_pairs, max_iou).
    Catches same-colour overlaps that HSV masks miss.
    """
    if not cfg.bbox_iou_enabled:
        return 0, 0.0

    comps = _get_fg_components(frame, cfg.bbox_min_area, cfg.fg_thresh)
    if len(comps) < 2:
        return 0, 0.0

    pairs = 0
    max_iou = 0.0
    for i in range(len(comps)):
        for j in range(i + 1, len(comps)):
            a, b = comps[i], comps[j]
            # Intersection
            ix1 = max(a.x, b.x)
            iy1 = max(a.y, b.y)
            ix2 = min(a.x + a.w, b.x + b.w)
            iy2 = min(a.y + a.h, b.y + b.h)
            if ix2 <= ix1 or iy2 <= iy1:
                continue
            inter = (ix2 - ix1) * (iy2 - iy1)
            if inter < cfg.bbox_min_intersection:
                continue
            # IoU
            union = a.w * a.h + b.w * b.h - inter
            iou = inter / max(union, 1)
            # Overlap ratio (intersection / smaller bbox)
            smaller = min(a.w * a.h, b.w * b.h)
            ratio = inter / max(smaller, 1)

            if iou >= cfg.bbox_min_iou or ratio >= cfg.bbox_min_overlap_ratio:
                pairs += 1
                max_iou = max(max_iou, iou)

    return pairs, max_iou


def _fg_pixel_overlap(
    frame: np.ndarray, cfg: CVConfig
) -> int:
    """
    Detect pixel-level overlap between distinct foreground components.
    Dilates each component slightly, then checks if dilated regions of
    different components overlap.  Colour-agnostic.
    Returns the number of overlapping pixels.
    """
    if not cfg.fg_pixel_overlap_enabled:
        return 0

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, fg = cv2.threshold(gray, cfg.fg_thresh, 255, cv2.THRESH_BINARY)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)

    # Keep only significant components
    valid = []
    for i in range(1, n_labels):
        if int(stats[i, cv2.CC_STAT_AREA]) >= cfg.bbox_min_area:
            valid.append(i)

    if len(valid) < 2:
        return 0

    dk = _ensure_odd(cfg.fg_dilate_k)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dk, dk))

    # Dilate each component and accumulate overlap count
    h, w = labels.shape
    coverage = np.zeros((h, w), dtype=np.int32)
    for lbl in valid:
        comp_mask = (labels == lbl).astype(np.uint8) * 255
        dilated = cv2.dilate(comp_mask, kernel, iterations=1)
        coverage += (dilated > 0).astype(np.int32)

    # Pixels covered by >= 2 components = overlap
    overlap_pixels = int((coverage >= 2).sum())
    return overlap_pixels


def _text_on_edge_overlap(
    frame: np.ndarray, cfg: CVConfig
) -> int:
    """
    Detect text/annotation overlapping lines or curves.

    Strategy: separate "thick" foreground (text, labels) from "thin" foreground
    (lines, curves, arrows) using morphological size filtering, then check
    if thick regions spatially overlap thin regions.
    This avoids the self-edge problem where text's own strokes match edges.
    """
    if not cfg.text_line_cross_enabled:
        return 0

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    _, fg = cv2.threshold(gray, cfg.fg_thresh, 255, cv2.THRESH_BINARY)

    # "Thick" foreground: survives aggressive erosion → text / large shapes
    thick_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    thick_eroded = cv2.erode(fg, thick_k, iterations=1)
    thick_mask = cv2.dilate(thick_eroded, thick_k, iterations=2) > 0  # restore + expand

    # "Thin" foreground: original fg minus thick regions → lines, curves, arrows
    thin_mask = (fg > 0) & (~thick_mask)

    # Dilate thin mask slightly so nearby text triggers
    dk = _ensure_odd(cfg.edge_dilate_k)
    dilate_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dk, dk))
    thin_dilated = cv2.dilate(thin_mask.astype(np.uint8) * 255, dilate_k, iterations=1) > 0

    # Overlap: thick (text) pixels that sit on dilated thin (line) regions
    overlap = thick_mask & thin_dilated
    return int(overlap.sum())


# =====================================================================
# Main extraction loop
# =====================================================================

def extract_all_frames(
    video_path: Path,
    cfg: CVConfig,
    *,
    progress_callback=None,
) -> Tuple[List[FrameFeatures], float, int]:
    """
    Process every frame of *video_path* and return:
      (frame_features_list, fps, total_frames)
    """

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS))
    if not math.isfinite(fps) or fps <= 0:
        fps = 30.0

    total_est = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    ok, frame = cap.read()
    if not ok or frame is None:
        cap.release()
        raise RuntimeError("Video is empty or unreadable")

    step = max(1, cfg.frame_step)
    features: List[FrameFeatures] = []

    prev_frame: Optional[np.ndarray] = None
    prev_solid: Optional[np.ndarray] = None
    prev_hist: Optional[np.ndarray] = None
    prev_components: int = 0
    prev_ocr_text: str = ""
    frame_idx = 0
    processed = 0

    while ok and frame is not None:
        if frame_idx % step == 0:
            ff = FrameFeatures(frame=frame_idx, sec=frame_idx / fps)

            # --- 1. Overlap / occlusion masks ---
            text_mask, solid_main, dark_mask = _extract_masks(frame, cfg)
            text_on_solid = text_mask & solid_main
            ff.text_pixels = int(text_on_solid.sum())
            signal_mask = text_on_solid.copy()

            if prev_frame is not None:
                ff.motion_pixels = _frame_motion(prev_frame, frame, cfg.motion_diff_thresh)
                delta = frame.astype(np.float32) - prev_frame.astype(np.float32)
                delta_dist = np.linalg.norm(delta, axis=2)
                changed = delta_dist >= cfg.change_diff_thresh

                if prev_solid is not None:
                    occ_mask = changed & dark_mask & prev_solid
                    wr_mask = changed & text_mask & prev_solid
                else:
                    occ_mask = np.zeros_like(changed, dtype=bool)
                    wr_mask = np.zeros_like(changed, dtype=bool)

                ff.occlusion_pixels = int(occ_mask.sum())
                ff.write_pixels = int(wr_mask.sum())
                signal_mask = signal_mask | occ_mask | wr_mask

            ff.overlap_pixels = int(signal_mask.sum())
            ff.cx, ff.cy = _mask_centroid(signal_mask)
            ff.bbox_x1, ff.bbox_y1, ff.bbox_x2, ff.bbox_y2 = _mask_bbox(signal_mask)

            # --- 1b. BBox IoU overlap (colour-agnostic) ---
            ff.bbox_overlap_pairs, ff.bbox_max_iou = _bbox_iou_overlap(frame, cfg)

            # --- 1c. Foreground pixel-level overlap (colour-agnostic) ---
            ff.fg_overlap_pixels = _fg_pixel_overlap(frame, cfg)

            # --- 1d. Text-on-line/curve cross detection ---
            ff.text_on_edge_pixels = _text_on_edge_overlap(frame, cfg)

            # --- Candidate: ANY overlap method triggers ---
            hsv_candidate = ff.overlap_pixels >= cfg.candidate_min_pixels
            bbox_candidate = ff.bbox_overlap_pairs >= 1
            fg_candidate = ff.fg_overlap_pixels >= cfg.fg_min_overlap_pixels
            edge_candidate = ff.text_on_edge_pixels >= cfg.text_on_edge_min_pixels
            ff.candidate = hsv_candidate or bbox_candidate or fg_candidate or edge_candidate

            # --- 2. Layout density ---
            ff.layout_max_density, ff.layout_dense_cells = _layout_density(frame, cfg)

            # --- 3. Element lifecycle (component count change) ---
            n_comp = _count_components(frame, cfg.lifecycle_min_area)
            ff.num_components = n_comp
            if prev_frame is not None:
                appeared = max(0, n_comp - prev_components)
                disappeared = max(0, prev_components - n_comp)
                if disappeared > 0 and processed >= 2:
                    prev_ff = features[-1] if features else None
                    if prev_ff is not None and prev_ff.num_components > prev_components:
                        ff.flash_events = min(appeared, disappeared)
            prev_components = n_comp

            # --- 4. Colour consistency ---
            hist = _color_histogram(frame, cfg.color_hist_bins)
            if prev_hist is not None:
                ff.color_shift = _chi_square_dist(prev_hist, hist)
            prev_hist = hist

            # --- 5. Rendering-artifact detection via OCR ---
            if (
                cfg.ocr_enabled
                and _HAS_TESSERACT
                and ff.bbox_x1 is not None
                and ff.overlap_pixels >= cfg.candidate_min_pixels
                and processed % 5 == 0
            ):
                bbox = (ff.bbox_x1, ff.bbox_y1, ff.bbox_x2, ff.bbox_y2)
                ocr_text = _ocr_region_text(frame, bbox, cfg.ocr_lang)
                ff.ocr_text = ocr_text
                if prev_ocr_text and ocr_text:
                    dist = _levenshtein_norm(prev_ocr_text, ocr_text)
                    if dist >= cfg.ocr_levenshtein_thresh:
                        ff.ocr_artifact = True
                prev_ocr_text = ocr_text

            # --- bookkeeping ---
            prev_frame = frame
            prev_solid = solid_main
            features.append(ff)
            processed += 1

            if progress_callback and processed % 50 == 0:
                progress_callback(frame_idx, total_est)

        frame_idx += 1
        ok, frame = cap.read()

    if progress_callback:
        progress_callback(frame_idx, total_est)

    cap.release()
    return features, fps, frame_idx


# =====================================================================
# Segment aggregation
# =====================================================================

def merge_candidate_frames(
    features: List[FrameFeatures],
    fps: float,
    cfg: CVConfig,
) -> List[Tuple[int, int]]:
    """Merge candidate frame indices into (start, end) segments.

    Frame indices are real video frame numbers (which may be non-contiguous
    when frame_step > 1).  The merge gap and minimum length thresholds are
    applied in real-frame units so that time-based behaviour stays the same
    regardless of the sampling rate.
    """
    indices = [f.frame for f in features if f.candidate]
    if not indices:
        return []

    step = max(1, cfg.frame_step)
    max_gap = max(step, int(round(cfg.merge_gap_sec * fps)))
    min_len = max(1, cfg.min_segment_frames)

    segments: List[Tuple[int, int]] = []
    start = indices[0]
    prev = indices[0]

    for idx in indices[1:]:
        if idx - prev <= max_gap:
            prev = idx
            continue
        n_sampled = sum(1 for f in features if start <= f.frame <= prev)
        if n_sampled >= min_len:
            segments.append((start, prev))
        start = idx
        prev = idx

    n_sampled = sum(1 for f in features if start <= f.frame <= prev)
    if n_sampled >= min_len:
        segments.append((start, prev))

    return segments


def compute_segment_features(
    seg_id: str,
    start: int,
    end: int,
    fps: float,
    features: List[FrameFeatures],
    cfg: CVConfig,
) -> SegmentFeatures:
    """Aggregate per-frame features into a SegmentFeatures object.

    *start* and *end* are real video frame numbers.  When frame_step > 1
    the features list may be sparse, so we filter by frame-number range
    instead of slicing by list index.
    """
    seg_frames = [f for f in features if start <= f.frame <= end]
    n = len(seg_frames)

    overlaps = np.array([f.overlap_pixels for f in seg_frames], dtype=np.float32)
    texts = np.array([f.text_pixels for f in seg_frames], dtype=np.float32)
    occs = np.array([f.occlusion_pixels for f in seg_frames], dtype=np.float32)
    motions = np.array([f.motion_pixels for f in seg_frames], dtype=np.float32)
    densities = np.array([f.layout_max_density for f in seg_frames], dtype=np.float32)
    dense_cells = np.array([f.layout_dense_cells for f in seg_frames], dtype=np.float32)
    color_shifts = np.array([f.color_shift for f in seg_frames], dtype=np.float32)

    centroids = np.array(
        [[f.cx, f.cy] for f in seg_frames if f.cx is not None], dtype=np.float32
    )
    if centroids.shape[0] >= 2:
        centered = centroids - centroids.mean(axis=0, keepdims=True)
        jitter = float(np.sqrt((centered ** 2).sum(axis=1).mean()))
    else:
        jitter = 0.0

    occ_thresh = max(20.0, 0.1 * float(overlaps.max())) if overlaps.max() > 0 else 20.0

    sf = SegmentFeatures(
        segment_id=seg_id,
        start_frame=start,
        end_frame=end,
        start_sec=start / fps,
        end_sec=end / fps,
        duration_sec=(end - start + 1) / fps,
        frames=n,
        # overlap
        overlap_avg=float(overlaps.mean()),
        overlap_max=int(overlaps.max()),
        overlap_p90=float(np.percentile(overlaps, 90)),
        text_avg=float(texts.mean()),
        occlusion_avg=float(occs.mean()),
        occlusion_ratio=float((occs >= occ_thresh).mean()),
        text_dominance=float(texts.mean() / max(occs.mean(), 1.0)),
        # motion
        motion_avg=float(motions.mean()),
        active_ratio=float((motions >= cfg.motion_active_pixels).mean()),
        static_ratio=float((motions <= cfg.motion_static_pixels).mean()),
        centroid_jitter=jitter,
        # layout
        layout_max_density_avg=float(densities.mean()),
        layout_dense_cell_avg=float(dense_cells.mean()),
        # lifecycle
        total_flash_events=sum(f.flash_events for f in seg_frames),
        # colour
        color_shift_max=float(color_shifts.max()) if n > 0 else 0.0,
        color_shift_avg=float(color_shifts.mean()) if n > 0 else 0.0,
        # BBox IoU overlap
        bbox_overlap_frame_ratio=float(
            sum(1 for f in seg_frames if f.bbox_overlap_pairs > 0) / max(n, 1)
        ),
        bbox_max_iou_max=float(max((f.bbox_max_iou for f in seg_frames), default=0.0)),
        # Foreground pixel overlap
        fg_overlap_avg=float(np.array([f.fg_overlap_pixels for f in seg_frames]).mean()),
        fg_overlap_max=int(max((f.fg_overlap_pixels for f in seg_frames), default=0)),
        # Text-on-edge
        text_on_edge_avg=float(np.array([f.text_on_edge_pixels for f in seg_frames]).mean()),
        text_on_edge_max=int(max((f.text_on_edge_pixels for f in seg_frames), default=0)),
        # OCR artifact
        ocr_artifact_frames=sum(1 for f in seg_frames if f.ocr_artifact),
    )
    return sf


# =====================================================================
# Segment classification (rule-based)
# =====================================================================

def classify_segment(sf: SegmentFeatures, cfg: CVConfig) -> Tuple[str, float, str]:
    """
    Classify a segment into one of:
      cv_fail           – high confidence this is a rendering / overlap bug
      likely_intentional – deliberate layout (e.g. side-by-side text + graph)
      needs_vlm         – uncertain, needs VLM confirmation
    Returns (label, confidence_score, reason).
    """

    d = sf.duration_sec
    omax = sf.overlap_max
    ar = sf.active_ratio
    j = sf.centroid_jitter
    mavg = sf.motion_avg
    sr = sf.static_ratio
    oratio = sf.occlusion_ratio
    td = sf.text_dominance
    ocr_af = sf.ocr_artifact_frames
    bbox_fr = sf.bbox_overlap_frame_ratio
    bbox_iou = sf.bbox_max_iou_max
    fg_max = sf.fg_overlap_max
    fg_avg = sf.fg_overlap_avg
    toe_max = sf.text_on_edge_max
    toe_avg = sf.text_on_edge_avg

    # --- Rendering artifact detected by OCR ---
    if ocr_af >= 2:
        score = min(1.0, 0.5 + 0.1 * ocr_af)
        return "cv_fail", score, "rendering-artifact-ocr"

    # --- Colour-agnostic overlap: BBox IoU + fg pixel + text-on-edge ---
    # Strong bbox overlap during active animation → likely a real bug
    has_bbox_signal = bbox_fr >= 0.3 and bbox_iou >= 0.08
    has_fg_signal = fg_max >= 200
    has_edge_signal = toe_max >= 100 and ar >= 0.2
    colour_agnostic_hit = has_bbox_signal or has_fg_signal or has_edge_signal

    if colour_agnostic_hit and d >= cfg.risk_min_duration and ar >= 0.15:
        sub_scores = []
        if has_bbox_signal:
            sub_scores.append(0.4 * min(2.0, bbox_iou / 0.1) + 0.3 * min(2.0, bbox_fr / 0.3))
        if has_fg_signal:
            sub_scores.append(0.5 * min(2.0, fg_max / 200))
        if has_edge_signal:
            sub_scores.append(0.4 * min(2.0, toe_max / 100) + 0.2 * min(2.0, ar / 0.3))
        score = min(1.0, max(sub_scores) * 0.6 + 0.2 * min(2.0, d / max(cfg.risk_min_duration, 1e-6)))
        return "cv_fail", score, "colour-agnostic-overlap"

    # If colour-agnostic signals exist but weaker → needs_vlm
    weak_agnostic = (bbox_fr >= 0.1 or fg_max >= 80 or toe_max >= 50) and d >= 0.3
    if weak_agnostic and not colour_agnostic_hit:
        score = min(1.0, 0.3 * min(2.0, bbox_fr / 0.2)
                    + 0.3 * min(2.0, fg_max / 150)
                    + 0.2 * min(2.0, toe_max / 80)
                    + 0.2 * min(2.0, d / 1.0))
        return "needs_vlm", score, "weak-colour-agnostic-signal"

    # --- Text-dominant layout -> likely intentional ---
    if d >= cfg.intent_min_duration and td >= 2.0 and oratio <= 0.08:
        score = min(1.0, 0.45 * min(3.0, d / max(cfg.intent_min_duration, 1e-6))
                     + 0.35 * min(3.0, td / 2.0)
                     + 0.20 * min(3.0, 0.08 / max(oratio + 1e-6, 1e-6)))
        return "likely_intentional", score, "text-dominant-layout"

    # --- Long + static + low jitter → likely intentional ---
    if (d >= cfg.intent_min_duration
            and mavg <= cfg.intent_max_motion
            and sr >= cfg.intent_min_static_ratio
            and j <= cfg.intent_max_jitter
            and oratio < cfg.risk_min_occlusion_ratio):
        score = min(1.0, (d / max(cfg.intent_min_duration, 1e-6))
                    * (sr / max(cfg.intent_min_static_ratio, 1e-6))
                    * (max(cfg.intent_max_motion, 1.0) / max(mavg, 1.0))
                    * (max(cfg.intent_max_jitter, 1.0) / max(j + 1e-6, 1.0)))
        return "likely_intentional", score, "long+static+low-jitter"

    # --- High overlap + occlusion → cv_fail ---
    occ_dominant = sf.occlusion_avg >= 0.7 * max(sf.text_avg, 1.0)
    if (d >= cfg.risk_min_duration
            and omax >= cfg.risk_min_overlap
            and ((oratio >= cfg.risk_min_occlusion_ratio and occ_dominant)
                 or (ar >= cfg.risk_min_active_ratio
                     and j >= cfg.risk_min_jitter
                     and occ_dominant))):
        score = min(1.0, 0.35 * min(2.0, omax / max(cfg.risk_min_overlap, 1.0))
                    + 0.30 * min(2.0, d / max(cfg.risk_min_duration, 1e-6))
                    + 0.15 * min(2.0, ar / max(cfg.risk_min_active_ratio, 1e-6))
                    + 0.10 * min(2.0, j / max(cfg.risk_min_jitter, 1e-6))
                    + 0.10 * min(2.0, oratio / max(cfg.risk_min_occlusion_ratio, 1e-6)))
        return "cv_fail", score, "high-overlap/occlusion"

    # --- Fallback: uncertain → needs VLM ---
    score = min(1.0, 0.4 * min(2.0, omax / max(cfg.risk_min_overlap, 1.0))
                + 0.3 * min(2.0, d / max(cfg.risk_min_duration, 1e-6))
                + 0.2 * min(2.0, j / max(cfg.risk_min_jitter, 1e-6))
                + 0.1 * min(2.0, ar / max(cfg.risk_min_active_ratio, 1e-6)))
    return "needs_vlm", score, "uncertain-pattern"


# =====================================================================
# Global (video-level) CV metrics
# =====================================================================

@dataclass
class GlobalCVMetrics:
    """Video-level aggregated CV metrics for fusion scoring."""

    total_frames: int = 0
    fps: float = 0.0
    duration_sec: float = 0.0

    # Overlap dimension
    overlap_frame_ratio: float = 0.0        # fraction of frames flagged
    cv_fail_count: int = 0
    cv_fail_duration_sec: float = 0.0

    # Rendering artifact dimension
    ocr_artifact_total: int = 0

    # Layout dimension
    layout_dense_frame_ratio: float = 0.0   # fraction of frames with dense cells

    # Animation smoothness
    motion_discontinuity_count: int = 0     # sudden jumps in motion energy
    flash_event_total: int = 0

    # Colour consistency
    color_shift_events: int = 0             # frames exceeding shift threshold
    color_shift_max: float = 0.0


def compute_global_cv_metrics(
    features: List[FrameFeatures],
    segments: List[SegmentFeatures],
    fps: float,
    cfg: CVConfig,
) -> GlobalCVMetrics:
    """Derive video-level metrics from frame + segment features."""

    n = len(features)
    if n == 0:
        return GlobalCVMetrics()

    g = GlobalCVMetrics(
        total_frames=n,
        fps=fps,
        duration_sec=n / fps,
    )

    # Overlap
    g.overlap_frame_ratio = sum(1 for f in features if f.candidate) / n
    fail_segs = [s for s in segments if s.label == "cv_fail"]
    g.cv_fail_count = len(fail_segs)
    g.cv_fail_duration_sec = sum(s.duration_sec for s in fail_segs)

    # Rendering artifact
    g.ocr_artifact_total = sum(f.ocr_artifact for f in features)

    # Layout
    g.layout_dense_frame_ratio = sum(
        1 for f in features if f.layout_dense_cells > 0
    ) / n

    # Animation smoothness – detect motion energy spikes
    motions = [f.motion_pixels for f in features]
    if len(motions) >= 3:
        m = np.array(motions, dtype=np.float32)
        diff = np.abs(np.diff(m))
        median_diff = float(np.median(diff)) + 1.0
        g.motion_discontinuity_count = int((diff > 10 * median_diff).sum())

    g.flash_event_total = sum(f.flash_events for f in features)

    # Colour consistency
    g.color_shift_max = max((f.color_shift for f in features), default=0.0)
    g.color_shift_events = sum(
        1 for f in features if f.color_shift >= cfg.color_shift_thresh
    )

    return g


# =====================================================================
# I/O helpers
# =====================================================================

def _to_hhmmss(sec: float) -> str:
    ms_total = int(round(max(0.0, sec) * 1000))
    s, ms = divmod(ms_total, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def save_frame_csv(features: List[FrameFeatures], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "frame", "sec", "hhmmss",
        "overlap_pixels", "text_pixels", "occlusion_pixels", "write_pixels",
        "bbox_overlap_pairs", "bbox_max_iou",
        "fg_overlap_pixels",
        "text_on_edge_pixels",
        "motion_pixels",
        "layout_max_density", "layout_dense_cells",
        "num_components", "flash_events",
        "color_shift",
        "ocr_artifact",
        "candidate",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for ff in features:
            w.writerow({
                "frame": ff.frame,
                "sec": round(ff.sec, 6),
                "hhmmss": _to_hhmmss(ff.sec),
                "overlap_pixels": ff.overlap_pixels,
                "text_pixels": ff.text_pixels,
                "occlusion_pixels": ff.occlusion_pixels,
                "write_pixels": ff.write_pixels,
                "bbox_overlap_pairs": ff.bbox_overlap_pairs,
                "bbox_max_iou": round(ff.bbox_max_iou, 4),
                "fg_overlap_pixels": ff.fg_overlap_pixels,
                "text_on_edge_pixels": ff.text_on_edge_pixels,
                "motion_pixels": ff.motion_pixels,
                "layout_max_density": round(ff.layout_max_density, 4),
                "layout_dense_cells": ff.layout_dense_cells,
                "num_components": ff.num_components,
                "flash_events": ff.flash_events,
                "color_shift": round(ff.color_shift, 6),
                "ocr_artifact": int(ff.ocr_artifact),
                "candidate": int(ff.candidate),
            })


def save_segment_csv(segments: List[SegmentFeatures], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "segment_id", "label", "score", "reason",
        "start_frame", "end_frame", "start_sec", "end_sec",
        "start_hhmmss", "end_hhmmss",
        "duration_sec", "frames",
        "overlap_avg", "overlap_max", "overlap_p90",
        "text_avg", "occlusion_avg", "occlusion_ratio", "text_dominance",
        "motion_avg", "active_ratio", "static_ratio", "centroid_jitter",
        "layout_max_density_avg", "layout_dense_cell_avg",
        "total_flash_events",
        "color_shift_max", "color_shift_avg",
        "bbox_overlap_frame_ratio", "bbox_max_iou_max",
        "fg_overlap_avg", "fg_overlap_max",
        "text_on_edge_avg", "text_on_edge_max",
        "ocr_artifact_frames",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for sf in segments:
            row = {k: getattr(sf, k) for k in fieldnames if hasattr(sf, k)}
            row["start_hhmmss"] = _to_hhmmss(sf.start_sec)
            row["end_hhmmss"] = _to_hhmmss(sf.end_sec)
            for k in ("overlap_avg", "overlap_p90", "text_avg", "occlusion_avg",
                       "text_dominance", "motion_avg", "active_ratio", "static_ratio",
                       "centroid_jitter", "layout_max_density_avg", "layout_dense_cell_avg",
                       "color_shift_max", "color_shift_avg", "score",
                       "occlusion_ratio", "duration_sec", "start_sec", "end_sec"):
                if k in row and isinstance(row[k], float):
                    row[k] = round(row[k], 4)
            w.writerow(row)


def extract_keyframes(
    video_path: Path,
    segments: List[SegmentFeatures],
    out_dir: Path,
    n_keyframes: int = 3,
) -> None:
    """Save start / mid / end keyframes for each segment."""
    if not segments:
        return
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return
    for sf in segments:
        seg_dir = out_dir / sf.segment_id
        seg_dir.mkdir(parents=True, exist_ok=True)
        points = [
            ("start", sf.start_frame),
            ("mid", (sf.start_frame + sf.end_frame) // 2),
            ("end", sf.end_frame),
        ]
        for tag, fidx in points:
            cap.set(cv2.CAP_PROP_POS_FRAMES, fidx)
            ok, frame = cap.read()
            if ok:
                cv2.imwrite(str(seg_dir / f"{tag}_{fidx}.jpg"), frame)
    cap.release()
