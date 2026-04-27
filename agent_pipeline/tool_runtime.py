"""
Restricted file tools for Manim repair loops.

All paths are resolved under a single run_dir root; attempts to escape raise errors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    message: str
    payload: Dict[str, Any]


def _normalize_rel_path(raw: str) -> str:
    s = (raw or "").strip().replace("\\", "/")
    if not s or s.startswith("/"):
        raise ValueError("path must be relative to run_dir")
    parts = []
    for segment in s.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            raise ValueError("path must not contain '..'")
        parts.append(segment)
    return "/".join(parts) if parts else ""


def _safe_join(root: Path, rel: str) -> Path:
    rel_norm = _normalize_rel_path(rel)
    if not rel_norm:
        raise ValueError("empty path")
    candidate = (root / rel_norm).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("path escapes run_dir") from exc
    return candidate


class ManimToolRuntime:
    """Execute read_file / search_file / apply_patch only under run_dir."""

    def __init__(self, run_dir: Path) -> None:
        self._root = run_dir.resolve()

    @property
    def root(self) -> Path:
        return self._root

    def read_file(
        self,
        path: str,
        *,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        max_chars: int = 120_000,
    ) -> ToolResult:
        try:
            target = _safe_join(self._root, path)
            if not target.is_file():
                return ToolResult(False, f"not a file: {path}", {"path": path})
            raw = target.read_text(encoding="utf-8")
            line_list = raw.splitlines(keepends=True)
            if start_line is not None or end_line is not None:
                s = max(1, int(start_line or 1))
                e = min(len(line_list), int(end_line or len(line_list)))
                if s > len(line_list):
                    text = ""
                else:
                    text = "".join(line_list[s - 1 : e])
            else:
                text = raw
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n... [truncated, max_chars={max_chars}]"
            nlines = len(raw.splitlines()) if raw else 0
            return ToolResult(
                True,
                "ok",
                {
                    "path": path,
                    "content": text,
                    "total_lines": nlines,
                },
            )
        except Exception as exc:
            return ToolResult(False, str(exc), {"path": path})

    def search_file(
        self,
        path: str,
        pattern: str,
        *,
        use_regex: bool = False,
        max_matches: int = 50,
        context_lines: int = 2,
    ) -> ToolResult:
        try:
            target = _safe_join(self._root, path)
            if not target.is_file():
                return ToolResult(False, f"not a file: {path}", {"path": path})
            text = target.read_text(encoding="utf-8")
            lines = text.splitlines()
            matches: List[Dict[str, Any]] = []
            if use_regex:
                rx = re.compile(pattern)
                for i, line in enumerate(lines, start=1):
                    if rx.search(line):
                        lo = max(1, i - context_lines)
                        hi = min(len(lines), i + context_lines)
                        ctx = "\n".join(
                            f"{j}: {lines[j - 1]}" for j in range(lo, hi + 1)
                        )
                        matches.append({"line": i, "text": line, "context": ctx})
                        if len(matches) >= max_matches:
                            break
            else:
                needle = pattern
                for i, line in enumerate(lines, start=1):
                    if needle in line:
                        lo = max(1, i - context_lines)
                        hi = min(len(lines), i + context_lines)
                        ctx = "\n".join(
                            f"{j}: {lines[j - 1]}" for j in range(lo, hi + 1)
                        )
                        matches.append({"line": i, "text": line, "context": ctx})
                        if len(matches) >= max_matches:
                            break
            return ToolResult(
                True,
                "ok",
                {"path": path, "match_count": len(matches), "matches": matches},
            )
        except Exception as exc:
            return ToolResult(False, str(exc), {"path": path, "pattern": pattern})

    def apply_patch(
        self,
        path: str,
        old_text: str,
        new_text: str,
        *,
        max_patch_bytes: int = 256_000,
    ) -> ToolResult:
        try:
            if len(old_text.encode("utf-8")) > max_patch_bytes:
                return ToolResult(
                    False,
                    "old_text exceeds max_patch_bytes",
                    {"path": path},
                )
            if len(new_text.encode("utf-8")) > max_patch_bytes:
                return ToolResult(
                    False,
                    "new_text exceeds max_patch_bytes",
                    {"path": path},
                )
            target = _safe_join(self._root, path)
            if not target.is_file():
                return ToolResult(False, f"not a file: {path}", {"path": path})
            content = target.read_text(encoding="utf-8")
            count = content.count(old_text)
            if count == 0:
                return ToolResult(
                    False,
                    "old_text not found (exact match required)",
                    {"path": path, "occurrences": 0},
                )
            if count > 1:
                return ToolResult(
                    False,
                    f"old_text is ambiguous: {count} occurrences (include more context)",
                    {"path": path, "occurrences": count},
                )
            updated = content.replace(old_text, new_text, 1)
            if len(updated.encode("utf-8")) > max_patch_bytes * 4:
                return ToolResult(
                    False,
                    "resulting file too large",
                    {"path": path},
                )
            target.write_text(updated, encoding="utf-8")
            return ToolResult(
                True,
                "patched",
                {
                    "path": path,
                    "bytes_written": len(updated.encode("utf-8")),
                },
            )
        except Exception as exc:
            return ToolResult(False, str(exc), {"path": path})

    def dispatch(self, name: str, arguments: Dict[str, Any]) -> ToolResult:
        if name == "read_file":
            return self.read_file(
                str(arguments.get("path", "")),
                start_line=arguments.get("start_line"),
                end_line=arguments.get("end_line"),
            )
        if name == "search_file":
            return self.search_file(
                str(arguments.get("path", "")),
                str(arguments.get("pattern", "")),
                use_regex=bool(arguments.get("use_regex", False)),
                max_matches=int(arguments.get("max_matches", 50)),
            )
        if name == "apply_patch":
            return self.apply_patch(
                str(arguments.get("path", "")),
                str(arguments.get("old_text", "")),
                str(arguments.get("new_text", "")),
                max_patch_bytes=int(arguments.get("max_patch_bytes", 256_000)),
            )
        if name == "finish_repair":
            return ToolResult(
                True,
                "finish",
                {
                    "fallback_required": bool(arguments.get("fallback_required", False)),
                    "summary": str(arguments.get("summary", "")),
                },
            )
        return ToolResult(False, f"unknown tool: {name}", {})


def build_openai_tool_schemas(max_patch_bytes: int) -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read a UTF-8 text file under the run directory. "
                    "Path is relative to run_dir (e.g. scene_pack.py)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {
                            "type": "integer",
                            "description": "1-based start line (optional)",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "1-based end line inclusive (optional)",
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_file",
                "description": (
                    "Search for a substring or regex in one file under run_dir; "
                    "returns matching line numbers and small context."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "pattern": {"type": "string"},
                        "use_regex": {"type": "boolean", "default": False},
                        "max_matches": {"type": "integer", "default": 50},
                    },
                    "required": ["path", "pattern"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "apply_patch",
                "description": (
                    "Replace exactly one occurrence of old_text with new_text in the file. "
                    "old_text must match exactly once. Only run_dir files."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                        "max_patch_bytes": {
                            "type": "integer",
                            "default": max_patch_bytes,
                            "description": "Safety cap for old/new chunk size",
                        },
                    },
                    "required": ["path", "old_text", "new_text"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "finish_repair",
                "description": (
                    "Call when done patching: set fallback_required=true if the issue "
                    "cannot be fixed with small edits and full-file repair is needed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fallback_required": {"type": "boolean"},
                        "summary": {"type": "string"},
                    },
                    "required": ["fallback_required"],
                    "additionalProperties": False,
                },
            },
        },
    ]
