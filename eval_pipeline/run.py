#!/usr/bin/env python3
"""
Main entry-point for the Manim Video Evaluation Pipeline.

Usage examples:

  # CV-only mode (no VLM, no API key needed):
  python -m eval_pipeline.run video.mp4 --skip-vlm

  # Full pipeline (CV + VLM):
  python -m eval_pipeline.run video.mp4 --api-key sk-xxx

  # Full pipeline with env var:
  set OPENAI_API_KEY=sk-xxx
  python -m eval_pipeline.run video.mp4

  # Custom model / endpoint:
  python -m eval_pipeline.run video.mp4 --model gpt-4.1 --base-url https://...

  # Batch mode (multiple videos):
  python -m eval_pipeline.run video1.mp4 video2.mp4 video3.mp4

Architecture:
  Layer 1 (CV)    → deterministic per-frame feature extraction
  Layer 2 (VLM)   → semantic judgment on suspicious segments
  Layer 3 (Fusion) → weighted multi-dimensional scoring + report
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from .config import CVConfig, FusionConfig, PipelineConfig, VLMConfig
from .cv_features import (
    SegmentFeatures,
    classify_segment,
    compute_global_cv_metrics,
    compute_segment_features,
    extract_all_frames,
    extract_keyframes,
    merge_candidate_frames,
    save_frame_csv,
    save_segment_csv,
)
from .vlm_judge import VLMVerdict, review_segments, save_verdicts_jsonl
from .fusion import compute_report, print_report, save_report_json


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

DEFAULT_API_KEY = os.environ.get("OPENAI_API_KEY")
DEFAULT_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.tabcode.cc/openai")
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")


# =====================================================================
# CLI
# =====================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval_pipeline",
        description="Three-layer evaluation pipeline for Manim-rendered videos.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    p.add_argument(
        "inputs",
        nargs="+",
        type=Path,
        help="One or more input MP4 video paths",
    )
    p.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=Path("eval_output"),
        help="Root output directory (default: eval_output)",
    )

    # --- VLM options ---
    vlm = p.add_argument_group("VLM options")
    vlm.add_argument("--skip-vlm", action="store_true", help="Run CV-only mode")
    vlm.add_argument("--api-key", type=str, default=DEFAULT_API_KEY, help="OpenAI API key (default: OPENAI_API_KEY from .env/env)")
    vlm.add_argument("--base-url", type=str, default=DEFAULT_BASE_URL, help="Custom API base URL (default: OPENAI_BASE_URL from .env/env)")
    vlm.add_argument("--model", type=str, default=DEFAULT_MODEL, help="VLM model name (default: OPENAI_MODEL from .env/env)")
    vlm.add_argument("--max-vlm-segments", type=int, default=0, help="Max segments to send to VLM (0=all)")
    vlm.add_argument("--vlm-all", action="store_true", help="Send ALL segments to VLM (including likely_intentional)")

    # --- CV tuning ---
    cv = p.add_argument_group("CV tuning")
    cv.add_argument("--no-ocr", action="store_true", help="Disable OCR artifact detection")
    cv.add_argument("--text-max-sat", type=int, default=48)
    cv.add_argument("--text-min-val", type=int, default=145)
    cv.add_argument("--solid-min-sat", type=int, default=20)
    cv.add_argument("--solid-min-val", type=int, default=20)
    cv.add_argument("--frame-step", type=int, default=1, help="Process every N-th frame (1=all, 30=sample at ~2fps for 60fps video)")
    cv.add_argument("--candidate-min-pixels", type=int, default=90)

    # --- Fusion weights ---
    fw = p.add_argument_group("Fusion weights")
    fw.add_argument("--w-overlap", type=float, default=0.20)
    fw.add_argument("--w-rendering", type=float, default=0.20)
    fw.add_argument("--w-layout", type=float, default=0.15)
    fw.add_argument("--w-animation", type=float, default=0.15)
    fw.add_argument("--w-color", type=float, default=0.10)
    fw.add_argument("--w-vlm", type=float, default=0.20)

    return p


def build_config(args: argparse.Namespace) -> PipelineConfig:
    cv_cfg = CVConfig(
        text_max_sat=args.text_max_sat,
        text_min_val=args.text_min_val,
        solid_min_sat=args.solid_min_sat,
        solid_min_val=args.solid_min_val,
        candidate_min_pixels=args.candidate_min_pixels,
        ocr_enabled=not args.no_ocr,
        frame_step=args.frame_step,
    )

    vlm_cfg = VLMConfig(
        model=args.model,
        api_key=args.api_key,
        base_url=args.base_url,
        max_segments=args.max_vlm_segments,
        include_cv_fail=True,
    )

    fusion_cfg = FusionConfig(
        w_overlap=args.w_overlap,
        w_rendering=args.w_rendering,
        w_layout=args.w_layout,
        w_animation=args.w_animation,
        w_color_consistency=args.w_color,
        w_vlm_semantic=args.w_vlm,
    )

    return PipelineConfig(
        cv=cv_cfg,
        vlm=vlm_cfg,
        fusion=fusion_cfg,
        output_dir=args.output_dir,
        skip_vlm=args.skip_vlm,
        vlm_all=getattr(args, "vlm_all", False),
    )


# =====================================================================
# Single-video pipeline
# =====================================================================

def evaluate_video(video_path: Path, cfg: PipelineConfig) -> dict:
    """Run the full pipeline on a single video.  Returns the report dict."""

    video_name = video_path.name
    stem = video_path.stem
    out_dir = cfg.output_dir / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Evaluating: {video_path}")
    print(f"  Output dir: {out_dir}")
    print(f"{'='*60}\n")

    # ------------------------------------------------------------------
    # Layer 1: CV feature extraction
    # ------------------------------------------------------------------
    print("[Layer 1] Extracting CV features ...")
    t0 = time.time()

    def cv_progress(cur, total):
        pct = cur / max(total, 1) * 100
        print(f"  frame {cur}/{total} ({pct:.0f}%)", end="\r", flush=True)

    features, fps, total_frames = extract_all_frames(
        video_path, cfg.cv, progress_callback=cv_progress
    )
    print(f"\n  Done: {total_frames} frames @ {fps:.1f} fps  ({time.time()-t0:.1f}s)")

    # Save per-frame CSV
    save_frame_csv(features, out_dir / "frame_stats.csv")

    # Segment extraction
    raw_segments = merge_candidate_frames(features, fps, cfg.cv)
    print(f"  Raw segments: {len(raw_segments)}")

    segment_features_list: List[SegmentFeatures] = []
    for idx, (s, e) in enumerate(raw_segments, start=1):
        seg_id = f"seg_{idx:04d}"
        sf = compute_segment_features(seg_id, s, e, fps, features, cfg.cv)
        label, score, reason = classify_segment(sf, cfg.cv)
        sf.label = label
        sf.score = score
        sf.reason = reason
        segment_features_list.append(sf)

    save_segment_csv(segment_features_list, out_dir / "segments_all.csv")

    # Classification summary
    n_fail = sum(1 for s in segment_features_list if s.label == "cv_fail")
    n_intent = sum(1 for s in segment_features_list if s.label == "likely_intentional")
    n_vlm = sum(1 for s in segment_features_list if s.label == "needs_vlm")
    print(f"  Classification: cv_fail={n_fail}, likely_intentional={n_intent}, needs_vlm={n_vlm}")

    # Extract keyframes for VLM segments
    if cfg.vlm_all:
        vlm_segments = list(segment_features_list)
    else:
        vlm_segments = [
            s for s in segment_features_list
            if s.label == "needs_vlm" or (cfg.vlm.include_cv_fail and s.label == "cv_fail")
        ]
    frames_dir = out_dir / "vlm_payload" / "frames"
    extract_keyframes(video_path, vlm_segments, frames_dir)

    # Global CV metrics
    global_cv = compute_global_cv_metrics(features, segment_features_list, fps, cfg.cv)

    # ------------------------------------------------------------------
    # Layer 2: VLM semantic judgment
    # ------------------------------------------------------------------
    verdicts: List[VLMVerdict] = []

    if cfg.skip_vlm:
        print("\n[Layer 2] VLM skipped (--skip-vlm)")
    elif not vlm_segments:
        print("\n[Layer 2] No segments to review – skipping VLM")
    else:
        print(f"\n[Layer 2] Sending {len(vlm_segments)} segments to VLM ({cfg.vlm.model}) ...")

        def vlm_progress(cur, total):
            print(f"  segment {cur}/{total}", end="\r", flush=True)

        try:
            verdicts = review_segments(
                vlm_segments,
                frames_dir,
                cfg.vlm,
                video_name=video_name,
                progress_callback=vlm_progress,
            )
            print(f"\n  VLM returned {len(verdicts)} verdicts")
            save_verdicts_jsonl(verdicts, out_dir / "vlm_verdicts.jsonl")
        except Exception as exc:
            print(f"\n  VLM error: {exc}")
            print("  Continuing with CV-only scoring ...")

    # ------------------------------------------------------------------
    # Layer 3: Fusion scoring
    # ------------------------------------------------------------------
    print(f"\n[Layer 3] Computing fusion scores ...")

    report = compute_report(
        video_name=video_name,
        global_cv=global_cv,
        segments=segment_features_list,
        verdicts=verdicts,
        fusion_cfg=cfg.fusion,
    )

    save_report_json(report, out_dir / "report.json")
    print_report(report)

    return {
        "video": video_name,
        "overall_score": report.overall_score,
        "overall_passed": report.overall_passed,
        "output_dir": str(out_dir),
    }


# =====================================================================
# Batch entry
# =====================================================================

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    cfg = build_config(args)

    results: List[dict] = []
    for video_path in args.inputs:
        if not video_path.exists():
            print(f"WARNING: {video_path} not found, skipping.", file=sys.stderr)
            continue
        result = evaluate_video(video_path, cfg)
        results.append(result)

    # Summary for batch mode
    if len(results) > 1:
        print(f"\n{'='*60}")
        print(f"  BATCH SUMMARY ({len(results)} videos)")
        print(f"{'='*60}")
        for r in results:
            status = "PASS" if r["overall_passed"] else "FAIL"
            print(f"  [{status}] {r['overall_score']:.2f}  {r['video']}")
        avg = sum(r["overall_score"] for r in results) / len(results)
        print(f"\n  Average score: {avg:.2f}")

    # Save batch summary
    if results:
        summary_path = cfg.output_dir / "batch_summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
