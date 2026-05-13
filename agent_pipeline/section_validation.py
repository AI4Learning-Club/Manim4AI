"""Shared validation result schema and helpers for section-local checks."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


_SEVERITY_ORDER = {
    "error": 0,
    "warning": 1,
    "info": 2,
}


@dataclass(frozen=True)
class ValidationIssue:
    tool: str
    severity: str
    category: str
    message: str
    evidence: str = ""
    fix_hint: str = ""
    line: int | None = None
    symbol: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ValidationIssue":
        line_raw = raw.get("line")
        line = int(line_raw) if isinstance(line_raw, int) or (isinstance(line_raw, str) and line_raw.isdigit()) else None
        severity = str(raw.get("severity") or "error").strip().lower() or "error"
        if severity not in _SEVERITY_ORDER:
            severity = "error"
        return cls(
            tool=str(raw.get("tool") or "").strip(),
            severity=severity,
            category=str(raw.get("category") or "").strip(),
            message=str(raw.get("message") or "").strip(),
            evidence=str(raw.get("evidence") or "").strip(),
            fix_hint=str(raw.get("fix_hint") or "").strip(),
            line=line,
            symbol=str(raw.get("symbol") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationReport:
    passed: bool
    summary: str
    issues: tuple[ValidationIssue, ...]

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ValidationReport":
        issues_raw = raw.get("issues")
        issues = tuple(
            issue if isinstance(issue, ValidationIssue) else ValidationIssue.from_dict(issue)
            for issue in issues_raw
        ) if isinstance(issues_raw, list | tuple) else ()
        passed_raw = raw.get("passed")
        if isinstance(passed_raw, bool):
            passed = passed_raw
        else:
            passed = not has_blocking_issues(issues)
        summary = str(raw.get("summary") or "").strip() or _default_summary(issues, passed)
        return cls(passed=passed, summary=summary, issues=issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def normalize_validation_issue(issue: ValidationIssue | Mapping[str, Any]) -> ValidationIssue:
    if isinstance(issue, ValidationIssue):
        return issue
    return ValidationIssue.from_dict(issue)


def normalize_validation_report(report: ValidationReport | Mapping[str, Any] | None) -> ValidationReport:
    if isinstance(report, ValidationReport):
        return report
    if report is None:
        return ValidationReport(passed=True, summary="No validation issues.", issues=())
    return ValidationReport.from_dict(report)


def merge_validation_reports(
    *reports: ValidationReport | Mapping[str, Any] | None,
) -> ValidationReport:
    normalized = [normalize_validation_report(report) for report in reports if report is not None]
    merged_issues: list[ValidationIssue] = []
    for report in normalized:
        merged_issues.extend(report.issues)
    merged_issues.sort(key=_issue_sort_key)
    passed = not has_blocking_issues(merged_issues)
    summary = _default_summary(merged_issues, passed)
    return ValidationReport(
        passed=passed,
        summary=summary,
        issues=tuple(merged_issues),
    )


def normalize_code_eval_report(report: Mapping[str, Any] | None) -> ValidationReport:
    if report is None:
        return ValidationReport(passed=True, summary="No validation issues.", issues=())

    issues_raw = report.get("issues")
    issues_list = issues_raw if isinstance(issues_raw, list) else []
    normalized_issues: list[ValidationIssue] = []
    for issue in issues_list:
        if not isinstance(issue, Mapping):
            continue
        severity = str(issue.get("severity") or "error").strip().lower() or "error"
        if severity not in _SEVERITY_ORDER:
            severity = "error"
        rule_id = str(issue.get("rule_id") or "structural").strip() or "structural"
        line_raw = issue.get("line")
        line = int(line_raw) if isinstance(line_raw, int) or (isinstance(line_raw, str) and line_raw.isdigit()) else None
        object_name = str(issue.get("object_name") or "").strip()
        body_name = str(issue.get("body_name") or "").strip()
        symbol = object_name or body_name
        normalized_issues.append(
            ValidationIssue(
                tool="code_eval",
                severity=severity,
                category=rule_id,
                message=str(issue.get("message") or "").strip(),
                evidence=str(issue.get("evidence") or "").strip(),
                fix_hint=str(issue.get("fix_hint") or "").strip(),
                line=line,
                symbol=symbol,
            )
        )

    passed_raw = report.get("passed")
    if normalized_issues:
        passed = False
    elif isinstance(passed_raw, bool):
        passed = passed_raw
    else:
        passed = True
    summary = str(report.get("summary") or "").strip() or _default_summary(normalized_issues, passed)
    return ValidationReport(
        passed=passed,
        summary=summary,
        issues=tuple(sorted(normalized_issues, key=_issue_sort_key)),
    )


def has_blocking_issues(
    report_or_issues: ValidationReport | Iterable[ValidationIssue | Mapping[str, Any]] | Mapping[str, Any] | None,
) -> bool:
    if report_or_issues is None:
        return False
    if isinstance(report_or_issues, ValidationReport):
        issues = report_or_issues.issues
    elif isinstance(report_or_issues, Mapping):
        issues = normalize_validation_report(report_or_issues).issues
    else:
        issues = tuple(normalize_validation_issue(issue) for issue in report_or_issues)
    return any(issue.severity == "error" for issue in issues)


def format_validation_report(report: ValidationReport | Mapping[str, Any] | None) -> str:
    normalized = normalize_validation_report(report)
    if not normalized.issues:
        return normalized.summary
    lines = [normalized.summary]
    for issue in normalized.issues:
        parts = [f"[{issue.tool or 'validation'}:{issue.severity}]"]
        if issue.category:
            parts.append(issue.category)
        if issue.line is not None:
            parts.append(f"line {issue.line}")
        if issue.symbol:
            parts.append(issue.symbol)
        if issue.message:
            parts.append(issue.message)
        if issue.evidence:
            parts.append(f"Evidence: {issue.evidence}")
        if issue.fix_hint:
            parts.append(f"Fix: {issue.fix_hint}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def validate_streaming_scene_file(
    *,
    code_eval_agent: Any,
    scene_file: Path,
    attempt: int = 0,
) -> ValidationReport:
    from .lint_error import lint_section_code

    code = scene_file.read_text(encoding="utf-8")
    lint_raw = lint_section_code(code, filename=scene_file.name)
    code_eval_raw = code_eval_agent.review(code)

    lint_report = normalize_validation_report(lint_raw)
    normalized_code_eval = normalize_code_eval_report(code_eval_raw)
    merged = merge_validation_reports(lint_report, normalized_code_eval)

    validation_dir = _section_validation_dir(scene_file)
    _write_json_debug(validation_dir / f"lint_report_{attempt}.json", lint_report.to_dict())
    _write_json_debug(validation_dir / f"code_eval_report_{attempt}.json", normalized_code_eval.to_dict())
    _write_json_debug(validation_dir / f"merged_report_{attempt}.json", merged.to_dict())

    return merged


def validate_and_fix_streaming_scene_file(
    *,
    code_eval_agent: Any,
    agent: Any,
    scene_file: Path,
    output_language: str,
    max_attempts: int,
    event_callback: Any = None,
    segment_id: str = "",
    scene_name: str = "",
    order: int | None = None,
) -> dict[str, Any]:
    validation_dir = _section_validation_dir(scene_file)
    error = ""
    _write_text_debug(
        validation_dir / "scene_file_source_0.py",
        scene_file.read_text(encoding="utf-8"),
    )
    report = validate_streaming_scene_file(
        code_eval_agent=code_eval_agent,
        scene_file=scene_file,
        attempt=0,
    )

    validation_attempts = 0
    while has_blocking_issues(report) and validation_attempts < max_attempts:
        validation_attempts += 1
        try:
            if event_callback is not None:
                event_callback(
                    {
                        "kind": "section_fix_started",
                        "segment_id": segment_id,
                        "scene_name": scene_name,
                        "segment_order": order,
                        "attempt": validation_attempts,
                    }
                )

            current_code = scene_file.read_text(encoding="utf-8")
            _write_text_debug(
                validation_dir / f"scene_file_before_fix_{validation_attempts}.py",
                current_code,
            )
            updated_code = _repair_scene_file_method_from_validation(
                agent=agent,
                scene_file=scene_file,
                segment_id=segment_id,
                validation_report=report,
                output_language=output_language,
            )
            if not isinstance(updated_code, str) or not updated_code.strip():
                raise ValueError("Scene-file validation fix did not return updated file content.")
            scene_file.write_text(updated_code, encoding="utf-8")
            _write_text_debug(
                validation_dir / f"scene_file_after_fix_{validation_attempts}.py",
                updated_code,
            )
            report = validate_streaming_scene_file(
                code_eval_agent=code_eval_agent,
                scene_file=scene_file,
                attempt=validation_attempts,
            )
            _write_json_debug(
                validation_dir / f"fix_result_{validation_attempts}.json",
                {
                    "segment_id": segment_id,
                    "scene_file": str(scene_file),
                    "attempt": validation_attempts,
                    "passed": report.passed,
                    "summary": report.summary,
                    "patched": True,
                },
            )
            if event_callback is not None:
                event_callback(
                    {
                        "kind": "section_fix_completed",
                        "segment_id": segment_id,
                        "scene_name": scene_name,
                        "segment_order": order,
                        "attempt": validation_attempts,
                        "success": report.passed,
                        "validation_attempts": validation_attempts,
                        "validation_summary": report.summary,
                    }
                )
        except Exception as exc:
            error = str(exc)
            _write_json_debug(
                validation_dir / f"fix_result_{validation_attempts}.json",
                {
                    "segment_id": segment_id,
                    "scene_file": str(scene_file),
                    "attempt": validation_attempts,
                    "passed": False,
                    "patched": False,
                    "error": error,
                },
            )
            if event_callback is not None:
                event_callback(
                    {
                        "kind": "section_fix_completed",
                        "segment_id": segment_id,
                        "scene_name": scene_name,
                        "segment_order": order,
                        "attempt": validation_attempts,
                        "success": False,
                        "validation_attempts": validation_attempts,
                        "validation_summary": error,
                    }
                )
            break

    return {
        "passed": report.passed and not error,
        "scene_file": scene_file,
        "validation_report": report.to_dict(),
        "validation_attempts": validation_attempts,
        "error": error,
    }


def build_render_failure_validation_report(
    *,
    segment_id: str,
    scene_name: str,
    error_log: str,
) -> ValidationReport:
    lowered = error_log.lower()
    category = "render_runtime"
    message = "Manim render failed while rendering this section."
    fix_hint = (
        "Repair the target section method so it renders successfully without changing "
        "the manifest, wrapper scene, or shared helpers."
    )
    if "latex" in lowered or "tex" in lowered or ".log" in lowered:
        category = "latex_runtime"
        message = "LaTeX compilation failed while rendering this section."
        fix_hint = (
            "Avoid invalid Tex/MathTex content, unsupported LaTeX commands, and malformed "
            "math strings in this section method."
        )
        if "missing $ inserted" in lowered or "add_labels" in lowered:
            fix_hint += (
                " In Manim Community v0.20.1, do not pass nontrivial math notation as a raw string into "
                "`add_labels(...)`; construct those labels with `MathTex(...)` explicitly."
            )
    elif "object has no attribute 'grid'" in lowered or "get_grid" in lowered:
        category = "unsupported_manim_api"
        message = "Section uses a Manim API that is unavailable in the target runtime."
        fix_hint = (
            "Target Manim Community v0.20.1 APIs only. Do not call `Axes.get_grid()`; use `NumberPlane(...)` "
            "for a background grid, or style the axes/ticks directly."
        )
    elif "audio track" in lowered:
        category = "audio_runtime"
        message = "Rendered segment is missing the expected audio track."
        fix_hint = "Ensure narration calls and output audio wiring remain valid for this section."
    elif "video not found" in lowered:
        category = "render_output_missing"
        message = "Manim finished but the segment video artifact was not found."
        fix_hint = "Keep the target section renderable and ensure it produces the expected scene output."

    evidence_lines = [line.rstrip() for line in error_log.splitlines() if line.strip()]
    evidence = "\n".join(evidence_lines[-20:]).strip()
    symbol = scene_name.strip() or segment_id.strip()
    issue = ValidationIssue(
        tool="render_runtime",
        severity="error",
        category=category,
        message=message,
        evidence=evidence,
        fix_hint=fix_hint,
        symbol=symbol,
    )
    return ValidationReport(
        passed=False,
        summary=_default_summary((issue,), passed=False),
        issues=(issue,),
    )


def repair_streaming_scene_file_method(
    *,
    agent: Any,
    scene_file: Path,
    segment_id: str,
    validation_report: ValidationReport | Mapping[str, Any],
    output_language: str,
) -> str:
    normalized_report = normalize_validation_report(validation_report)
    return _repair_scene_file_method_from_validation(
        agent=agent,
        scene_file=scene_file,
        segment_id=segment_id,
        validation_report=normalized_report,
        output_language=output_language,
    )


def _repair_scene_file_method_from_validation(
    *,
    agent: Any,
    scene_file: Path,
    segment_id: str,
    validation_report: ValidationReport,
    output_language: str,
) -> str:
    if not segment_id:
        raise ValueError("segment_id is required for validation-driven method repair.")

    from .scene_pack import build_segment_repair_context, replace_method_source

    code = scene_file.read_text(encoding="utf-8")
    context = build_segment_repair_context(code, segment_id)
    updated_method_source = agent.fix_segment_method_from_validation(
        segment_id=context.segment.segment_id,
        method_name=context.section_method.method_name,
        manifest_source=context.manifest_source,
        wrapper_scene_source=context.wrapper_scene_source,
        section_method_source=context.section_method.source,
        helper_method_sources=[helper.source for helper in context.helper_methods],
        validation_report=validation_report.to_dict(),
        output_language=output_language,
    )
    return replace_method_source(
        code,
        owner_class=context.section_owner_class,
        method_name=context.section_method.method_name,
        new_method_source=updated_method_source,
    )


def _issue_sort_key(issue: ValidationIssue) -> tuple[int, int, str, str, str]:
    return (
        _SEVERITY_ORDER.get(issue.severity, 99),
        issue.line if issue.line is not None else 10**9,
        issue.tool,
        issue.category,
        issue.message,
    )


def _default_summary(issues: Iterable[ValidationIssue], passed: bool) -> str:
    issue_list = list(issues)
    if not issue_list:
        return "No validation issues."
    error_count = sum(1 for issue in issue_list if issue.severity == "error")
    warning_count = sum(1 for issue in issue_list if issue.severity == "warning")
    info_count = sum(1 for issue in issue_list if issue.severity == "info")
    if passed:
        return (
            f"Validation passed with {len(issue_list)} non-blocking issue(s) "
            f"({warning_count} warning, {info_count} info)."
        )
    return (
        f"Validation failed with {len(issue_list)} issue(s): "
        f"{error_count} error, {warning_count} warning, {info_count} info."
    )


def _section_validation_dir(scene_file: Path) -> Path:
    return scene_file.parent / "validation" / scene_file.stem


def _write_json_debug(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text_debug(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


__all__ = [
    "ValidationIssue",
    "ValidationReport",
    "normalize_validation_issue",
    "normalize_validation_report",
    "normalize_code_eval_report",
    "merge_validation_reports",
    "has_blocking_issues",
    "format_validation_report",
    "validate_streaming_scene_file",
    "validate_and_fix_streaming_scene_file",
    "build_render_failure_validation_report",
    "repair_streaming_scene_file_method",
]
