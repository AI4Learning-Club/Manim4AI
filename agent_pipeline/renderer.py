"""
Manim rendering wrapper for Scene Pack code.

Writes generated code to a single ``scene.py`` file, renders each segment scene
declared in ``SCENE_MANIFEST`` as its own Manim subprocess, and concatenates
the segment videos into one final ``video.mp4``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Any, Callable, List, Optional

from plugins.manim.runtime_config import get_manim_settings

from .scene_pack import (
    SegmentSpec,
    build_segment_scene_source,
    extract_speak_texts_for_segment,
    parse_scene_pack,
)
from .tts import (
    SCENE_TTS_RATE,
    _agent_debug_ndjson,
    configure_scene_tts,
    generate_audio,
    get_global_tts_cache_dir,
    has_audio_stream,
    merge_narration_mp3_clips,
    scene_tts_global_cache_path,
    scene_tts_round_cache_path,
    voice_for_language,
)

TTS_MAX_WORKERS = max(1, get_manim_settings().tts_workers)
BACKEND_ROOT = Path(__file__).resolve().parents[3]
STREAMING_REUSE_ONLY_SEGMENT_ID = "__streaming_reuse_only__"


def _render_progress(
    cb: Optional[Callable[[str], None]],
    msg: str,
) -> None:
    if cb is not None:
        cb(msg)


def _render_event(
    cb: Optional[Callable[[dict[str, Any]], None]],
    payload: dict[str, Any],
) -> None:
    if cb is not None:
        cb(payload)


def _segment_tts_timeout_seconds(text_count: int) -> float:
    # Bound total section TTS wait so one stuck provider/account lease cannot leave
    # the whole Manim job at "running" forever.
    configured_job_timeout = float(getattr(get_manim_settings(), "job_timeout_seconds", 1800) or 1800)
    default_tts_timeout = min(300.0, max(30.0, 75.0 * max(1, int(text_count or 1))))
    return max(0.01, min(configured_job_timeout, default_tts_timeout))


def _resolved_manim_cli_config_path() -> Optional[Path]:
    raw = (get_manim_settings().manim_cli_config_file or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = BACKEND_ROOT / path
    return path if path.is_file() else None


@dataclass
class SegmentRenderResult:
    segment_id: str
    scene_name: str
    order: int
    output_dir: Path
    success: bool
    video_path: Optional[Path] = None
    error_log: str = ""
    render_attempts: int = 1
    render_repair_rounds: int = 0


@dataclass
class RenderResult:
    success: bool
    video_path: Optional[Path] = None
    error_log: str = ""
    scene_name: str = ""
    segments: List[SegmentRenderResult] = field(default_factory=list)


@dataclass
class SegmentTTSPreparationResult:
    segment_id: str
    scene_name: str
    texts: List[str]
    ok: bool
    generated_count: int = 0
    reused_round_count: int = 0
    reused_global_count: int = 0
    failed_texts: List[str] = field(default_factory=list)


def _sanitize_chinese_in_latex(code: str) -> str:
    """Auto-fix Chinese / CJK characters inside MathTex/Tex raw strings.

    Covers single-quoted, double-quoted, and triple-quoted raw strings as well
    as multi-argument calls like ``MathTex(r"a", r"中文", r"b")``.  The
    extended CJK range includes CJK Unified Ideographs, Extension A,
    full-width punctuation, and common CJK symbols.
    """

    # Extended CJK detection: basic block + Extension A + full-width
    # punctuation + CJK symbols & punctuation + half/full-width forms.
    _CJK_RE = re.compile(
        r"[\u3000-\u303f"   # CJK symbols & punctuation (，。、；：？！…)
        r"\u4e00-\u9fff"    # CJK Unified Ideographs
        r"\u3400-\u4dbf"    # CJK Extension A
        r"\uf900-\ufaff"    # CJK Compatibility Ideographs
        r"\uff00-\uffef"    # Half/full-width forms (，．！？)
        r"]"
    )

    def _has_cjk(text: str) -> bool:
        return bool(_CJK_RE.search(text))

    def _clean_latex_content(raw_content: str) -> str:
        """Strip CJK characters from a LaTeX string, preserving math."""
        # Replace \text{CJK...}, \textbf{CJK...}, \textit{CJK...},
        # \mathrm{CJK...}, \operatorname{CJK...}, etc.
        cleaned = re.sub(
            r"\\(?:text|textbf|textit|textrm|textsf|texttt|mathrm|operatorname)"
            r"\{([^}]*" + _CJK_RE.pattern + r"[^}]*)\}",
            r"\\quad",
            raw_content,
        )
        # Remove remaining bare CJK characters (not inside braces).
        cleaned = _CJK_RE.sub(" ", cleaned)
        # Remove zero-width / control characters.
        cleaned = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff\ufffd]", " ", cleaned)
        cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", cleaned)
        # Collapse whitespace.
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            cleaned = r"\\quad"
        return cleaned

    # --- Locate all string arguments inside MathTex(...) / Tex(...) calls ---
    # We use AST parsing to find calls and identify string arguments, then
    # scan the source code from each argument's start offset to reliably
    # locate the string literal boundaries (avoids AST end_col_offset
    # inconsistencies across Python versions).
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Fallback: if the code doesn't parse, return as-is and let later
        # stages (syntax fix) handle it.
        return code

    # Collect (start_offset, end_offset, replacement) for each tainted string.
    replacements: list[tuple[int, int, str]] = []
    lines = code.splitlines(True)  # keep newlines for offset calculation

    # Build a cumulative line-start offset table.
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))

    def _scan_string_literal(start: int) -> tuple[int, int, str, str] | None:
        """Scan a Python string literal starting at *start* in *code*.

        Returns ``(literal_start, literal_end, prefix, quote)`` where
        ``code[literal_start:literal_end]`` is the full literal including
        prefix and quotes, or ``None`` if we cannot parse it.
        """
        i = start
        # Skip optional string prefix (r, b, u, f, or combinations).
        while i < len(code) and code[i] in "rRbBuUfF":
            i += 1
        if i >= len(code):
            return None
        prefix = code[start:i]
        # Determine quote style.
        if code[i:i + 3] in ('"""', "'''"):
            quote = code[i:i + 3]
        elif code[i] in ('"', "'"):
            quote = code[i]
        else:
            return None
        qlen = len(quote)
        j = i + qlen  # position after opening quote
        # Scan to closing quote.
        while j < len(code):
            if code[j] == "\\" and qlen == 1:
                j += 2  # skip escaped character
                continue
            if code[j:j + qlen] == quote:
                j += qlen  # include closing quote
                return start, j, prefix, quote
            j += 1
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match MathTex(...) / Tex(...) as Name or self.xxx (Attribute).
        func = node.func
        call_name = None
        if isinstance(func, ast.Name):
            call_name = func.id
        elif isinstance(func, ast.Attribute):
            call_name = func.attr
        if call_name not in ("MathTex", "Tex"):
            continue

        # Scan ALL positional and keyword string arguments.
        arg_nodes = list(node.args) + [kw.value for kw in node.keywords]
        for arg in arg_nodes:
            if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                continue
            raw_content = arg.value
            if not _has_cjk(raw_content):
                continue
            cleaned = _clean_latex_content(raw_content)
            if cleaned == raw_content:
                continue
            if not hasattr(arg, "lineno"):
                continue
            arg_start = line_offsets[arg.lineno - 1] + arg.col_offset
            span = _scan_string_literal(arg_start)
            if span is None:
                continue
            lit_start, lit_end, prefix, quote = span
            new_literal = f"{prefix}{quote}{cleaned}{quote}"
            replacements.append((lit_start, lit_end, new_literal))

    if not replacements:
        return code

    # Apply replacements in source order.
    replacements.sort(key=lambda r: r[0])
    parts: list[str] = []
    cursor = 0
    for start, end, replacement in replacements:
        parts.append(code[cursor:start])
        parts.append(replacement)
        cursor = end
    parts.append(code[cursor:])
    return "".join(parts)


def _pregenererate_tts(
    code: str,
    output_dir: Path,
    *,
    tts_voice: str | None = None,
    event_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> None:
    """Extract narration texts and pre-generate TTS audio once per round."""
    texts: list[str] = []
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"speak", "speak_with_subtitle"}:
                continue
            if not node.args:
                continue
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                texts.append(first_arg.value)
    except SyntaxError:
        texts = []

    if not texts:
        return

    try:
        cache_dir = output_dir / "tts_cache"
        global_cache_dir = get_global_tts_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        global_cache_dir.mkdir(parents=True, exist_ok=True)
        unique_texts = list(dict.fromkeys(texts))
        total = len(unique_texts)
        completed = 0
        _render_event(
            event_callback,
            {
                "kind": "tts_started",
                "tts_total": total,
                "tts_done": 0,
            },
        )

        def _ensure_tts(text: str) -> bool:
            voice = tts_voice or voice_for_language("en")
            round_fp = scene_tts_round_cache_path(text, cache_dir)
            global_fp = scene_tts_global_cache_path(
                text,
                voice,
                SCENE_TTS_RATE,
                global_cache_dir,
            )
            if round_fp.exists():
                return False
            if global_fp.exists():
                shutil.copy2(str(global_fp), str(round_fp))
                return False
            if not generate_audio(text, global_fp, voice=voice, rate=SCENE_TTS_RATE):
                return False
            shutil.copy2(str(global_fp), str(round_fp))
            return True

        generated = 0
        max_workers = min(TTS_MAX_WORKERS, len(unique_texts))
        # region agent log
        _agent_debug_ndjson(
            "H1",
            "renderer._pregenererate_tts",
            "tts_thread_pool",
            {
                "max_workers": max_workers,
                "n_unique_texts": len(unique_texts),
                "tts_max_workers_setting": TTS_MAX_WORKERS,
            },
        )
        # endregion
        if max_workers <= 1:
            for text in unique_texts:
                if _ensure_tts(text):
                    generated += 1
                completed += 1
                _render_event(
                    event_callback,
                    {
                        "kind": "tts_progress",
                        "tts_total": total,
                        "tts_done": completed,
                    },
                )
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_ensure_tts, text) for text in unique_texts]
                for future in as_completed(futures):
                    if future.result():
                        generated += 1
                    completed += 1
                    _render_event(
                        event_callback,
                        {
                            "kind": "tts_progress",
                            "tts_total": total,
                            "tts_done": completed,
                        },
                    )
        print(f"  Pre-generated {generated} TTS audio files")
    except Exception as exc:
        print(f"  TTS pre-generation warning: {exc}")


def prepare_segment_tts_assets(
    code: str,
    output_dir: Path,
    *,
    segment_id: str | None = None,
    tts_voice: str | None = None,
    event_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    generate_audio_fn: Callable[[str, Path, str, str], bool] = generate_audio,
    copy_file_fn: Callable[[str, str], Any] = shutil.copy2,
    path_exists_fn: Callable[[Path], bool] | None = None,
) -> SegmentTTSPreparationResult:
    """Prepare TTS assets for a single Scene Pack segment from full or minimal code."""
    resolved_exists = path_exists_fn or (lambda path: path.exists())
    scene_pack = parse_scene_pack(code)
    if not scene_pack.manifest:
        raise ValueError("Scene Pack does not contain any manifest segments.")

    if segment_id:
        segment = next((item for item in scene_pack.manifest if item.segment_id == segment_id), None)
        if segment is None:
            raise ValueError(f"Unknown segment id `{segment_id}`.")
    else:
        segment = scene_pack.manifest[0]

    texts = extract_speak_texts_for_segment(code, segment, scene_pack)
    unique_texts = list(dict.fromkeys(texts))
    voice = tts_voice or voice_for_language("en")
    cache_dir = output_dir / "tts_cache"
    global_cache_dir = get_global_tts_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    global_cache_dir.mkdir(parents=True, exist_ok=True)

    _render_event(
        event_callback,
        {
            "kind": "section_tts_started",
            "segment_id": segment.segment_id,
            "scene_name": segment.scene_name,
            "tts_total": len(unique_texts),
            "tts_done": 0,
        },
    )

    generated_count = 0
    reused_round_count = 0
    reused_global_count = 0
    failed_texts: list[str] = []

    def _prepare_one(text: str) -> tuple[str, str]:
        round_fp = scene_tts_round_cache_path(text, cache_dir)
        global_fp = scene_tts_global_cache_path(
            text,
            voice,
            SCENE_TTS_RATE,
            global_cache_dir,
        )
        if resolved_exists(round_fp):
            return (text, "reused_round")
        if resolved_exists(global_fp):
            copy_file_fn(str(global_fp), str(round_fp))
            return (text, "reused_global")
        if not generate_audio_fn(text, global_fp, voice, SCENE_TTS_RATE):
            return (text, "failed")
        copy_file_fn(str(global_fp), str(round_fp))
        return (text, "generated")

    max_workers = max(1, min(get_manim_settings().tts_process_threads, len(unique_texts) or 1))
    timeout_seconds = _segment_tts_timeout_seconds(len(unique_texts))
    if max_workers == 1 or len(unique_texts) <= 1:
        results = []
        for text in unique_texts:
            executor = ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(_prepare_one, text)
                done, _pending = wait([future], timeout=timeout_seconds, return_when=FIRST_COMPLETED)
                if not done:
                    future.cancel()
                    results.append((text, "failed"))
                    continue
                results.append(future.result())
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
    else:
        executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            future_map = {executor.submit(_prepare_one, text): idx for idx, text in enumerate(unique_texts)}
            ordered_results: list[tuple[int, tuple[str, str]]] = []
            deadline = time.monotonic() + timeout_seconds
            pending: set[Future[tuple[str, str]]] = set(future_map)
            while pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                done, pending = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
                for future in done:
                    ordered_results.append((future_map[future], future.result()))
            for future in pending:
                future.cancel()
                idx = future_map[future]
                ordered_results.append((idx, (unique_texts[idx], "failed")))
            ordered_results.sort(key=lambda item: item[0])
            results = [item[1] for item in ordered_results]
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    for text, status in results:
        if status == "reused_round":
            reused_round_count += 1
        elif status == "reused_global":
            reused_global_count += 1
        elif status == "generated":
            generated_count += 1
        else:
            failed_texts.append(text)

    ok = not failed_texts
    result = SegmentTTSPreparationResult(
        segment_id=segment.segment_id,
        scene_name=segment.scene_name,
        texts=unique_texts,
        ok=ok,
        generated_count=generated_count,
        reused_round_count=reused_round_count,
        reused_global_count=reused_global_count,
        failed_texts=failed_texts,
    )
    _render_event(
        event_callback,
        {
            "kind": "section_tts_completed",
            "segment_id": result.segment_id,
            "scene_name": result.scene_name,
            "tts_total": len(unique_texts),
            "tts_done": len(unique_texts) - len(failed_texts),
            "generated_count": generated_count,
            "reused_round_count": reused_round_count,
            "reused_global_count": reused_global_count,
            "failed_count": len(failed_texts),
            "success": ok,
        },
    )
    return result


def _collect_unique_speak_texts(code: str) -> list[str]:
    texts: list[str] = []
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"speak", "speak_with_subtitle"}:
                continue
            if not node.args:
                continue
            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                texts.append(first_arg.value)
    except SyntaxError:
        texts = []
    return list(dict.fromkeys(texts))


def _coerce_path(value: Any) -> Path:
    return value if isinstance(value, Path) else Path(str(value))


def _segment_results_from_dual(
    raw: list[dict[str, Any]],
    *,
    output_dir: Path,
) -> List[SegmentRenderResult]:
    out: List[SegmentRenderResult] = []
    for item in raw:
        if "error" in item:
            seg_id = str(item.get("segment_id") or "unknown")
            order = int(item.get("order", 0))
            seg_dir = output_dir / "segments" / f"{order:02d}_{seg_id}"
            out.append(
                SegmentRenderResult(
                    segment_id=seg_id,
                    scene_name="",
                    order=order,
                    output_dir=seg_dir,
                    success=False,
                    error_log=str(item["error"]),
                )
            )
            continue
        vd = item["result"]
        out.append(
            SegmentRenderResult(
                segment_id=str(vd["segment_id"]),
                scene_name=str(vd["scene_name"]),
                order=int(vd["order"]),
                output_dir=_coerce_path(vd["output_dir"]),
                success=bool(vd["success"]),
                video_path=_coerce_path(vd["video_path"]) if vd.get("video_path") else None,
                error_log=str(vd.get("error_log") or ""),
            )
        )
    return out


def _maybe_merge_narration_mp3(
    *,
    unique_texts: list[str],
    cache_dir: Path,
    output_dir: Path,
    enabled: bool,
) -> None:
    if not enabled or not unique_texts:
        return
    merge_dir = output_dir / "tts_merged"
    merge_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for text in unique_texts:
        fp = scene_tts_round_cache_path(text, cache_dir)
        if fp.exists():
            paths.append(fp)
    if paths:
        merge_narration_mp3_clips(paths, merge_dir / "narration_merged.mp3")


def _safe_print(text: str) -> None:
    import sys
    try:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    except Exception:
        pass


_TERMINAL_RENDER_FAILURE_PATTERNS = (
    "traceback (most recent call last):",
    "notimplementederror:",
    "valueerror:",
    "typeerror:",
    "runtimeerror:",
    "attributeerror:",
    "indexerror:",
    "keyerror:",
    "zerodivisionerror:",
    "syntaxerror:",
    "latex error",
)


def _contains_terminal_render_failure(output: str) -> bool:
    lowered = output.lower()
    return any(pattern in lowered for pattern in _TERMINAL_RENDER_FAILURE_PATTERNS)


def _finalize_subprocess_exit(
    process: subprocess.Popen[str],
    *,
    terminate_first: bool = False,
    terminate_grace_seconds: float = 10.0,
) -> int:
    if terminate_first and process.poll() is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        except Exception:
            try:
                process.kill()
            except ProcessLookupError:
                pass

    try:
        return process.wait(timeout=max(0.1, terminate_grace_seconds))
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return -9


def _echo_manim_render_to_console() -> bool:
    """Mirror stream to stdout (legacy); default off so AI/debug output stays clean."""
    raw = (os.environ.get("MANIM_STREAM_RENDER_LOG") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    return bool(get_manim_settings().stream_manim_render_to_console)


def _run_subprocess_streaming(
    cmd: list[str],
    cwd: Path,
    log_file: Path,
    env: dict[str, str] | None = None,
    *,
    timeout_seconds: float | None = 45 * 60,
    failure_idle_seconds: float = 3.0,
    echo_to_console: bool | None = None,
) -> tuple[int, str]:
    """Run a subprocess; capture combined stdout/stderr to ``log_file`` and return it.

    By default (``echo_to_console`` false) nothing is printed to stdout so Manim INFO/tqdm
    does not interleave with pipeline logs; set ``MANIM_STREAM_RENDER_LOG=1`` or
    ``stream_manim_render_to_console`` in settings to mirror to the console again.
    """
    echo = _echo_manim_render_to_console() if echo_to_console is None else echo_to_console
    if not echo:
        try:
            print(
                f"[manim] render log (not mixed with pipeline stdout): {log_file.resolve()}",
                file=sys.stderr,
                flush=True,
            )
        except Exception:
            pass
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    process = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    queue: Queue[Optional[str]] = Queue()
    output_chunks: list[str] = []
    last_output_at = time.monotonic()
    saw_terminal_failure = False

    def _reader() -> None:
        assert process.stdout is not None
        try:
            for line in iter(process.stdout.readline, ""):
                queue.put(line)
        finally:
            process.stdout.close()
            queue.put(None)

    reader = Thread(target=_reader, daemon=True)
    reader.start()

    reader_done = False
    log_file.parent.mkdir(parents=True, exist_ok=True)

    with log_file.open("w", encoding="utf-8") as handle:
        while True:
            try:
                chunk = queue.get(timeout=0.2)
            except Empty:
                if deadline is not None and time.monotonic() > deadline:
                    msg = "\n[manim subprocess exceeded timeout; terminating]\n"
                    handle.write(msg)
                    handle.flush()
                    output_chunks.append(msg)
                    break
                if (
                    saw_terminal_failure
                    and process.poll() is None
                    and time.monotonic() - last_output_at > max(0.1, failure_idle_seconds)
                ):
                    msg = "\n[manim subprocess emitted a terminal failure and became idle; terminating]\n"
                    handle.write(msg)
                    handle.flush()
                    output_chunks.append(msg)
                    break
                if process.poll() is not None and time.monotonic() - last_output_at > 0.5:
                    break
                if reader_done and process.poll() is not None:
                    break
                continue

            if chunk is None:
                reader_done = True
                if process.poll() is not None and queue.empty():
                    break
                continue

            if echo:
                _safe_print(chunk)
            handle.write(chunk)
            handle.flush()
            output_chunks.append(chunk)
            last_output_at = time.monotonic()
            if _contains_terminal_render_failure(chunk):
                saw_terminal_failure = True

        while not queue.empty():
            chunk = queue.get_nowait()
            if chunk is None:
                continue
            if echo:
                _safe_print(chunk)
            handle.write(chunk)
            handle.flush()
            output_chunks.append(chunk)
            last_output_at = time.monotonic()
            if _contains_terminal_render_failure(chunk):
                saw_terminal_failure = True

    returncode = _finalize_subprocess_exit(
        process,
        terminate_first=saw_terminal_failure and process.poll() is None,
    )
    return returncode, "".join(output_chunks)


def render_scene_pack(
    code: str,
    output_dir: Path,
    quality_flags: str = "-ql --fps 30",
    enable_tts: bool = True,
    tts_voice: str | None = None,
    selected_segment_ids: Optional[set[str]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
    event_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> RenderResult:
    """
    Render a Scene Pack from source code.

    Writes *code* to ``output_dir/scene.py``, renders the selected segment
    scenes declared in ``SCENE_MANIFEST`` in parallel, and concatenates the
    available segment videos into ``output_dir/video.mp4``.
    """
    code = _sanitize_chinese_in_latex(code)

    try:
        scene_pack = parse_scene_pack(code)
    except ValueError as exc:
        return RenderResult(
            success=False,
            error_log=str(exc),
            scene_name="ScenePack",
            segments=[],
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    segments_root = output_dir / "segments"
    full_render = not selected_segment_ids
    if full_render and segments_root.exists():
        shutil.rmtree(segments_root)
    segments_root.mkdir(parents=True, exist_ok=True)

    full_code = _build_scene_file_code(
        code,
        tts_voice=tts_voice or voice_for_language("en"),
        tts_rate=SCENE_TTS_RATE,
        tts_global_cache_dir=get_global_tts_cache_dir(),
    )
    scene_file = output_dir / "scene.py"
    scene_file.write_text(full_code, encoding="utf-8")

    manim_settings = get_manim_settings()
    use_dual = (
        enable_tts
        and manim_settings.dual_process_pipeline_enabled
        and not selected_segment_ids
        and bool(scene_pack.manifest)
    )

    segment_results: List[SegmentRenderResult] = []

    if enable_tts:
        voice = tts_voice or voice_for_language("en")
        configure_scene_tts(
            voice=voice,
            rate=SCENE_TTS_RATE,
            global_cache_dir=get_global_tts_cache_dir(),
        )
        if use_dual:
            from plugins.manim.agent_pipeline.concurrency_runtime import (
                run_dual_process_scene_pack_workers,
            )

            cache_dir = output_dir / "tts_cache"
            global_cache_dir = get_global_tts_cache_dir()
            cache_dir.mkdir(parents=True, exist_ok=True)
            global_cache_dir.mkdir(parents=True, exist_ok=True)
            unique_texts = _collect_unique_speak_texts(code)
            _render_event(
                event_callback,
                {
                    "kind": "tts_started",
                    "tts_total": len(unique_texts),
                    "tts_done": 0,
                },
            )
            tts_jobs: list[dict[str, Any]] = []
            for text in unique_texts:
                round_fp = scene_tts_round_cache_path(text, cache_dir)
                global_fp = scene_tts_global_cache_path(
                    text,
                    voice,
                    SCENE_TTS_RATE,
                    global_cache_dir,
                )
                tts_jobs.append(
                    {
                        "key": hashlib.md5(text.encode("utf-8")).hexdigest()[:16],
                        "text": text,
                        "global_fp": str(global_fp),
                        "round_fp": str(round_fp),
                        "voice": voice,
                        "rate": SCENE_TTS_RATE,
                    }
                )
            manim_payloads: list[dict[str, Any]] = []
            for segment in scene_pack.manifest:
                texts_seg = extract_speak_texts_for_segment(code, segment, scene_pack)
                manim_payloads.append(
                    {
                        "segment_id": segment.segment_id,
                        "scene_name": segment.scene_name,
                        "method_name": segment.method_name,
                        "order": segment.order,
                        "lineno": segment.lineno,
                        "scene_file": str(scene_file.resolve()),
                        "output_dir": str(output_dir.resolve()),
                        "quality_flags": quality_flags,
                        "should_render": True,
                        "required_texts": texts_seg,
                        "round_cache_dir": str(cache_dir.resolve()),
                    }
                )
            tts_results, manim_raw = run_dual_process_scene_pack_workers(
                tts_jobs=tts_jobs,
                manim_payloads=manim_payloads,
                event_callback=event_callback,
            )
            if tts_results and event_callback is None:
                ok_tts = sum(1 for _k, ok in tts_results if ok)
                _render_event(
                    event_callback,
                    {
                        "kind": "tts_progress",
                        "tts_total": len(tts_results),
                        "tts_done": len(tts_results),
                        "tts_ok": ok_tts,
                    },
                )
                _render_progress(
                    progress_callback,
                    f"TTS 批处理结果: {ok_tts}/{len(tts_results)} 条成功（失败会导致对应 segment 无法开始 Manim）",
                )
            segment_results = _segment_results_from_dual(manim_raw, output_dir=output_dir)
            _maybe_merge_narration_mp3(
                unique_texts=unique_texts,
                cache_dir=cache_dir,
                output_dir=output_dir,
                enabled=manim_settings.tts_merge_narration_enabled,
            )
        else:
            _render_progress(
                progress_callback,
                "单进程模式：先在同进程预生成 TTS，再并行/顺序渲染各 segment。",
            )
            _pregenererate_tts(code, output_dir, tts_voice=tts_voice, event_callback=event_callback)
            if manim_settings.tts_merge_narration_enabled:
                ut = _collect_unique_speak_texts(code)
                _maybe_merge_narration_mp3(
                    unique_texts=ut,
                    cache_dir=output_dir / "tts_cache",
                    output_dir=output_dir,
                    enabled=True,
                )

    if not segment_results:
        segment_results = _render_segments(
            manifest=scene_pack.manifest,
            scene_file=scene_file,
            output_dir=output_dir,
            quality_flags=quality_flags,
            selected_segment_ids=selected_segment_ids,
            progress_callback=progress_callback,
            event_callback=event_callback,
        )
    ordered_results = sorted(segment_results, key=lambda item: item.order)

    failed = [segment for segment in ordered_results if not segment.success]
    if failed:
        error_log = _format_segment_failures(failed)
        _write_round_render_log(output_dir, ordered_results, final_error=error_log)
        return RenderResult(
            success=False,
            error_log=error_log,
            scene_name="ScenePack",
            segments=ordered_results,
        )

    final_video = output_dir / "video.mp4"
    concat_error = _concat_segment_videos(ordered_results, final_video)
    if concat_error:
        _write_round_render_log(output_dir, ordered_results, final_error=concat_error)
        return RenderResult(
            success=False,
            error_log=concat_error,
            scene_name="ScenePack",
            segments=ordered_results,
        )

    has_tts_calls = "self.speak(" in code or "self.speak_with_subtitle(" in code
    allow_render_without_tts = bool(get_manim_settings().allow_render_without_tts)
    if enable_tts and has_tts_calls and not has_audio_stream(final_video) and not allow_render_without_tts:
        error_log = (
            "Rendered Scene Pack video is missing an audio track even though the code uses TTS calls."
        )
        _write_round_render_log(output_dir, ordered_results, final_error=error_log)
        return RenderResult(
            success=False,
            error_log=error_log,
            scene_name="ScenePack",
            segments=ordered_results,
        )

    _write_round_render_log(output_dir, ordered_results, final_video=final_video)
    return RenderResult(
        success=True,
        video_path=final_video,
        scene_name="ScenePack",
        segments=ordered_results,
    )


def render_scene(
    code: str,
    output_dir: Path,
    quality_flags: str = "-ql --fps 30",
    enable_tts: bool = True,
    tts_voice: str | None = None,
) -> RenderResult:
    """Thin compatibility wrapper for the Scene Pack renderer."""
    return render_scene_pack(
        code,
        output_dir,
        quality_flags=quality_flags,
        enable_tts=enable_tts,
        tts_voice=tts_voice,
    )


def render_streaming_scene_pack_segment(
    code: str,
    output_dir: Path,
    *,
    segment_id: str,
    order: int | None = None,
    quality_flags: str = "-ql --fps 30",
    tts_voice: str | None = None,
    scene_file: Path | None = None,
) -> SegmentRenderResult:
    """Render one segment from a partial/full Scene Pack source snapshot."""
    segment, resolved_scene_file, _segment_code = write_streaming_scene_pack_segment_file(
        code,
        output_dir,
        segment_id=segment_id,
        order=order,
        tts_voice=tts_voice,
        scene_file=scene_file,
    )
    voice = tts_voice or voice_for_language("en")
    configure_scene_tts(
        voice=voice,
        rate=SCENE_TTS_RATE,
        global_cache_dir=get_global_tts_cache_dir(),
    )
    return _render_or_reuse_segment(
        segment=segment,
        scene_file=resolved_scene_file,
        output_dir=output_dir,
        quality_flags=quality_flags,
        should_render=True,
    )


def render_streaming_scene_pack_segment_with_repair(
    code: str,
    output_dir: Path,
    *,
    segment_id: str,
    order: int | None = None,
    quality_flags: str = "-ql --fps 30",
    tts_voice: str | None = None,
    scene_file: Path | None = None,
    agent: Any | None = None,
    code_eval_agent: Any | None = None,
    output_language: str = "en",
    max_render_fix_attempts: int | None = None,
    max_validation_fix_attempts: int | None = None,
    event_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> SegmentRenderResult:
    from .section_validation import (
        build_render_failure_validation_report,
        repair_streaming_scene_file_method,
        validate_and_fix_streaming_scene_file,
    )

    resolved_scene_file = scene_file if isinstance(scene_file, Path) else Path(scene_file) if scene_file else None
    render_fix_limit = (
        max(0, int(max_render_fix_attempts))
        if max_render_fix_attempts is not None
        else max(0, int(get_manim_settings().render_fix_max_attempts))
    )
    validation_fix_limit = (
        max(1, int(max_validation_fix_attempts))
        if max_validation_fix_attempts is not None
        else max(1, int(get_manim_settings().code_eval_fix_max_attempts))
    )

    render_attempts = 0
    render_repair_rounds = 0
    latest_result: SegmentRenderResult | None = None
    current_code = code

    while True:
        render_attempts += 1
        latest_result = render_streaming_scene_pack_segment(
            current_code,
            output_dir,
            segment_id=segment_id,
            order=order,
            quality_flags=quality_flags,
            tts_voice=tts_voice,
            scene_file=resolved_scene_file,
        )
        latest_result.render_attempts = render_attempts
        latest_result.render_repair_rounds = render_repair_rounds
        if latest_result.success:
            return latest_result

        if (
            resolved_scene_file is None
            or agent is None
            or render_repair_rounds >= render_fix_limit
        ):
            return latest_result

        scene_name = latest_result.scene_name
        validation_dir = _streaming_scene_validation_dir(resolved_scene_file)
        repair_attempt = render_repair_rounds + 1
        report = build_render_failure_validation_report(
            segment_id=segment_id,
            scene_name=scene_name,
            error_log=latest_result.error_log,
        )
        _write_debug_json(
            validation_dir / f"render_report_{repair_attempt}.json",
            {
                "segment_id": segment_id,
                "scene_name": scene_name,
                "attempt": repair_attempt,
                **report.to_dict(),
                "raw_error_log_tail": latest_result.error_log[-5000:],
            },
        )
        _write_debug_text(
            validation_dir / f"scene_file_before_render_fix_{repair_attempt}.py",
            resolved_scene_file.read_text(encoding="utf-8"),
        )
        if event_callback is not None:
            _render_event(
                event_callback,
                {
                    "kind": "section_render_fix_started",
                    "segment_id": segment_id,
                    "scene_name": scene_name,
                    "segment_order": order,
                    "attempt": repair_attempt,
                },
            )
        try:
            updated_code = repair_streaming_scene_file_method(
                agent=agent,
                scene_file=resolved_scene_file,
                segment_id=segment_id,
                validation_report=report,
                output_language=output_language,
            )
            if not updated_code.strip():
                raise ValueError("Render repair did not return updated scene file content.")
            resolved_scene_file.write_text(updated_code, encoding="utf-8")
            _write_debug_text(
                validation_dir / f"scene_file_after_render_fix_{repair_attempt}.py",
                updated_code,
            )
            validation_result = validate_and_fix_streaming_scene_file(
                code_eval_agent=code_eval_agent,
                agent=agent,
                scene_file=resolved_scene_file,
                output_language=output_language,
                max_attempts=validation_fix_limit,
                segment_id=segment_id,
                scene_name=scene_name,
                order=order,
            )
            validation_summary = ""
            if isinstance(validation_result.get("validation_report"), dict):
                validation_summary = str(validation_result["validation_report"].get("summary") or "")
            _write_debug_json(
                validation_dir / f"render_fix_result_{repair_attempt}.json",
                {
                    "segment_id": segment_id,
                    "scene_name": scene_name,
                    "attempt": repair_attempt,
                    "patched": True,
                    "validation_passed": bool(validation_result.get("passed")),
                    "validation_attempts": int(validation_result.get("validation_attempts", 0)),
                    "validation_summary": validation_summary,
                    "error": str(validation_result.get("error") or ""),
                },
            )
            if event_callback is not None:
                _render_event(
                    event_callback,
                    {
                        "kind": "section_render_fix_completed",
                        "segment_id": segment_id,
                        "scene_name": scene_name,
                        "segment_order": order,
                        "attempt": repair_attempt,
                        "success": bool(validation_result.get("passed")),
                        "validation_attempts": int(validation_result.get("validation_attempts", 0)),
                        "validation_summary": validation_summary or str(validation_result.get("error") or ""),
                    },
                )
            if not validation_result.get("passed"):
                latest_result.render_repair_rounds = repair_attempt
                latest_result.render_attempts = render_attempts
                latest_result.error_log = validation_summary or str(validation_result.get("error") or "validation_failed")
                return latest_result
            render_repair_rounds = repair_attempt
            current_code = resolved_scene_file.read_text(encoding="utf-8")
        except Exception as exc:
            error_text = str(exc)
            _write_debug_json(
                validation_dir / f"render_fix_result_{repair_attempt}.json",
                {
                    "segment_id": segment_id,
                    "scene_name": scene_name,
                    "attempt": repair_attempt,
                    "patched": False,
                    "error": error_text,
                },
            )
            if event_callback is not None:
                _render_event(
                    event_callback,
                    {
                        "kind": "section_render_fix_completed",
                        "segment_id": segment_id,
                        "scene_name": scene_name,
                        "segment_order": order,
                        "attempt": repair_attempt,
                        "success": False,
                        "validation_summary": error_text,
                    },
                )
            latest_result.render_repair_rounds = repair_attempt
            latest_result.render_attempts = render_attempts
            latest_result.error_log = error_text
            return latest_result


def write_streaming_scene_pack_segment_file(
    code: str,
    output_dir: Path,
    *,
    segment_id: str,
    order: int | None = None,
    tts_voice: str | None = None,
    scene_file: Path | None = None,
) -> tuple[SegmentSpec, Path, str]:
    """Materialize a single ready segment into ``streaming_scene_files`` immediately."""
    segment_code = build_segment_scene_source(code, segment_id)
    segment_code = _sanitize_chinese_in_latex(segment_code)
    scene_pack = parse_scene_pack(segment_code)
    segment = next((item for item in scene_pack.manifest if item.segment_id == segment_id), None)
    if segment is None:
        raise ValueError(f"Unknown segment id `{segment_id}` in streaming segment source.")
    if order is not None:
        segment = SegmentSpec(
            segment_id=segment.segment_id,
            scene_name=segment.scene_name,
            method_name=segment.method_name,
            order=int(order),
            lineno=segment.lineno,
        )

    voice = tts_voice or voice_for_language("en")
    full_code = _build_scene_file_code(
        segment_code,
        tts_voice=voice,
        tts_rate=SCENE_TTS_RATE,
        tts_global_cache_dir=get_global_tts_cache_dir(),
    )
    resolved_scene_file = scene_file
    if resolved_scene_file is None:
        scene_files_dir = output_dir / "streaming_scene_files"
        scene_files_dir.mkdir(parents=True, exist_ok=True)
        resolved_scene_file = scene_files_dir / f"{segment.order:02d}_{segment.segment_id}.py"
    else:
        resolved_scene_file.parent.mkdir(parents=True, exist_ok=True)
    resolved_scene_file.write_text(full_code, encoding="utf-8")
    return segment, resolved_scene_file, segment_code


def _build_scene_file_code(
    code: str,
    *,
    tts_voice: str,
    tts_rate: str,
    tts_global_cache_dir: Path,
) -> str:
    project_root = Path(__file__).resolve().parents[2]
    path_bootstrap = (
        "import sys\n"
        "from pathlib import Path\n"
        f'_PROJECT_ROOT = Path("{project_root.as_posix()}")\n'
        "if str(_PROJECT_ROOT) not in sys.path:\n"
        "    sys.path.insert(0, str(_PROJECT_ROOT))\n"
    )
    tts_bootstrap = (
        "from plugins.manim.agent_pipeline.tts import configure_scene_tts\n"
        f"configure_scene_tts(voice={tts_voice!r}, rate={tts_rate!r}, "
        f"global_cache_dir={str(tts_global_cache_dir)!r})\n"
    )
    compatibility_imports = "from plugins.manim.colortest.narrated_scene import NarratedScene\n"
    return path_bootstrap + "\n" + tts_bootstrap + compatibility_imports + "\n" + code


def _streaming_scene_validation_dir(scene_file: Path) -> Path:
    return scene_file.parent / "validation" / scene_file.stem


def _write_debug_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_debug_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _render_segments(
    *,
    manifest: List[SegmentSpec],
    scene_file: Path,
    output_dir: Path,
    quality_flags: str,
    selected_segment_ids: Optional[set[str]],
    progress_callback: Optional[Callable[[str], None]] = None,
    event_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> List[SegmentRenderResult]:
    selected = set(selected_segment_ids or [])
    reuse_only = STREAMING_REUSE_ONLY_SEGMENT_ID in selected
    selected.discard(STREAMING_REUSE_ONLY_SEGMENT_ID)
    total = len(manifest)
    if reuse_only or selected:
        out: List[SegmentRenderResult] = []
        completed = 0
        for segment in manifest:
            should = segment.segment_id in selected
            _render_progress(
                progress_callback,
                f"Manim [{segment.order:02d}/{total}] {segment.segment_id} "
                f"scene={segment.scene_name} render={'yes' if should else 'reuse/skip'}",
            )
            _render_event(
                event_callback,
                {
                    "kind": "segment_started",
                    "segment_id": segment.segment_id,
                    "scene_name": segment.scene_name,
                    "segment_order": segment.order,
                    "segment_total": total,
                },
            )
            out.append(
                _render_or_reuse_segment(
                    segment=segment,
                    scene_file=scene_file,
                    output_dir=output_dir,
                    quality_flags=quality_flags,
                    should_render=should,
                )
            )
            completed += 1
            _render_event(
                event_callback,
                {
                    "kind": "segment_completed",
                    "segment_id": segment.segment_id,
                    "scene_name": segment.scene_name,
                    "segment_order": segment.order,
                    "segment_total": total,
                    "segment_done": completed,
                    "success": bool(out[-1].success),
                },
            )
        return out

    max_workers = len(manifest)
    cap = get_manim_settings().segment_render_workers
    if cap > 0:
        max_workers = min(max_workers, cap)

    if max_workers <= 1:
        results_seq: List[SegmentRenderResult] = []
        completed = 0
        for segment in manifest:
            _render_progress(
                progress_callback,
                f"Manim [{segment.order:02d}/{total}] 启动子进程 {segment.scene_name} "
                f"({segment.segment_id})",
            )
            _render_event(
                event_callback,
                {
                    "kind": "segment_started",
                    "segment_id": segment.segment_id,
                    "scene_name": segment.scene_name,
                    "segment_order": segment.order,
                    "segment_total": total,
                },
            )
            results_seq.append(
                _render_or_reuse_segment(
                    segment=segment,
                    scene_file=scene_file,
                    output_dir=output_dir,
                    quality_flags=quality_flags,
                    should_render=True,
                )
            )
            completed += 1
            _render_event(
                event_callback,
                {
                    "kind": "segment_completed",
                    "segment_id": segment.segment_id,
                    "scene_name": segment.scene_name,
                    "segment_order": segment.order,
                    "segment_total": total,
                    "segment_done": completed,
                    "success": bool(results_seq[-1].success),
                },
            )
        return results_seq

    results: List[SegmentRenderResult] = []
    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                _render_or_reuse_segment,
                segment=segment,
                scene_file=scene_file,
                output_dir=output_dir,
                quality_flags=quality_flags,
                should_render=True,
            ): segment
            for segment in manifest
        }
        for segment in manifest:
            _render_event(
                event_callback,
                {
                    "kind": "segment_started",
                    "segment_id": segment.segment_id,
                    "scene_name": segment.scene_name,
                    "segment_order": segment.order,
                    "segment_total": total,
                },
            )
        for future in as_completed(future_map):
            segment = future_map[future]
            res = future.result()
            st = "OK" if res.success else "FAIL"
            _render_progress(
                progress_callback,
                f"Manim [{segment.order:02d}/{total}] {segment.scene_name} 完成 | {st}",
            )
            results.append(res)
            completed += 1
            _render_event(
                event_callback,
                {
                    "kind": "segment_completed",
                    "segment_id": segment.segment_id,
                    "scene_name": segment.scene_name,
                    "segment_order": segment.order,
                    "segment_total": total,
                    "segment_done": completed,
                    "success": bool(res.success),
                },
            )
    return results


def _render_or_reuse_segment(
    *,
    segment: SegmentSpec,
    scene_file: Path,
    output_dir: Path,
    quality_flags: str,
    should_render: bool,
) -> SegmentRenderResult:
    segment_dir = _segment_output_dir(output_dir, segment)
    if not should_render:
        existing = _existing_segment_result(segment, segment_dir)
        if existing is not None:
            return existing
        return SegmentRenderResult(
            segment_id=segment.segment_id,
            scene_name=segment.scene_name,
            order=segment.order,
            output_dir=segment_dir,
            success=False,
            error_log=(
                f"Segment `{segment.segment_id}` was not selected for rerender, "
                "but no prior segment video exists to reuse."
            ),
        )

    segment_dir.mkdir(parents=True, exist_ok=True)
    media_dir = segment_dir / "media"
    log_file = segment_dir / "render_log.txt"

    manim_cfg = _resolved_manim_cli_config_path()
    cmd: list[str] = [
        sys.executable,
        "-m",
        "manim",
    ]
    if manim_cfg is not None:
        cmd.extend(["--config_file", str(manim_cfg)])
    cmd.extend(
        [
            str(scene_file.resolve()),
            segment.scene_name,
            *shlex.split(quality_flags),
            "--media_dir",
            str(media_dir.resolve()),
        ]
    )
    render_env = dict(os.environ)
    existing_pythonpath = render_env.get("PYTHONPATH", "").strip()
    backend_root_str = str(BACKEND_ROOT)
    render_env["PYTHONPATH"] = (
        backend_root_str
        if not existing_pythonpath
        else os.pathsep.join([backend_root_str, existing_pythonpath])
    )

    returncode, combined_output = _run_subprocess_streaming(
        cmd=cmd,
        cwd=output_dir,
        log_file=log_file,
        env=render_env,
        timeout_seconds=max(1.0, float(get_manim_settings().job_timeout_seconds)),
    )
    if returncode != 0:
        return SegmentRenderResult(
            segment_id=segment.segment_id,
            scene_name=segment.scene_name,
            order=segment.order,
            output_dir=segment_dir,
            success=False,
            error_log=combined_output[-5000:],
        )

    video_path = _find_video(media_dir, segment.scene_name)
    if video_path is None:
        return SegmentRenderResult(
            segment_id=segment.segment_id,
            scene_name=segment.scene_name,
            order=segment.order,
            output_dir=segment_dir,
            success=False,
            error_log=f"Render completed but segment video not found under {media_dir}",
        )

    final_segment_video = segment_dir / "video.mp4"
    shutil.copy2(str(video_path), str(final_segment_video))
    return SegmentRenderResult(
        segment_id=segment.segment_id,
        scene_name=segment.scene_name,
        order=segment.order,
        output_dir=segment_dir,
        success=True,
        video_path=final_segment_video,
    )


def _segment_output_dir(output_dir: Path, segment: SegmentSpec) -> Path:
    return output_dir / "segments" / f"{segment.order:02d}_{_sanitize_segment_id(segment.segment_id)}"


def _existing_segment_result(
    segment: SegmentSpec,
    segment_dir: Path,
) -> Optional[SegmentRenderResult]:
    final_segment_video = segment_dir / "video.mp4"
    if not final_segment_video.exists():
        return None
    return SegmentRenderResult(
        segment_id=segment.segment_id,
        scene_name=segment.scene_name,
        order=segment.order,
        output_dir=segment_dir,
        success=True,
        video_path=final_segment_video,
    )


def _contiguous_prefix_segment_results(
    segment_results: List[SegmentRenderResult],
) -> List[SegmentRenderResult]:
    ordered = sorted(segment_results, key=lambda item: item.order)
    prefix: list[SegmentRenderResult] = []
    expected_order = 0
    for segment in ordered:
        if segment.order != expected_order:
            break
        if not segment.success or segment.video_path is None:
            break
        prefix.append(segment)
        expected_order += 1
    return prefix


def build_incremental_preview_video(
    segment_results: List[SegmentRenderResult],
    output_path: Path,
) -> tuple[int, str]:
    prefix = _contiguous_prefix_segment_results(segment_results)
    if not prefix:
        return 0, "No contiguous rendered segment prefix is available for preview."
    return len(prefix), _concat_segment_videos(prefix, output_path)


def build_incremental_hls_preview(
    segment_results: List[SegmentRenderResult],
    hls_root: Path,
    *,
    preview_version: int,
    target_segment_seconds: int = 6,
) -> tuple[int, Path | None, str]:
    prefix = _contiguous_prefix_segment_results(segment_results)
    if not prefix:
        return 0, None, "No contiguous rendered segment prefix is available for preview."
    manifest_path, error = _build_hls_manifest_from_segment_results(
        prefix,
        hls_root,
        manifest_name=f"preview_v{preview_version:02d}.m3u8",
        target_segment_seconds=target_segment_seconds,
    )
    if error:
        return len(prefix), None, error
    return len(prefix), manifest_path, ""


def build_hls_video_file(
    video_path: Path,
    hls_root: Path,
    *,
    manifest_name: str,
    target_segment_seconds: int = 6,
) -> tuple[Path | None, str]:
    source = Path(video_path).resolve()
    if not source.exists() or not source.is_file():
        return None, f"Video file is missing for HLS packaging: {source}"
    segment_dir = Path(hls_root) / "segments" / Path(manifest_name).stem
    playlist_path, error = _package_video_as_hls(
        source,
        segment_dir,
        segment_prefix=Path(manifest_name).stem,
        target_segment_seconds=target_segment_seconds,
    )
    if error:
        return None, error
    manifest_path = Path(hls_root) / "manifests" / manifest_name
    chunks, error = _read_hls_playlist_chunks(
        playlist_path,
        relative_base=manifest_path.parent,
    )
    if error:
        return None, error
    _write_combined_hls_manifest(
        chunks,
        manifest_path,
        playlist_type="VOD",
    )
    return manifest_path, ""


def _build_hls_manifest_from_segment_results(
    segment_results: List[SegmentRenderResult],
    hls_root: Path,
    *,
    manifest_name: str,
    target_segment_seconds: int,
) -> tuple[Path | None, str]:
    chunks: list[tuple[float, str]] = []
    manifest_path = Path(hls_root) / "manifests" / manifest_name
    for segment in sorted(segment_results, key=lambda item: item.order):
        if not segment.video_path:
            return None, f"Missing video path for HLS segment: {segment.segment_id}"
        source = Path(segment.video_path).resolve()
        segment_dir = Path(hls_root) / "segments" / _hls_segment_dir_name(segment)
        playlist_path, error = _package_video_as_hls(
            source,
            segment_dir,
            segment_prefix=_hls_segment_dir_name(segment),
            target_segment_seconds=target_segment_seconds,
        )
        if error:
            return None, error
        segment_chunks, error = _read_hls_playlist_chunks(
            playlist_path,
            relative_base=manifest_path.parent,
        )
        if error:
            return None, error
        chunks.extend(segment_chunks)
    if not chunks:
        return None, "No HLS media segments were produced."
    _write_combined_hls_manifest(chunks, manifest_path, playlist_type="VOD")
    return manifest_path, ""


def _hls_segment_dir_name(segment: SegmentRenderResult) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(segment.segment_id or "segment")).strip("_")
    return f"{int(segment.order):02d}_{safe_id or 'segment'}"


def _package_video_as_hls(
    source_path: Path,
    output_dir: Path,
    *,
    segment_prefix: str,
    target_segment_seconds: int,
) -> tuple[Path, str]:
    playlist_path = output_dir / "index.m3u8"
    if playlist_path.exists() and any(output_dir.glob("*.ts")):
        return playlist_path, ""
    if not source_path.exists() or not source_path.is_file():
        return playlist_path, f"HLS source video is missing: {source_path}"
    if not shutil.which("ffmpeg"):
        return playlist_path, "ffmpeg not found, cannot package HLS output."
    output_dir.mkdir(parents=True, exist_ok=True)
    for old_file in output_dir.glob("*"):
        if old_file.is_file() and old_file.suffix.lower() in {".m3u8", ".ts"}:
            old_file.unlink()
    segment_template = output_dir / f"{segment_prefix}_%03d.ts"
    base_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0",
        "-f",
        "hls",
        "-hls_time",
        str(max(1, int(target_segment_seconds))),
        "-hls_playlist_type",
        "vod",
        "-hls_segment_filename",
        str(segment_template),
    ]
    copy_cmd = [*base_cmd, "-c", "copy", str(playlist_path)]
    copy_result = subprocess.run(
        copy_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if copy_result.returncode == 0 and playlist_path.exists() and any(output_dir.glob("*.ts")):
        return playlist_path, ""
    reencode_cmd = [
        *base_cmd,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(playlist_path),
    ]
    reencode_result = subprocess.run(
        reencode_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if reencode_result.returncode == 0 and playlist_path.exists() and any(output_dir.glob("*.ts")):
        return playlist_path, ""
    return playlist_path, (
        "ffmpeg HLS packaging failed.\n\n"
        f"copy stderr:\n{copy_result.stderr[-2000:]}\n\n"
        f"reencode stderr:\n{reencode_result.stderr[-2000:]}"
    )


def _read_hls_playlist_chunks(
    playlist_path: Path,
    *,
    relative_base: Path,
) -> tuple[list[tuple[float, str]], str]:
    chunks: list[tuple[float, str]] = []
    pending_duration: float | None = None
    for raw_line in playlist_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("#EXTINF:"):
            duration_text = line[len("#EXTINF:") :].split(",", 1)[0]
            try:
                pending_duration = float(duration_text)
            except ValueError:
                pending_duration = 0.0
            continue
        if not line or line.startswith("#"):
            continue
        media_path = (playlist_path.parent / line).resolve()
        try:
            relative_uri = media_path.relative_to(relative_base.resolve()).as_posix()
        except ValueError:
            relative_uri = os.path.relpath(media_path, relative_base.resolve()).replace(os.sep, "/")
        chunks.append((max(0.01, float(pending_duration or 0.01)), relative_uri))
        pending_duration = None
    if not chunks:
        return [], f"HLS playlist did not contain media chunks: {playlist_path}"
    return chunks, ""


def _write_combined_hls_manifest(
    chunks: list[tuple[float, str]],
    manifest_path: Path,
    *,
    playlist_type: str,
) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    target_duration = max(1, int(math.ceil(max(duration for duration, _ in chunks))))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        f"#EXT-X-PLAYLIST-TYPE:{playlist_type}",
    ]
    for duration, uri in chunks:
        lines.append(f"#EXTINF:{duration:.3f},")
        lines.append(uri)
    lines.append("#EXT-X-ENDLIST")
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _concat_segment_videos(
    segment_results: List[SegmentRenderResult],
    output_path: Path,
) -> str:
    if not segment_results:
        return "No segment videos were produced for concatenation."

    ordered_videos = [segment.video_path for segment in segment_results if segment.video_path]
    if len(ordered_videos) != len(segment_results):
        return "Cannot concatenate segment videos because at least one segment is missing its final video."

    if len(ordered_videos) == 1:
        shutil.copy2(str(ordered_videos[0]), str(output_path))
        return ""

    if not shutil.which("ffmpeg"):
        return "ffmpeg not found, cannot concatenate Scene Pack segments."

    concat_list = output_path.parent / "segments_concat.txt"
    concat_list.write_text(
        "".join(_concat_list_line(video) for video in ordered_videos),
        encoding="utf-8",
    )

    copy_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        str(output_path),
    ]
    copy_result = subprocess.run(
        copy_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if copy_result.returncode == 0 and output_path.exists():
        return ""

    reencode_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output_path),
    ]
    reencode_result = subprocess.run(
        reencode_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if reencode_result.returncode == 0 and output_path.exists():
        return ""

    return (
        "ffmpeg concat failed.\n\n"
        "Copy-mode stderr:\n"
        f"{copy_result.stderr[-3000:]}\n\n"
        "Re-encode stderr:\n"
        f"{reencode_result.stderr[-3000:]}"
    )


def _concat_list_line(path: Path) -> str:
    normalized = path.resolve().as_posix().replace("'", r"'\''")
    return f"file '{normalized}'\n"


def _format_segment_failures(failed_segments: List[SegmentRenderResult]) -> str:
    chunks = []
    for segment in failed_segments:
        header = f"[{segment.order:02d}:{segment.segment_id} -> {segment.scene_name}]"
        body = segment.error_log.strip() or "Unknown segment render failure."
        chunks.append(f"{header}\n{body}")
    return "\n\n".join(chunks)


def _write_round_render_log(
    output_dir: Path,
    segment_results: List[SegmentRenderResult],
    *,
    final_video: Optional[Path] = None,
    final_error: str = "",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    lines = ["Scene Pack render summary", ""]
    for segment in sorted(segment_results, key=lambda item: item.order):
        status = "OK" if segment.success else "FAIL"
        lines.append(
            f"[{segment.order:02d}] {segment.segment_id} | {segment.scene_name} | {status}"
        )
        lines.append(f"  dir: {segment.output_dir}")
        if segment.video_path:
            lines.append(f"  video: {segment.video_path}")
        if segment.error_log:
            snippet = segment.error_log.strip()
            if len(snippet) > 1000:
                snippet = snippet[-1000:]
            lines.append("  error:")
            lines.append(snippet)
        lines.append("")

    if final_video:
        lines.append(f"Final video: {final_video}")
    if final_error:
        lines.append("Final error:")
        lines.append(final_error)

    (output_dir / "render_log.txt").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _sanitize_segment_id(segment_id: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_-]+", "_", segment_id.strip())
    cleaned = cleaned.strip("_").lower()
    return cleaned or "segment"


def _find_video(media_dir: Path, scene_name: str) -> Optional[Path]:
    """Search for the rendered .mp4 under media_dir."""
    if not media_dir.exists():
        return None

    candidates = [
        mp4
        for mp4 in media_dir.rglob("*.mp4")
        if "partial_movie_files" not in {part.lower() for part in mp4.parts}
    ]

    for mp4 in candidates:
        if scene_name in mp4.stem:
            return mp4

    all_mp4 = sorted(candidates, key=lambda item: item.stat().st_mtime)
    if all_mp4:
        return all_mp4[-1]
    return None


__all__ = [
    "STREAMING_REUSE_ONLY_SEGMENT_ID",
    "SegmentTTSPreparationResult",
    "SegmentRenderResult",
    "RenderResult",
    "build_hls_video_file",
    "build_incremental_hls_preview",
    "build_incremental_preview_video",
    "prepare_segment_tts_assets",
    "render_streaming_scene_pack_segment",
    "render_streaming_scene_pack_segment_with_repair",
    "write_streaming_scene_pack_segment_file",
    "render_scene_pack",
    "render_scene",
]
