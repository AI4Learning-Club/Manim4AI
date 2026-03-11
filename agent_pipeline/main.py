"""
Multi-agent Manim generation-evaluation loop.

Flow:
  1. CodeGen agent produces Manim code from a student request.
  2. Renderer invokes Manim.  On failure the CodeGen agent fixes the code
     and the renderer retries once.
  3. The eval pipeline scores the rendered video (CV + VLM).
  4. If the video fails QA, the CodeGen agent revises the code using the
     evaluation feedback AND keyframe screenshots, and steps 2-3 repeat once.
  5. The best round (highest score) is selected as the final output.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from .code_gen import CodeGenAgent
from .renderer import RenderResult, render_scene
from .evaluator import evaluate, collect_keyframes
from .teaching_planner import TeachingPlannerAgent
from .tts import generate_narration, has_audio_stream, merge_audio_video

# =====================================================================
# Configuration constants (override via .env or process env)
# =====================================================================

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

API_KEY = os.environ.get("OPENAI_API_KEY", "")
BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
MANIM_QUALITY = "-qm --fps 60"
RUNS_DIR = ROOT_DIR / "runs"

# =====================================================================
# Helpers
# =====================================================================

def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def _detect_chinese_in_mathtex(code: str) -> str:
    """Scan code for Chinese chars inside MathTex/Tex and return a warning."""
    import re
    issues = []
    for m in re.finditer(r'(MathTex|Tex)\s*\(', code):
        start = m.end()
        depth = 1
        i = start
        while i < len(code) and depth > 0:
            if code[i] == '(':
                depth += 1
            elif code[i] == ')':
                depth -= 1
            i += 1
        fragment = code[start:i]
        chinese = re.findall(r'[\u4e00-\u9fff]+', fragment)
        if chinese:
            issues.append(f"  Found Chinese '{','.join(chinese)}' inside {m.group(1)}() near: ...{fragment[:80]}...")
    if issues:
        return "\n\nAUTO-DETECTED ISSUES (fix these first!):\n" + "\n".join(issues)
    return ""


def _check_python_syntax(code: str) -> Optional[str]:
    """Return a readable syntax error string, or None if code parses."""
    try:
        ast.parse(code)
        return None
    except SyntaxError as exc:
        line = ""
        if exc.lineno and 1 <= exc.lineno <= len(code.splitlines()):
            line = code.splitlines()[exc.lineno - 1]
        pointer = ""
        if exc.offset and line:
            pointer = " " * max(exc.offset - 1, 0) + "^"
        return (
            f"{exc.__class__.__name__}: {exc.msg}\n"
            f"line {exc.lineno}, column {exc.offset}\n"
            f"{line}\n{pointer}"
        )


def _repair_syntax_before_render(
    agent: CodeGenAgent,
    code: str,
    round_dir: Path,
    label: str,
    max_attempts: int = 2,
) -> tuple[str, Optional[str]]:
    """Fix Python syntax errors before calling Manim."""
    syntax_error = _check_python_syntax(code)
    attempt = 0
    while syntax_error and attempt < max_attempts:
        attempt += 1
        _log(f"{label}: Python syntax invalid before render — asking LLM to fix (attempt {attempt}) ...")
        extra_hint = (
            "\n\nThis is a Python syntax failure, not a Manim layout issue.\n"
            "Fix the code so it parses first. Pay special attention to:\n"
            "- unterminated string literals\n"
            "- broken multiline Chinese strings\n"
            "- missing closing brackets or parentheses\n"
            "- truncated code near the end of the file\n"
            "- leaving every `self.speak(...)` / `self.speak_with_subtitle(...)` string on one logical Python string literal\n"
        )
        code = agent.fix(code, syntax_error + extra_hint)
        (round_dir / f"scene_syntax_fixed_{attempt}.py").write_text(code, encoding="utf-8")
        syntax_error = _check_python_syntax(code)
    return code, syntax_error


def _try_render(
    agent: CodeGenAgent,
    code: str,
    round_dir: Path,
    label: str,
) -> tuple[str, RenderResult]:
    """Render *code*; on failure ask the agent to fix once and retry."""
    code, syntax_error = _repair_syntax_before_render(agent, code, round_dir, label)
    if syntax_error:
        _log(f"{label}: syntax fix failed before render")
        return code, RenderResult(success=False, error_log=syntax_error, scene_name="")

    _log(f"{label}: rendering ...")
    result = render_scene(code, round_dir, quality_flags=MANIM_QUALITY)

    if not result.success:
        _log(f"{label}: render FAILED — asking LLM to fix ...")
        extra_hint = _detect_chinese_in_mathtex(code)
        error_info = result.error_log + extra_hint
        code = agent.fix(code, error_info)
        (round_dir / "scene_fixed.py").write_text(code, encoding="utf-8")
        code, syntax_error = _repair_syntax_before_render(agent, code, round_dir, label)
        if syntax_error:
            _log(f"{label}: post-fix syntax still invalid")
            return code, RenderResult(success=False, error_log=syntax_error, scene_name="")
        result = render_scene(code, round_dir, quality_flags=MANIM_QUALITY)
        if result.success:
            _log(f"{label}: fix succeeded, render OK")
        else:
            _log(f"{label}: fix also failed")
    else:
        _log(f"{label}: render OK")

    return code, result


def _try_eval(
    video_path: Path,
    eval_dir: Path,
    label: str,
) -> Optional[Dict]:
    """Run the eval pipeline.  Returns the report dict or None on error."""
    _log(f"{label}: evaluating video ...")
    try:
        report = evaluate(
            video_path,
            eval_dir,
            api_key=API_KEY,
            base_url=BASE_URL,
            model=MODEL,
        )
        score = report.get("overall_score", 0)
        passed = report.get("overall_passed", False)
        status = "PASS" if passed else "FAIL"
        _log(f"{label}: score={score:.2f} [{status}]")
        return report
    except Exception as exc:
        _log(f"{label}: evaluation error — {exc}")
        return None


def _round_info(n: int, render: RenderResult, report: Optional[Dict]) -> Dict:
    return {
        "round": n,
        "render_success": render.success,
        "video": str(render.video_path) if render.video_path else None,
        "eval_score": report.get("overall_score") if report else None,
        "eval_passed": report.get("overall_passed") if report else None,
    }


def _find_keyframes(eval_dir: Path) -> List[Path]:
    """Collect keyframe images from an eval output dir."""
    kf = collect_keyframes(eval_dir)
    if kf:
        return kf
    for sub in eval_dir.rglob("*.jpg"):
        kf.append(sub)
    return sorted(kf)


# =====================================================================
# Main pipeline
# =====================================================================

def run_pipeline(
    request_text: str,
    image_path: Optional[Path] = None,
    run_dir: Optional[Path] = None,
) -> Dict:
    """Execute the full generate-render-evaluate-improve loop."""

    if run_dir is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = RUNS_DIR / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "request.txt").write_text(request_text, encoding="utf-8")
    if image_path and image_path.exists():
        shutil.copy2(str(image_path), str(run_dir / f"request_image{image_path.suffix}"))

    _log("Teaching planner: building lesson structure ...")
    planner = TeachingPlannerAgent(api_key=API_KEY, base_url=BASE_URL, model=MODEL)
    agent = CodeGenAgent(api_key=API_KEY, base_url=BASE_URL, model=MODEL)
    teaching_plan: Dict[str, Any] = planner.plan(request_text, image_path)
    _log(f"Teaching planner: {len(teaching_plan.get('sections', []))} section(s) ready")

    teaching_plan_path = run_dir / "teaching_plan.json"
    teaching_plan_path.write_text(
        json.dumps(teaching_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary: Dict = {
        "request": request_text,
        "image": str(image_path) if image_path else None,
        "teaching_plan_file": str(teaching_plan_path),
        "rounds": [],
        "final_video": None,
        "final_score": None,
        "final_passed": None,
    }

    # Keep track of (score, video_path, round_number) for best-of selection
    candidates: list[tuple[float, Optional[str], int]] = []

    # ==================================================================
    # Round 1
    # ==================================================================
    _log("Round 1: generating Manim code ...")
    code = agent.generate(request_text, image_path, teaching_plan=teaching_plan)

    r1_dir = run_dir / "round1"
    code, r1_render = _try_render(agent, code, r1_dir, "Round 1")

    r1_report: Optional[Dict] = None
    r1_eval_dir = r1_dir / "eval"
    if r1_render.success and r1_render.video_path:
        r1_report = _try_eval(r1_render.video_path, r1_eval_dir, "Round 1")

    summary["rounds"].append(_round_info(1, r1_render, r1_report))
    if r1_report:
        candidates.append((
            r1_report.get("overall_score", 0),
            str(r1_render.video_path) if r1_render.video_path else None,
            1,
        ))

    # Early exit if Round 1 passes
    if r1_report and r1_report.get("overall_passed"):
        _log("Round 1 PASSED — done!")
        summary["final_video"] = str(r1_render.video_path)
        summary["final_score"] = r1_report["overall_score"]
        summary["final_passed"] = True
        _add_tts(agent, code, request_text, run_dir, summary)
        _save_summary(run_dir, summary)
        return summary

    # ==================================================================
    # Round 2 (improvement with visual feedback)
    # ==================================================================
    if not r1_render.success:
        _log("Round 1 render failed completely — skipping Round 2")
        summary["final_passed"] = False
        _save_summary(run_dir, summary)
        return summary

    _log("Round 2: improving code with evaluation feedback + keyframe screenshots ...")

    # Gather keyframe images so the LLM can *see* what went wrong
    keyframes = _find_keyframes(r1_eval_dir)
    if keyframes:
        _log(f"  Sending {len(keyframes)} keyframe(s) to LLM for visual feedback")

    feedback = r1_report or {"issues": [], "dimensions": [], "overall_score": 0}
    code = agent.improve(
        code,
        feedback,
        keyframe_paths=keyframes or None,
        teaching_plan=teaching_plan,
    )

    r2_dir = run_dir / "round2"
    code, r2_render = _try_render(agent, code, r2_dir, "Round 2")

    r2_report: Optional[Dict] = None
    r2_eval_dir = r2_dir / "eval"
    if r2_render.success and r2_render.video_path:
        r2_report = _try_eval(r2_render.video_path, r2_eval_dir, "Round 2")

    summary["rounds"].append(_round_info(2, r2_render, r2_report))
    if r2_report:
        candidates.append((
            r2_report.get("overall_score", 0),
            str(r2_render.video_path) if r2_render.video_path else None,
            2,
        ))

    # ==================================================================
    # Pick the best round
    # ==================================================================
    if candidates:
        candidates.sort(key=lambda t: t[0], reverse=True)
        best_score, best_video, best_round = candidates[0]
        summary["final_video"] = best_video
        summary["final_score"] = best_score
        summary["final_passed"] = any(c[0] >= 0.65 for c in candidates)
        _log(f"Best result: Round {best_round} (score={best_score:.2f})")
    else:
        summary["final_passed"] = False

    _add_tts(agent, code, request_text, run_dir, summary)
    _log(f"Done — final score: {summary['final_score']}, passed: {summary['final_passed']}")
    _save_summary(run_dir, summary)
    return summary


def _add_tts(
    agent: CodeGenAgent,
    code: str,
    request_text: str,
    run_dir: Path,
    summary: Dict,
) -> None:
    """Generate fallback narration and merge it when the final video is silent."""
    if not summary.get("final_video"):
        return
    video_path = Path(summary["final_video"])
    if not video_path.exists():
        return
    if has_audio_stream(video_path):
        _log("Final render already contains audio track — skipping fallback narration merge")
        return

    _log("Final render is silent — generating fallback narration with TTS ...")
    try:
        script = agent.narrate(code, request_text)
        if not script:
            _log("  Narration script empty — skipping TTS")
            return
        _log(f"  Narration script: {len(script)} paragraphs")

        audio_path = generate_narration(script, run_dir)
        if not audio_path:
            _log("  TTS audio generation failed")
            return
        _log(f"  Audio generated: {audio_path}")

        video_with_audio = run_dir / "final_with_audio.mp4"
        ok = merge_audio_video(video_path, audio_path, video_with_audio)
        if ok:
            summary["final_video_with_audio"] = str(video_with_audio)
            _log(f"  Video+audio merged: {video_with_audio}")
        else:
            _log("  Audio merge failed — delivering silent video")
    except Exception as exc:
        _log(f"  TTS error: {exc} — delivering silent video")


def _save_summary(run_dir: Path, summary: Dict) -> None:
    path = run_dir / "summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"Summary saved: {path}")


# =====================================================================
# CLI
# =====================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="agent_pipeline",
        description="Multi-agent Manim generation-evaluation loop",
    )
    p.add_argument("request", nargs="?", default=None, help="Student request text")
    p.add_argument("--image", type=Path, default=None, help="Optional input image")
    p.add_argument("--run-dir", type=Path, default=None, help="Custom output directory")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if not API_KEY:
        print("Error: OPENAI_API_KEY is not set. Put it in .env or export it in the shell and try again.")
        return 1

    if args.request is None and args.image is None:
        print("Usage: python -m agent_pipeline \"题目描述\" [--image img.png]")
        return 1

    request_text = args.request or "请根据图片中的题目生成动画讲解。"

    t0 = time.time()
    summary = run_pipeline(request_text, args.image, args.run_dir)
    elapsed = time.time() - t0

    print(f"\n{'='*60}")
    print(f"  Pipeline finished in {elapsed:.0f}s")
    print(f"  Rounds: {len(summary['rounds'])}")
    for r in summary["rounds"]:
        rn = r["round"]
        sc = r.get("eval_score")
        ps = r.get("eval_passed")
        vid = "YES" if r.get("render_success") else "NO"
        print(f"    Round {rn}: render={vid}, score={sc}, passed={ps}")
    print(f"  Final video:  {summary['final_video']}")
    audio_vid = summary.get('final_video_with_audio')
    if audio_vid:
        print(f"  With audio:   {audio_vid}")
    print(f"  Final score:  {summary['final_score']}")
    print(f"  Final passed: {summary['final_passed']}")
    print(f"{'='*60}")

    return 0 if summary.get("final_passed") else 1
