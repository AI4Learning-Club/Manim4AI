"""Incremental buffering and readiness detection for streaming Scene Pack code."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from .scene_pack import SectionReadinessSpec, inspect_section_readiness


@dataclass(frozen=True)
class ScenePackStreamSnapshot:
    buffer: str
    parseable_prefix: str
    all_reports: list[SectionReadinessSpec]
    newly_ready: list[SectionReadinessSpec]


class ScenePackStreamBuffer:
    """Collect code deltas and emit newly ready sections from the parseable prefix."""

    def __init__(self) -> None:
        self._buffer = ""
        self._submitted_segment_ids: set[str] = set()

    @property
    def buffer(self) -> str:
        return self._buffer

    @property
    def ready_segment_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._submitted_segment_ids))

    def mark_submitted(self, segment_id: str) -> None:
        normalized = str(segment_id or "").strip()
        if not normalized:
            return
        self._submitted_segment_ids.add(normalized)

    def append(self, delta: str) -> ScenePackStreamSnapshot:
        if delta:
            self._buffer += delta

        parseable_prefix = extract_parseable_prefix(self._buffer)
        reports = inspect_section_readiness(parseable_prefix) if parseable_prefix.strip() else []
        newly_ready: list[SectionReadinessSpec] = []
        for report in reports:
            segment_id = report.segment.segment_id
            if not report.ready or segment_id in self._submitted_segment_ids:
                continue
            newly_ready.append(report)

        newly_ready.sort(key=lambda item: item.segment.order)
        return ScenePackStreamSnapshot(
            buffer=self._buffer,
            parseable_prefix=parseable_prefix,
            all_reports=reports,
            newly_ready=newly_ready,
        )


def extract_parseable_prefix(code: str) -> str:
    """Return the longest line-based prefix that can be parsed as Python."""
    sanitized = sanitize_streaming_code(code)
    if not sanitized.strip():
        return ""
    if _syntax_ok(sanitized):
        return sanitized

    lines = sanitized.splitlines()
    if not lines:
        return ""

    # Walk backward to find the longest parseable line prefix. This is simple
    # but robust for the first streaming pass, where syntax errors are usually
    # caused by an incomplete tail while earlier sections are already closed.
    for end in range(len(lines) - 1, 0, -1):
        candidate = "\n".join(lines[:end]).rstrip()
        if not candidate:
            continue
        if _syntax_ok(candidate):
            return candidate + "\n"
    return ""


def sanitize_streaming_code(code: str) -> str:
    if not code:
        return ""

    lines = code.splitlines()
    cleaned: list[str] = []
    seen_restart = False
    started = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped in {"```python", "```"}:
            continue

        # If the model restarts from the top and emits a second full code block,
        # keep the first stream and drop the restarted tail so prefix parsing can
        # still progress on the useful portion.
        if stripped.startswith("from manim import *"):
            if started:
                seen_restart = True
                break
            started = True

        cleaned.append(raw_line)

    out = "\n".join(cleaned).strip()
    if out:
        out += "\n"
    if seen_restart:
        return _sanitize_bad_spoken_quotes(out)
    return _sanitize_bad_spoken_quotes(out)


def _sanitize_bad_spoken_quotes(code: str) -> str:
    had_trailing_newline = code.endswith("\n")
    lines = code.splitlines()
    sanitized_lines: list[str] = []
    for line in lines:
        if (
            "self.speak(" not in line
            and "self.speak_with_subtitle(" not in line
            and "self.show_page_title_chip(" not in line
        ):
            sanitized_lines.append(line)
            continue

        call_markers = (
            "self.speak(",
            "self.speak_with_subtitle(",
            "self.show_page_title_chip(",
        )
        call_start = min(
            index for index in (line.find(marker) for marker in call_markers) if index >= 0
        )
        open_paren = line.find("(", call_start)
        if open_paren < 0:
            sanitized_lines.append(line)
            continue

        quote_start = -1
        quote_char = ""
        for index in range(open_paren + 1, len(line)):
            if line[index] in {'"', "'"}:
                quote_start = index
                quote_char = line[index]
                break
        if quote_start < 0:
            sanitized_lines.append(line)
            continue

        comma_index = line.find(",", quote_start + 1)
        close_paren_index = line.find(")", quote_start + 1)
        candidate_end = comma_index if comma_index >= 0 else close_paren_index
        if candidate_end is None or candidate_end < 0:
            sanitized_lines.append(line)
            continue

        quote_end = line.rfind(quote_char, quote_start + 1, candidate_end)
        if quote_end <= quote_start:
            sanitized_lines.append(line)
            continue

        inner = line[quote_start + 1:quote_end]
        escaped_inner_chars: list[str] = []
        for index, char in enumerate(inner):
            if char == quote_char and (index == 0 or inner[index - 1] != "\\"):
                escaped_inner_chars.append("\\")
            escaped_inner_chars.append(char)
        escaped_inner = "".join(escaped_inner_chars)
        sanitized_lines.append(
            line[:quote_start + 1] + escaped_inner + line[quote_end:]
        )
    result = "\n".join(sanitized_lines)
    if had_trailing_newline:
        result += "\n"
    return result


def _syntax_ok(code: str) -> bool:
    try:
        ast.parse(code)
    except SyntaxError:
        return False
    return True


__all__ = [
    "ScenePackStreamSnapshot",
    "ScenePackStreamBuffer",
    "extract_parseable_prefix",
    "sanitize_streaming_code",
]
