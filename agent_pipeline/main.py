"""
Single-round Manim generation pipeline.

Flow:
  1. CodeGen agent produces Round 1 Manim code from a student request.
  2. Round 1 renders with TTS enabled at final delivery quality.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from .asset_resolver import resolve_local_assets
from .code_eval import CodeEvalAgent
from .code_gen import CodeGenAgent
from .llm import resolve_pipeline_llm_configs, validate_pipeline_llm_configs
from .output_language import normalize_output_language, output_language_name
from .renderer import RenderResult, render_scene_pack
from .scene_pack import build_segment_repair_context, replace_method_source
from .teaching_planner import TeachingPlannerAgent
from .theme_resolver import resolve_theme
from .tts import has_audio_stream, voice_for_language

# =====================================================================
# Configuration constants (override via .env or process env)
# =====================================================================

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

MANIM_QUALITY = os.environ.get("MANIM_QUALITY", "-qm --fps 60")
ROUND1_MANIM_QUALITY = os.environ.get(
    "ROUND1_MANIM_QUALITY",
    os.environ.get("ROUND2_MANIM_QUALITY", MANIM_QUALITY),
)
RUNS_DIR = ROOT_DIR / "runs"
USE_LOCAL_ICONS = os.environ.get("A4L_USE_LOCAL_ICONS", "1").lower() not in {
    "0",
    "false",
    "no",
}
DEFAULT_OUTPUT_LANGUAGE = normalize_output_language(
    os.environ.get("A4L_VIDEO_LANGUAGE", "en")
)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


SYNTAX_FIX_MAX_ATTEMPTS = max(1, _int_env("A4L_SYNTAX_FIX_MAX_ATTEMPTS", 4))
RENDER_FIX_MAX_ATTEMPTS = max(1, _int_env("A4L_RENDER_FIX_MAX_ATTEMPTS", 4))
CODE_EVAL_FIX_MAX_ATTEMPTS = max(1, _int_env("A4L_CODE_EVAL_FIX_MAX_ATTEMPTS", 2))
LATEX_TEXT_FIX_MAX_ATTEMPTS = max(1, _int_env("A4L_LATEX_TEXT_FIX_MAX_ATTEMPTS", 2))

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
    for match in re.finditer(r"(MathTex|Tex)\s*\(", code):
        start = match.end()
        depth = 1
        i = start
        while i < len(code) and depth > 0:
            if code[i] == "(":
                depth += 1
            elif code[i] == ")":
                depth -= 1
            i += 1
        fragment = code[start:i]
        chinese = re.findall(r"[\u4e00-\u9fff]+", fragment)
        if chinese:
            issues.append(
                f"  Found Chinese '{','.join(chinese)}' inside "
                f"{match.group(1)}() near: ...{fragment[:80]}..."
            )
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
        lines = code.splitlines()
        if exc.lineno and 1 <= exc.lineno <= len(lines):
            line = lines[exc.lineno - 1]
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
    output_language: str,
    max_attempts: int = SYNTAX_FIX_MAX_ATTEMPTS,
) -> tuple[str, Optional[str], int]:
    """Fix Python syntax errors before calling Manim."""
    syntax_error = _check_python_syntax(code)
    attempt = 0
    while syntax_error and attempt < max_attempts:
        attempt += 1
        _log(f"{label}: Python syntax invalid before render - asking LLM to fix (attempt {attempt}) ...")
        extra_hint = (
            "\n\nThis is a Python syntax failure, not a Manim layout issue.\n"
            "Fix the code so it parses first. Pay special attention to:\n"
            "- unterminated string literals\n"
            "- broken multiline Chinese strings\n"
            "- missing closing brackets or parentheses\n"
            "- truncated code near the end of the file\n"
            "- leaving every `self.speak(...)` / `self.speak_with_subtitle(...)` string on one logical Python string literal\n"
        )
        code = agent.fix(code, syntax_error + extra_hint, output_language=output_language)
        (round_dir / f"scene_syntax_fixed_{attempt}.py").write_text(code, encoding="utf-8")
        syntax_error = _check_python_syntax(code)
    return code, syntax_error, attempt


_USER_FACING_TEXT_CALLS = {
    "MarkupText",
    "Paragraph",
    "Text",
    "Title",
    "get_muted_text",
    "get_secondary_text",
    "get_success_text",
    "get_text",
    "get_warning_text",
    "make_page_title",
    "make_subtitle_panel",
    "set_subtitle",
    "show_page_title_chip",
    "speak",
    "speak_with_subtitle",
}


def _call_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _iter_string_literals(node: ast.AST):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
        return
    if isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                yield value.value


_LATEX_TEXT_MARKER_RE = re.compile(
    r"(\\[A-Za-z]+|[_^]\{[^}]+\}|[A-Za-z0-9]+\s*_\s*\{[^}]+\})"
)


def _looks_like_inline_latex(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return False
    if not _LATEX_TEXT_MARKER_RE.search(cleaned):
        return False
    return any(ch in cleaned for ch in "\\{}_^")


def _collect_latex_in_user_facing_text(code: str) -> List[Dict[str, Any]]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    lines = code.splitlines()
    issues: list[Dict[str, Any]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node.func)
        if call_name not in _USER_FACING_TEXT_CALLS:
            continue

        for arg in node.args:
            for literal in _iter_string_literals(arg):
                snippet = re.sub(r"\s+", " ", literal).strip()
                if snippet and _looks_like_inline_latex(snippet):
                    lineno = int(getattr(node, "lineno", 0) or 0)
                    start = max(lineno - 2, 1)
                    end = min(lineno + 2, len(lines))
                    context = "\n".join(
                        f"{idx}: {lines[idx - 1]}"
                        for idx in range(start, end + 1)
                    )
                    issues.append({
                        "line": lineno,
                        "call_name": call_name,
                        "snippet": snippet[:120],
                        "source_line": lines[lineno - 1] if 1 <= lineno <= len(lines) else "",
                        "context": context,
                    })

        for keyword in node.keywords:
            for literal in _iter_string_literals(keyword.value):
                snippet = re.sub(r"\s+", " ", literal).strip()
                if snippet and _looks_like_inline_latex(snippet):
                    lineno = int(getattr(node, "lineno", 0) or 0)
                    start = max(lineno - 2, 1)
                    end = min(lineno + 2, len(lines))
                    context = "\n".join(
                        f"{idx}: {lines[idx - 1]}"
                        for idx in range(start, end + 1)
                    )
                    issues.append({
                        "line": lineno,
                        "call_name": call_name,
                        "snippet": snippet[:120],
                        "source_line": lines[lineno - 1] if 1 <= lineno <= len(lines) else "",
                        "context": context,
                    })

    deduped: list[Dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for issue in issues:
        key = (
            int(issue.get("line", 0) or 0),
            str(issue.get("call_name", "")),
            str(issue.get("snippet", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def _format_latex_in_user_facing_text_report(issues: List[Dict[str, Any]]) -> str:
    if not issues:
        return ""

    summary = [
        "\n\nAUTO-DETECTED LATEX IN PLAIN TEXT:",
        *[
            f"  line {int(issue.get('line', 0) or 0)}: {issue.get('call_name', '')} -> {issue.get('snippet', '')}"
            for issue in issues
        ],
    ]
    return "\n".join(summary)


def _write_latex_text_issue_details(
    round_dir: Path,
    attempt: int,
    issues: List[Dict[str, Any]],
) -> None:
    if not issues:
        return

    parts = ["AUTO-DETECTED LATEX IN PLAIN TEXT", ""]
    for idx, issue in enumerate(issues, start=1):
        parts.append(
            f"[{idx}] line {int(issue.get('line', 0) or 0)} | {issue.get('call_name', '')}"
        )
        parts.append(f"snippet: {issue.get('snippet', '')}")
        parts.append(f"source: {issue.get('source_line', '')}")
        parts.append("context:")
        parts.append(str(issue.get("context", "")))
        parts.append("")

    (round_dir / f"scene_latex_text_report_{attempt}.txt").write_text(
        "\n".join(parts).rstrip() + "\n",
        encoding="utf-8",
    )


def _repair_latex_in_user_facing_text_before_render(
    agent: CodeGenAgent,
    code: str,
    round_dir: Path,
    label: str,
    output_language: str,
    max_attempts: int = LATEX_TEXT_FIX_MAX_ATTEMPTS,
) -> tuple[str, str, int]:
    latex_issues = _collect_latex_in_user_facing_text(code)
    latex_report = _format_latex_in_user_facing_text_report(latex_issues)
    attempt = 0

    while latex_report and attempt < max_attempts:
        attempt += 1
        _write_latex_text_issue_details(round_dir, attempt, latex_issues)
        _log(
            f"{label}: found LaTeX fragments inside plain text - "
            f"asking LLM to repair (attempt {attempt}) ..."
        )
        extra_hint = (
            "\n\nThis is a text-vs-math rendering fix.\n"
            "Do NOT leave LaTeX fragments inside plain-text helpers such as "
            "`get_text`, `get_secondary_text`, `get_success_text`, `get_warning_text`, "
            "`make_page_title`, `make_subtitle_panel`, `set_subtitle`, `speak`, or "
            "`speak_with_subtitle`.\n"
            "If a sentence contains math, split it into natural-language text plus a "
            "separate `get_math(...)` / `get_highlighted_math(...)` object, then lay "
            "them out together.\n"
            "For narration or subtitle strings, rewrite the formula into natural language "
            "instead of keeping raw LaTeX commands.\n"
            "Do not change the lesson meaning.\n"
        )
        code = agent.fix(code, latex_report + extra_hint, output_language=output_language)
        (round_dir / f"scene_latex_text_fixed_{attempt}.py").write_text(code, encoding="utf-8")
        latex_issues = _collect_latex_in_user_facing_text(code)
        latex_report = _format_latex_in_user_facing_text_report(latex_issues)

    return code, latex_report, attempt
def _code_eval_issues(report: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not report:
        return []
    issues = report.get("issues")
    return issues if isinstance(issues, list) else []


def _code_eval_has_blockers(report: Optional[Dict[str, Any]]) -> bool:
    return any(
        str(issue.get("severity", "")).lower() == "error"
        for issue in _code_eval_issues(report)
        if isinstance(issue, dict)
    )


def _format_code_eval_report(report: Optional[Dict[str, Any]]) -> str:
    if not report:
        return "Pre-render code_eval failed without a structured report."
    lines: list[str] = []
    summary = str(report.get("summary", "")).strip()
    if summary:
        lines.append(summary)
    for issue in _code_eval_issues(report):
        if not isinstance(issue, dict):
            continue
        severity = str(issue.get("severity", "error")).upper()
        rule_id = str(issue.get("rule_id", "unknown")).strip()
        obj = str(issue.get("object_name", "")).strip()
        body = str(issue.get("body_name", "")).strip()
        line = issue.get("line")
        location = f"line {line}" if isinstance(line, int) else "line ?"
        subject = obj or body or "object"
        detail = str(issue.get("message", "")).strip()
        evidence = str(issue.get("evidence", "")).strip()
        fix_hint = str(issue.get("fix_hint", "")).strip()
        parts = [f"[{severity}] {rule_id} @ {location}: {subject}"]
        if detail:
            parts.append(detail)
        if evidence:
            parts.append(f"Evidence: {evidence}")
        if fix_hint:
            parts.append(f"Fix: {fix_hint}")
        lines.append(" | ".join(parts))
    return "\n".join(lines) or "Pre-render code_eval found unresolved structural issues."


def _repair_code_eval_before_render(
    code_eval_agent: CodeEvalAgent,
    agent: CodeGenAgent,
    code: str,
    round_dir: Path,
    label: str,
    output_language: str,
    max_attempts: int = CODE_EVAL_FIX_MAX_ATTEMPTS,
) -> tuple[str, Dict[str, Any], int]:
    try:
        report = code_eval_agent.review(code)
    except Exception as exc:
        _log(f"{label}: code_eval unavailable before render - {exc}")
        return code, {"passed": True, "summary": f"code_eval skipped: {exc}", "issues": []}, 0

    (round_dir / "scene_code_eval_report_0.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    attempt = 0
    while _code_eval_issues(report) and attempt < max_attempts:
        attempt += 1
        _log(
            f"{label}: pre-render code_eval found {len(_code_eval_issues(report))} issue(s) - "
            f"asking LLM to repair (attempt {attempt}) ..."
        )
        code = agent.fix_from_code_eval(code, report, output_language=output_language)
        (round_dir / f"scene_code_eval_fixed_{attempt}.py").write_text(code, encoding="utf-8")
        try:
            report = code_eval_agent.review(code)
        except Exception as exc:
            _log(f"{label}: code_eval recheck unavailable after repair - {exc}")
            report = {
                "passed": True,
                "summary": f"code_eval recheck skipped: {exc}",
                "issues": [],
            }
            break
        (round_dir / f"scene_code_eval_report_{attempt}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return code, report, attempt


def _repair_failed_segments_for_rerender(
    agent: CodeGenAgent,
    code: str,
    render_result: RenderResult,
    *,
    label: str,
    output_language: str,
) -> Optional[tuple[str, set[str]]]:
    failed_segments = [
        segment
        for segment in sorted(render_result.segments, key=lambda item: item.order)
        if not segment.success
    ]
    if not failed_segments:
        return None

    patched_code = code
    patched_segment_ids: set[str] = set()

    for segment in failed_segments:
        if not segment.error_log.strip():
            _log(f"{label}: skipping segment-aware repair for {segment.segment_id} because no segment-specific error log is available")
            return None

        try:
            context = build_segment_repair_context(patched_code, segment.segment_id)
            updated_method_source = agent.fix_segment_method(
                segment_id=context.segment.segment_id,
                method_name=context.section_method.method_name,
                manifest_source=context.manifest_source,
                wrapper_scene_source=context.wrapper_scene_source,
                section_method_source=context.section_method.source,
                helper_method_sources=[helper.source for helper in context.helper_methods],
                error_log=segment.error_log,
                output_language=output_language,
            )
            patched_code = replace_method_source(
                patched_code,
                owner_class=context.section_owner_class,
                method_name=context.section_method.method_name,
                new_method_source=updated_method_source,
            )
            patched_segment_ids.add(segment.segment_id)
        except Exception as exc:
            _log(
                f"{label}: segment-aware repair fallback to whole-file fix for "
                f"{segment.segment_id} ({segment.scene_name}) - {exc}"
            )
            return None

    if not patched_segment_ids:
        return None
    return patched_code, patched_segment_ids


def _try_render(
    code_eval_agent: CodeEvalAgent,
    agent: CodeGenAgent,
    code: str,
    round_dir: Path,
    label: str,
    *,
    quality_flags: str,
    enable_tts: bool,
    output_language: str,
) -> tuple[str, RenderResult, Dict[str, Any]]:
    """Render *code*; on failure keep repairing until retry budget is exhausted."""
    round_dir.mkdir(parents=True, exist_ok=True)
    result = RenderResult(success=False, error_log="", scene_name="")
    syntax_fix_rounds = 0
    latex_text_fix_rounds = 0
    code_eval_fix_rounds = 0
    segment_fix_rounds = 0
    tts_voice = voice_for_language(output_language)
    pending_segment_rerender_ids: Optional[set[str]] = None

    for attempt in range(RENDER_FIX_MAX_ATTEMPTS + 1):
        render_segment_ids = pending_segment_rerender_ids
        code, syntax_error, syntax_attempts = _repair_syntax_before_render(
            agent, code, round_dir, label, output_language=output_language
        )
        syntax_fix_rounds += syntax_attempts
        if syntax_attempts:
            render_segment_ids = None
        if syntax_error:
            _log(f"{label}: syntax fix failed before render")
            result = RenderResult(success=False, error_log=syntax_error, scene_name="")
        else:
            code, latex_text_error, latex_text_attempts = _repair_latex_in_user_facing_text_before_render(
                agent,
                code,
                round_dir,
                label,
                output_language=output_language,
            )
            latex_text_fix_rounds += latex_text_attempts
            if latex_text_attempts:
                render_segment_ids = None
                code, syntax_error, syntax_attempts = _repair_syntax_before_render(
                    agent, code, round_dir, label, output_language=output_language
                )
                syntax_fix_rounds += syntax_attempts
                if syntax_attempts:
                    render_segment_ids = None
            if syntax_error:
                _log(f"{label}: syntax fix failed after latex-text repair")
                result = RenderResult(success=False, error_log=syntax_error, scene_name="")
                continue
            if latex_text_error:
                _log(f"{label}: latex-in-plain-text fix did not converge before render")
                result = RenderResult(success=False, error_log=latex_text_error, scene_name="")
                continue

            code, code_eval_report, code_eval_attempts = _repair_code_eval_before_render(
                code_eval_agent,
                agent,
                code,
                round_dir,
                label,
                output_language=output_language,
            )
            code_eval_fix_rounds += code_eval_attempts
            if code_eval_attempts:
                render_segment_ids = None
            if code_eval_attempts:
                code, syntax_error, syntax_attempts = _repair_syntax_before_render(
                    agent, code, round_dir, label, output_language=output_language
                )
                syntax_fix_rounds += syntax_attempts
                if syntax_attempts:
                    render_segment_ids = None
                if syntax_error:
                    _log(f"{label}: syntax fix failed after code_eval repair")
                    result = RenderResult(success=False, error_log=syntax_error, scene_name="")
                    continue

                code, latex_text_error, latex_text_attempts = _repair_latex_in_user_facing_text_before_render(
                    agent,
                    code,
                    round_dir,
                    label,
                    output_language=output_language,
                )
                latex_text_fix_rounds += latex_text_attempts
                if latex_text_attempts:
                    render_segment_ids = None
                    code, syntax_error, syntax_attempts = _repair_syntax_before_render(
                        agent, code, round_dir, label, output_language=output_language
                    )
                    syntax_fix_rounds += syntax_attempts
                    if syntax_attempts:
                        render_segment_ids = None
                if syntax_error:
                    _log(f"{label}: syntax fix failed after code_eval latex-text repair")
                    result = RenderResult(success=False, error_log=syntax_error, scene_name="")
                    continue
                if latex_text_error:
                    _log(f"{label}: latex-in-plain-text fix did not converge after code_eval repair")
                    result = RenderResult(success=False, error_log=latex_text_error, scene_name="")
                    continue

            if _code_eval_has_blockers(code_eval_report):
                _log(f"{label}: unresolved code_eval blockers remain before render")
                result = RenderResult(
                    success=False,
                    error_log=_format_code_eval_report(code_eval_report),
                    scene_name="",
                )
                continue
            if _code_eval_issues(code_eval_report):
                _log(
                    f"{label}: code_eval left {len(_code_eval_issues(code_eval_report))} "
                    "warning issue(s); continuing to render"
                )

            render_msg = f"{label}: rendering"
            if attempt:
                render_msg += f" after fix {attempt}"
            if render_segment_ids:
                render_msg += f" (segments only: {', '.join(sorted(render_segment_ids))})"
            _log(render_msg + " ...")
            result = render_scene_pack(
                code,
                round_dir,
                quality_flags=quality_flags,
                enable_tts=enable_tts,
                tts_voice=tts_voice,
                selected_segment_ids=render_segment_ids,
            )
            if result.success:
                if attempt:
                    _log(f"{label}: fix {attempt} succeeded, render OK")
                else:
                    _log(f"{label}: render OK")
                return code, result, {
                    "syntax_fix_rounds": syntax_fix_rounds,
                    "latex_text_fix_rounds": latex_text_fix_rounds,
                    "code_eval_fix_rounds": code_eval_fix_rounds,
                    "segment_fix_rounds": segment_fix_rounds,
                    "render_fix_rounds": attempt,
                    "total_fix_rounds": (
                        syntax_fix_rounds
                        + latex_text_fix_rounds
                        + code_eval_fix_rounds
                        + segment_fix_rounds
                        + attempt
                    ),
                }

        if attempt >= RENDER_FIX_MAX_ATTEMPTS:
            _log(f"{label}: render still failed after {attempt} fix attempt(s)")
            failed_segments = _failed_segment_labels(result)
            if failed_segments:
                _log(f"{label}: failed segments: {', '.join(failed_segments)}")
            if result.error_log.strip():
                _log(f"{label}: latest render error:\n{result.error_log.strip()}")
            break

        segment_fixed = _repair_failed_segments_for_rerender(
            agent,
            code,
            result,
            label=label,
            output_language=output_language,
        )
        if segment_fixed is not None:
            code, rerender_segment_ids = segment_fixed
            pending_segment_rerender_ids = rerender_segment_ids
            segment_fix_rounds += 1
            (round_dir / f"scene_segment_fixed_{attempt + 1}.py").write_text(code, encoding="utf-8")
            _log(
                f"{label}: segment-aware repair updated "
                f"{', '.join(sorted(rerender_segment_ids))}; rerendering only those segment(s)"
            )
            continue

        _log(
            f"{label}: render FAILED - asking LLM to fix "
            f"(attempt {attempt + 1}/{RENDER_FIX_MAX_ATTEMPTS}) ..."
        )
        failed_segments = _failed_segment_labels(result)
        if failed_segments:
            _log(f"{label}: failed segments: {', '.join(failed_segments)}")
        if result.error_log.strip():
            _log(f"{label}: render error details:\n{result.error_log.strip()}")
        error_info = result.error_log + _detect_chinese_in_mathtex(code)
        code = agent.fix(code, error_info, output_language=output_language)
        pending_segment_rerender_ids = None
        (round_dir / f"scene_fixed_{attempt + 1}.py").write_text(code, encoding="utf-8")

    return code, result, {
        "syntax_fix_rounds": syntax_fix_rounds,
        "latex_text_fix_rounds": latex_text_fix_rounds,
        "code_eval_fix_rounds": code_eval_fix_rounds,
        "segment_fix_rounds": segment_fix_rounds,
        "render_fix_rounds": RENDER_FIX_MAX_ATTEMPTS,
        "total_fix_rounds": (
            syntax_fix_rounds
            + latex_text_fix_rounds
            + code_eval_fix_rounds
            + segment_fix_rounds
            + RENDER_FIX_MAX_ATTEMPTS
        ),
    }


def _segment_infos(render: RenderResult) -> List[Dict[str, Any]]:
    infos: List[Dict[str, Any]] = []
    for segment in sorted(render.segments, key=lambda item: item.order):
        info: Dict[str, Any] = {
            "order": segment.order,
            "segment_id": segment.segment_id,
            "scene_name": segment.scene_name,
            "success": segment.success,
            "video": str(segment.video_path) if segment.video_path else None,
            "output_dir": str(segment.output_dir),
        }
        if segment.error_log:
            info["error"] = segment.error_log[-1500:]
        infos.append(info)
    return infos


def _failed_segment_labels(render: RenderResult) -> List[str]:
    return [
        f"{segment.order:02d}:{segment.segment_id}({segment.scene_name})"
        for segment in sorted(render.segments, key=lambda item: item.order)
        if not segment.success
    ]


def _round_info(n: int, render: RenderResult, report: Optional[Dict]) -> Dict:
    info = {
        "round": n,
        "render_success": render.success,
        "video": str(render.video_path) if render.video_path else None,
        "eval_score": report.get("overall_score") if report else None,
        "eval_passed": report.get("overall_passed") if report else None,
        "segments": _segment_infos(render),
    }
    if render.error_log:
        info["render_warning" if render.success else "render_error"] = render.error_log[-1500:]
    return info


def _build_eval_meta(
    *,
    render_at_1: Optional[bool],
    render_at_final: Optional[bool],
    repair_rounds: int,
    time_total_sec: Optional[float],
    time_per_stage: Dict[str, float],
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "render_at_1": render_at_1,
        "render_at_final": render_at_final,
        "time_total_sec": time_total_sec,
        "time_per_stage": time_per_stage or None,
    }
    if repair_rounds > 0:
        meta["repair_rounds"] = repair_rounds
        meta["fix_rate"] = 1.0 if render_at_final else 0.0
    return meta


# =====================================================================
# Main pipeline
# =====================================================================


def run_pipeline(
    request_text: str,
    image_path: Optional[Path] = None,
    run_dir: Optional[Path] = None,
    language: Optional[str] = None,
) -> Dict:
    """Execute the single-round generate-render pipeline."""
    stage_times: Dict[str, float] = {}
    output_language = normalize_output_language(language, DEFAULT_OUTPUT_LANGUAGE)

    if run_dir is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = RUNS_DIR / ts
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "request.txt").write_text(request_text, encoding="utf-8")
    if image_path and image_path.exists():
        shutil.copy2(str(image_path), str(run_dir / f"request_image{image_path.suffix}"))

    llm_configs = resolve_pipeline_llm_configs()
    validate_pipeline_llm_configs(llm_configs)
    analysis_llm = llm_configs["analysis"]
    code_llm = llm_configs["code"]
    _log(
        "LLM routing: "
        f"analysis={analysis_llm.provider}/{analysis_llm.model}, "
        f"code={code_llm.provider}/{code_llm.model}"
    )
    _log(f"Output language: {output_language_name(output_language)} ({output_language})")

    llm_routing_path = run_dir / "llm_routing.json"
    llm_routing_path.write_text(
        json.dumps(
            {
                "analysis": analysis_llm.summary(),
                "code": code_llm.summary(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    _log("Teaching planner: building lesson structure ...")
    stage_started_at = time.time()
    planner = TeachingPlannerAgent(analysis_llm)
    agent = CodeGenAgent(code_llm)
    code_eval_agent = CodeEvalAgent(analysis_llm)
    teaching_plan: Dict[str, Any] = planner.plan(request_text, image_path)
    stage_times["planning"] = time.time() - stage_started_at
    _log(f"Teaching planner: {len(teaching_plan.get('sections', []))} section(s) ready")

    _log("Theme resolver: selecting lesson theme ...")
    stage_started_at = time.time()
    selected_theme = resolve_theme(request_text, teaching_plan)
    stage_times["theme_selection"] = time.time() - stage_started_at
    teaching_plan["selected_theme"] = selected_theme
    _log(
        "Theme resolver: selected "
        f"{selected_theme['theme_id']} ({selected_theme['display_name']})"
    )

    assets_info: Dict[str, Any] = {
        "enabled": USE_LOCAL_ICONS,
        "icon_dir": str((ROOT_DIR / "icon").resolve()),
        "available_icon_count": 0,
        "selected_assets": [],
    }
    if USE_LOCAL_ICONS:
        _log("Asset resolver: selecting local icons ...")
        stage_started_at = time.time()
        try:
            assets_info = resolve_local_assets(
                request_text,
                teaching_plan,
                llm_config=analysis_llm,
            )
            selected_assets = assets_info.get("selected_assets", [])
            teaching_plan["selected_assets"] = selected_assets
            stage_times["asset_selection"] = time.time() - stage_started_at
            _log(f"Asset resolver: selected {len(selected_assets)} icon(s)")
        except Exception as exc:
            stage_times["asset_selection"] = time.time() - stage_started_at
            teaching_plan["selected_assets"] = []
            _log(f"Asset resolver: skipped due to error - {exc}")
    else:
        teaching_plan["selected_assets"] = []

    selected_assets_path = run_dir / "selected_assets.json"
    selected_assets_path.write_text(
        json.dumps(assets_info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    selected_theme_path = run_dir / "selected_theme.json"
    selected_theme_path.write_text(
        json.dumps(selected_theme, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    teaching_plan_path = run_dir / "teaching_plan.json"
    teaching_plan_path.write_text(
        json.dumps(teaching_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary: Dict[str, Any] = {
        "request": request_text,
        "output_language": output_language,
        "image": str(image_path) if image_path else None,
        "teaching_plan_file": str(teaching_plan_path),
        "selected_theme_file": str(selected_theme_path),
        "selected_theme_id": selected_theme["theme_id"],
        "selected_theme_reason": selected_theme.get("reason"),
        "selected_assets_file": str(selected_assets_path),
        "selected_assets_count": len(teaching_plan.get("selected_assets", [])),
        "llm_routing_file": str(llm_routing_path),
        "analysis_llm": analysis_llm.summary(),
        "code_llm": code_llm.summary(),
        "rounds": [],
        "final_video": None,
        "final_video_with_audio": None,
        "final_score": None,
        "final_passed": None,
    }

    # ==================================================================
    # Round 1
    # ==================================================================
    _log("Round 1: generating Manim code ...")
    stage_started_at = time.time()
    code = agent.generate(
        request_text,
        image_path,
        teaching_plan=teaching_plan,
        output_language=output_language,
    )
    stage_times["round1_codegen"] = time.time() - stage_started_at

    r1_dir = run_dir / "round1"
    stage_started_at = time.time()
    code, r1_render, r1_fix_stats = _try_render(
        code_eval_agent,
        agent,
        code,
        r1_dir,
        "Round 1",
        quality_flags=ROUND1_MANIM_QUALITY,
        enable_tts=True,
        output_language=output_language,
    )
    stage_times["round1_render"] = time.time() - stage_started_at

    summary["rounds"].append(_round_info(1, r1_render, None))
    if r1_render.success and r1_render.video_path:
        summary["final_video"] = str(r1_render.video_path)
        if has_audio_stream(r1_render.video_path):
            summary["final_video_with_audio"] = str(r1_render.video_path)
        summary["final_passed"] = True
        _log("Final delivery: Round 1 video ready")
    else:
        summary["final_passed"] = False

    _log(f"Done - final score: {summary['final_score']}, passed: {summary['final_passed']}")
    _save_summary(run_dir, summary)
    return summary


def _save_summary(run_dir: Path, summary: Dict[str, Any]) -> None:
    path = run_dir / "summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"Summary saved: {path}")


# =====================================================================
# CLI
# =====================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent_pipeline",
        description="Single-round Manim generation pipeline",
    )
    parser.add_argument("request", nargs="?", default=None, help="Student request text")
    parser.add_argument("--image", type=Path, default=None, help="Optional input image")
    parser.add_argument("--run-dir", type=Path, default=None, help="Custom output directory")
    parser.add_argument(
        "--language",
        type=str,
        default=DEFAULT_OUTPUT_LANGUAGE,
        help="Output language for the video: en or zh",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        validate_pipeline_llm_configs(resolve_pipeline_llm_configs())
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return 1

    if args.request is None and args.image is None:
        print('Usage: python -m agent_pipeline "request text" [--image img.png]')
        return 1

    request_text = args.request or "Generate an animation lesson from the image."

    t0 = time.time()
    summary = run_pipeline(
        request_text,
        args.image,
        args.run_dir,
        language=args.language,
    )
    elapsed = time.time() - t0

    print(f"\n{'=' * 60}")
    print(f"  Pipeline finished in {elapsed:.0f}s")
    print(f"  Rounds: {len(summary['rounds'])}")
    for round_info in summary["rounds"]:
        round_number = round_info["round"]
        score = round_info.get("eval_score")
        passed = round_info.get("eval_passed")
        rendered = "YES" if round_info.get("render_success") else "NO"
        print(f"    Round {round_number}: render={rendered}, score={score}, passed={passed}")
    print(f"  Final video:  {summary['final_video']}")
    print(f"  Language:     {summary['output_language']}")
    audio_video = summary.get("final_video_with_audio")
    if audio_video:
        print(f"  With audio:   {audio_video}")
    print(f"  Final score:  {summary['final_score']}")
    print(f"  Final passed: {summary['final_passed']}")
    print(f"{'=' * 60}")

    return 0 if summary.get("final_video") else 1
