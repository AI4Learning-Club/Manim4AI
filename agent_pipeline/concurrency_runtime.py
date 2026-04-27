"""
Dual-process + threaded orchestration for Scene Pack TTS and Manim segment renders.

TTS runs in a dedicated child process with a ThreadPoolExecutor; segment renders run in
another child process with its own ThreadPoolExecutor. Both communicate via filesystem
paths shared with the parent (run_dir).
"""

from __future__ import annotations

import multiprocessing as mp
import shutil
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from plugins.manim.runtime_config import get_manim_settings


@dataclass
class SectionPipelineTask:
    segment_id: str
    scene_name: str
    order: int
    payload: dict[str, Any]


@dataclass
class SectionPipelineState:
    task: SectionPipelineTask
    status: str = "code_ready"
    validation_result: Any = None
    tts_result: Any = None
    render_result: Any = None
    tts_attempts: int = 0
    error: str = ""


@dataclass
class SectionPipelineCoordinator:
    tts_prepare_fn: Callable[[dict[str, Any]], Any]
    render_fn: Callable[[dict[str, Any]], Any]
    validate_fix_fn: Callable[[dict[str, Any]], Any] | None = None
    event_callback: Callable[[dict[str, Any]], None] | None = None
    on_section_render_completed: Callable[[SectionPipelineState], None] | None = None
    validate_workers: int = 1
    tts_workers: int = 1
    render_workers: int = 1
    tts_retry_attempts: int = 0
    allow_render_on_tts_failure: bool = False
    poll_interval_s: float = 0.02
    _validate_executor: ThreadPoolExecutor = field(init=False)
    _tts_executor: ThreadPoolExecutor = field(init=False)
    _render_executor: ThreadPoolExecutor = field(init=False)
    _states: dict[str, SectionPipelineState] = field(init=False, default_factory=dict)
    _validate_futures: dict[Future[Any], str] = field(init=False, default_factory=dict)
    _tts_futures: dict[Future[Any], str] = field(init=False, default_factory=dict)
    _render_futures: dict[Future[Any], str] = field(init=False, default_factory=dict)
    _accepting_submissions: bool = field(init=False, default=True)
    _lock: threading.Lock = field(init=False, default_factory=threading.Lock)
    _driver_stop: threading.Event = field(init=False, default_factory=threading.Event)
    _driver_thread: threading.Thread = field(init=False)

    def __post_init__(self) -> None:
        self._validate_executor = ThreadPoolExecutor(max_workers=max(1, self.validate_workers))
        self._tts_executor = ThreadPoolExecutor(max_workers=max(1, self.tts_workers))
        self._render_executor = ThreadPoolExecutor(max_workers=max(1, self.render_workers))
        self._driver_thread = threading.Thread(target=self._drive_loop, daemon=True)
        self._driver_thread.start()

    def submit_ready_section(self, payload: dict[str, Any]) -> None:
        segment_id = str(payload["segment_id"])
        task = SectionPipelineTask(
            segment_id=segment_id,
            scene_name=str(payload["scene_name"]),
            order=int(payload["order"]),
            payload=dict(payload),
        )
        with self._lock:
            if not self._accepting_submissions:
                raise RuntimeError("Coordinator is closed to new section submissions.")
            if segment_id in self._states:
                return
            state = SectionPipelineState(task=task)
            self._states[segment_id] = state
            if self.validate_fix_fn is not None:
                state.status = "validating"
                future = self._validate_executor.submit(self.validate_fix_fn, task.payload)
                self._validate_futures[future] = segment_id
            else:
                state.status = "tts_running"
                future = self._tts_executor.submit(self.tts_prepare_fn, task.payload)
                self._tts_futures[future] = segment_id

    def close_submissions(self) -> None:
        with self._lock:
            self._accepting_submissions = False

    def wait_until_complete(self) -> list[SectionPipelineState]:
        while True:
            with self._lock:
                terminal = all(
                    state.status in {"done", "failed"}
                    for state in self._states.values()
                )
                done = (
                    not self._accepting_submissions
                    and not self._validate_futures
                    and not self._tts_futures
                    and not self._render_futures
                    and terminal
                )
                if done:
                    break
            time.sleep(self.poll_interval_s)
        self._driver_stop.set()
        self._driver_thread.join(timeout=max(1.0, self.poll_interval_s * 50))
        self._validate_executor.shutdown(wait=True)
        self._tts_executor.shutdown(wait=True)
        self._render_executor.shutdown(wait=True)
        return sorted(self._states.values(), key=lambda item: item.task.order)

    def _advance_once(self) -> None:
        done_validate = _pop_done_futures(self._validate_futures)
        for future, segment_id in done_validate:
            state = self._states[segment_id]
            try:
                result = future.result()
                state.validation_result = result
                if _validation_result_ok(result):
                    if isinstance(result, dict) and isinstance(result.get("payload"), dict):
                        state.task.payload = dict(result["payload"])
                    state.status = "tts_running"
                    tts_future = self._tts_executor.submit(self.tts_prepare_fn, state.task.payload)
                    self._tts_futures[tts_future] = segment_id
                else:
                    state.status = "failed"
                    state.error = _validation_result_error(result)
            except Exception as exc:
                state.status = "failed"
                state.error = str(exc)

        done_tts = _pop_done_futures(self._tts_futures)
        for future, segment_id in done_tts:
            state = self._states[segment_id]
            try:
                result = future.result()
                state.tts_result = result
                state.tts_attempts += 1
                if _tts_result_ok(result):
                    state.status = "rendering"
                    render_future = self._render_executor.submit(
                        self._run_render_task,
                        state.task,
                    )
                    self._render_futures[render_future] = segment_id
                elif state.tts_attempts <= self.tts_retry_attempts:
                    state.status = "tts_running"
                    retry_payload = dict(state.task.payload)
                    retry_payload["tts_retry_attempt"] = state.tts_attempts
                    retry_payload["tts_retry_reason"] = _tts_result_error(result)
                    state.task.payload = retry_payload
                    if self.event_callback is not None:
                        self.event_callback(
                            {
                                "kind": "section_tts_retry_scheduled",
                                "segment_id": state.task.segment_id,
                                "scene_name": state.task.scene_name,
                                "segment_order": state.task.order,
                                "attempt": state.tts_attempts,
                                "max_retries": self.tts_retry_attempts,
                                "reason": _tts_result_error(result),
                            }
                        )
                    tts_future = self._tts_executor.submit(self.tts_prepare_fn, state.task.payload)
                    self._tts_futures[tts_future] = segment_id
                elif self.allow_render_on_tts_failure:
                    state.status = "rendering"
                    render_payload = dict(state.task.payload)
                    render_payload["tts_failed"] = True
                    render_payload["tts_failed_texts"] = _tts_failed_texts(result)
                    render_payload["tts_error"] = _tts_result_error(result)
                    state.task.payload = render_payload
                    if self.event_callback is not None:
                        self.event_callback(
                            {
                                "kind": "section_tts_failed_but_rendering",
                                "segment_id": state.task.segment_id,
                                "scene_name": state.task.scene_name,
                                "segment_order": state.task.order,
                                "attempts": state.tts_attempts,
                                "reason": _tts_result_error(result),
                            }
                        )
                    render_future = self._render_executor.submit(
                        self._run_render_task,
                        state.task,
                    )
                    self._render_futures[render_future] = segment_id
                else:
                    state.status = "failed"
                    state.error = _tts_result_error(result)
            except Exception as exc:
                state.status = "failed"
                state.error = str(exc)

        done_render = _pop_done_futures(self._render_futures)
        for future, segment_id in done_render:
            state = self._states[segment_id]
            try:
                result = future.result()
                state.render_result = result
                if _render_result_ok(result):
                    state.status = "done"
                else:
                    state.status = "failed"
                    state.error = _render_result_error(result)
                if self.event_callback is not None:
                    self.event_callback(
                        {
                            "kind": "section_render_completed",
                            "segment_id": state.task.segment_id,
                            "scene_name": state.task.scene_name,
                            "segment_order": state.task.order,
                            "success": state.status == "done",
                        }
                    )
                if self.on_section_render_completed is not None:
                    self.on_section_render_completed(state)
            except Exception as exc:
                state.status = "failed"
                state.error = str(exc)
                if self.event_callback is not None:
                    self.event_callback(
                        {
                            "kind": "section_render_completed",
                            "segment_id": state.task.segment_id,
                            "scene_name": state.task.scene_name,
                            "segment_order": state.task.order,
                            "success": False,
                        }
                    )
                if self.on_section_render_completed is not None:
                    self.on_section_render_completed(state)

    def _drive_loop(self) -> None:
        while not self._driver_stop.is_set():
            self._advance_once()
            time.sleep(self.poll_interval_s)

    def _run_render_task(self, task: SectionPipelineTask) -> Any:
        if self.event_callback is not None:
            self.event_callback(
                {
                    "kind": "section_render_started",
                    "segment_id": task.segment_id,
                    "scene_name": task.scene_name,
                    "segment_order": task.order,
                }
            )
        return self.render_fn(task.payload)


def _pop_done_futures(mapping: dict[Future[Any], str]) -> list[tuple[Future[Any], str]]:
    if not mapping:
        return []
    done, _pending = wait(list(mapping.keys()), timeout=0, return_when=FIRST_COMPLETED)
    out: list[tuple[Future[Any], str]] = []
    for future in list(done):
        segment_id = mapping.pop(future)
        out.append((future, segment_id))
    return out


def _tts_result_ok(result: Any) -> bool:
    if isinstance(result, dict):
        return bool(result.get("ok", False))
    return bool(getattr(result, "ok", False))


def _tts_result_error(result: Any) -> str:
    if isinstance(result, dict):
        failed = result.get("failed_texts")
        if isinstance(failed, list) and failed:
            return f"tts_failed_texts={failed}"
        return str(result.get("error") or "tts_failed")
    return str(getattr(result, "error", "") or "tts_failed")


def _tts_failed_texts(result: Any) -> list[str]:
    if isinstance(result, dict):
        failed = result.get("failed_texts")
        return [str(item) for item in failed] if isinstance(failed, list) else []
    failed = getattr(result, "failed_texts", None)
    return [str(item) for item in failed] if isinstance(failed, list) else []


def _validation_result_ok(result: Any) -> bool:
    if isinstance(result, dict):
        if "passed" in result:
            return bool(result.get("passed"))
        if "ok" in result:
            return bool(result.get("ok"))
        return "error" not in result
    return bool(getattr(result, "passed", False))


def _validation_result_error(result: Any) -> str:
    if isinstance(result, dict):
        if "error" in result and result.get("error"):
            return str(result["error"])
        report = result.get("validation_report")
        if isinstance(report, dict):
            return str(report.get("summary") or "validation_failed")
        return "validation_failed"
    return str(getattr(result, "error", "") or "validation_failed")


def _render_result_ok(result: Any) -> bool:
    if isinstance(result, dict):
        if "success" in result:
            return bool(result.get("success"))
        if "result" in result and isinstance(result["result"], dict):
            return bool(result["result"].get("success"))
        return "error" not in result
    return bool(getattr(result, "success", False))


def _render_result_error(result: Any) -> str:
    if isinstance(result, dict):
        if "error" in result:
            return str(result["error"])
        if "result" in result and isinstance(result["result"], dict):
            return str(result["result"].get("error_log") or "render_failed")
        return "render_failed"
    return str(getattr(result, "error_log", "") or "render_failed")


def _tts_job_runner(payload: dict[str, Any]) -> tuple[str, bool]:
    """Executed inside TTS worker threads (mirrors renderer._pregenerate _ensure_tts)."""
    from plugins.manim.agent_pipeline.tts import generate_audio

    key = str(payload["key"])
    text = str(payload["text"])
    global_fp = Path(payload["global_fp"])
    round_fp = Path(payload["round_fp"])
    voice = str(payload["voice"])
    rate = str(payload["rate"])
    round_fp.parent.mkdir(parents=True, exist_ok=True)
    global_fp.parent.mkdir(parents=True, exist_ok=True)
    if round_fp.exists():
        return (key, True)
    if global_fp.exists():
        shutil.copy2(str(global_fp), str(round_fp))
        return (key, True)
    if not generate_audio(text, global_fp, voice=voice, rate=rate):
        return (key, False)
    shutil.copy2(str(global_fp), str(round_fp))
    return (key, True)


def _tts_worker_loop(job_queue: mp.Queue, result_queue: mp.Queue, worker_threads: int) -> None:
    """Child process: drain TTS jobs then exit."""
    pending: list[Any] = []
    while True:
        item = job_queue.get()
        if item is None:
            break
        pending.append(item)
    if not pending:
        result_queue.put([])
        return
    max_w = max(1, min(worker_threads, len(pending)))
    with ThreadPoolExecutor(max_workers=max_w) as executor:
        futures = [executor.submit(_tts_job_runner, p) for p in pending]
        results = [f.result() for f in as_completed(futures)]
    result_queue.put(results)


def _manim_segment_runner(payload: dict[str, Any]) -> dict[str, Any]:
    """Wait for segment TTS files, then render one segment."""
    import time as _time

    from plugins.manim.agent_pipeline.renderer import _render_or_reuse_segment
    from plugins.manim.agent_pipeline.scene_pack import SegmentSpec
    from plugins.manim.agent_pipeline.tts import scene_tts_round_cache_path

    texts = list(payload["required_texts"])
    round_dir = Path(payload["round_cache_dir"])
    for text in texts:
        rp = scene_tts_round_cache_path(text, round_dir)
        deadline = 3600.0
        start = _time.monotonic()
        while not rp.exists() and (_time.monotonic() - start) < deadline:
            _time.sleep(0.05)
        if not rp.exists():
            return {
                "error": f"TTS cache missing for segment after wait: {rp}",
                "order": int(payload["order"]),
                "segment_id": str(payload["segment_id"]),
            }

    segment = SegmentSpec(
        segment_id=str(payload["segment_id"]),
        scene_name=str(payload["scene_name"]),
        method_name=str(payload["method_name"]),
        order=int(payload["order"]),
        lineno=payload.get("lineno"),
    )
    scene_file = Path(payload["scene_file"])
    output_dir = Path(payload["output_dir"])
    quality_flags = str(payload["quality_flags"])
    should_render = bool(payload["should_render"])

    result = _render_or_reuse_segment(
        segment=segment,
        scene_file=scene_file,
        output_dir=output_dir,
        quality_flags=quality_flags,
        should_render=should_render,
    )
    return {"order": result.order, "result": asdict(result)}


def _manim_worker_loop(job_queue: mp.Queue, result_queue: mp.Queue, worker_threads: int) -> None:
    pending: list[Any] = []
    while True:
        item = job_queue.get()
        if item is None:
            break
        pending.append(item)
    if not pending:
        result_queue.put({"segments": []})
        return
    max_w = max(1, min(worker_threads, len(pending)))
    segments_out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_w) as executor:
        futures = [executor.submit(_manim_segment_runner, p) for p in pending]
        for future in as_completed(futures):
            segments_out.append(future.result())
    result_queue.put({"segments": segments_out})


def run_dual_process_scene_pack_workers(
    *,
    tts_jobs: list[dict[str, Any]],
    manim_payloads: list[dict[str, Any]],
    event_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[tuple[str, bool]], list[dict[str, Any]]]:
    """
    Run TTS batch in one child process and segment Manim renders in another.

    Returns (tts_results, manim_segment_payloads) where each manim item is a dict with
    ``order`` and ``result`` (asdict) or ``error`` / ``order``.
    """
    settings = get_manim_settings()
    tts_threads = max(1, settings.tts_process_threads)
    seg_cap = settings.segment_render_workers
    manifest_len = max(1, len(manim_payloads))
    seg_threads = manifest_len if seg_cap <= 0 else max(1, min(seg_cap, manifest_len))

    ctx = mp.get_context("spawn")
    tts_q: mp.Queue = ctx.Queue()
    tts_res: mp.Queue = ctx.Queue()
    manim_q: mp.Queue = ctx.Queue()
    manim_res: mp.Queue = ctx.Queue()

    for job in tts_jobs:
        tts_q.put(job)
    tts_q.put(None)

    for payload in manim_payloads:
        manim_q.put(payload)
    manim_q.put(None)

    p_tts = ctx.Process(target=_tts_worker_loop, args=(tts_q, tts_res, tts_threads))
    p_manim = ctx.Process(
        target=_manim_worker_loop,
        args=(manim_q, manim_res, seg_threads),
    )
    p_tts.start()
    p_manim.start()

    tts_results: list[tuple[str, bool]] | None = None
    manim_raw: dict[str, Any] | None = None
    render_stage_announced = False

    while tts_results is None or manim_raw is None:
        if tts_results is None and not p_tts.is_alive():
            p_tts.join()
            tts_results = list(tts_res.get() or [])
            if event_callback is not None and tts_results:
                ok_tts = sum(1 for _key, ok in tts_results if ok)
                event_callback(
                    {
                        "kind": "tts_progress",
                        "tts_total": len(tts_results),
                        "tts_done": len(tts_results),
                        "tts_ok": ok_tts,
                    }
                )
            if event_callback is not None and not render_stage_announced:
                event_callback(
                    {
                        "kind": "render_in_progress",
                        "message": "视频渲染中",
                        "progress": 75,
                    }
                )
                render_stage_announced = True

        if manim_raw is None and not p_manim.is_alive():
            p_manim.join()
            manim_raw = manim_res.get() or {"segments": []}

        if tts_results is None or manim_raw is None:
            time.sleep(0.05)

    if tts_results is None:
        tts_results = []
    if manim_raw is None:
        manim_raw = {"segments": []}

    segments = list(manim_raw.get("segments") or [])

    return tts_results, segments
