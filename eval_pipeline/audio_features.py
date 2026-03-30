"""
Layer 1b – Audio feature extraction and audio-visual alignment.

Extracts deterministic audio quality metrics and computes temporal
alignment between speech activity and video animation activity.

Uses only ffmpeg/ffprobe + numpy (no extra dependencies).

Scoring dimensions fed to fusion:
  7. Audio Signal Quality – SNR, clipping, spectral flatness (signal-level)
  8. AV Temporal Alignment – temporal IoU between speech and motion segments
     (also absorbs duration mismatch and silence distribution)
"""

from __future__ import annotations

import json
import subprocess
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

PCMResult = Tuple[np.ndarray, int]


# =====================================================================
# Dataclasses
# =====================================================================

@dataclass
class AudioMetrics:
    """Video-level audio quality summary."""

    has_audio: bool = False
    audio_duration_sec: float = 0.0
    video_duration_sec: float = 0.0
    duration_mismatch_sec: float = 0.0
    sample_rate: int = 0

    # Silence analysis
    leading_silence_sec: float = 0.0
    trailing_silence_sec: float = 0.0
    mid_silence_total_sec: float = 0.0
    mid_silence_segments: int = 0

    # Clipping / truncation
    clipping_ratio: float = 0.0

    # SNR
    snr_db: float = 0.0

    # Spectral flatness (Wiener entropy): 0 = tonal/clean, 1 = white noise
    spectral_flatness: float = 0.0


@dataclass
class AlignmentMetrics:
    """Temporal alignment between speech activity and video motion."""

    has_audio: bool = False
    speech_segments: List[Tuple[float, float]] = field(default_factory=list)
    motion_segments: List[Tuple[float, float]] = field(default_factory=list)
    speech_total_sec: float = 0.0
    motion_total_sec: float = 0.0
    overlap_sec: float = 0.0
    iou: float = 0.0
    coverage: float = 0.0


# =====================================================================
# Low-level helpers
# =====================================================================

def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _has_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None


def _probe_durations(video_path: Path) -> Tuple[float, float]:
    """Return (audio_duration_sec, video_duration_sec) via ffprobe.

    Returns (0.0, video_dur) if no audio stream exists.
    """
    if not _has_ffprobe():
        return 0.0, 0.0

    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,duration",
        "-of", "json",
        str(video_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            return 0.0, 0.0

        data = json.loads(result.stdout)
        audio_dur = 0.0
        video_dur = 0.0
        for stream in data.get("streams", []):
            dur = float(stream.get("duration", 0) or 0)
            codec_type = stream.get("codec_type", "")
            if codec_type == "audio" and dur > audio_dur:
                audio_dur = dur
            elif codec_type == "video" and dur > video_dur:
                video_dur = dur
        return audio_dur, video_dur
    except Exception:
        return 0.0, 0.0


def _extract_audio_pcm(
    video_path: Path,
    sample_rate: int = 16000,
) -> Optional[PCMResult]:
    """Extract audio as mono float32 PCM using ffmpeg.

    Returns (samples_array, sample_rate) or None if no audio.
    """
    if not _has_ffmpeg():
        return None

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",                      # drop video
        "-ac", "1",                 # mono
        "-ar", str(sample_rate),    # downsample
        "-f", "f32le",              # raw float32 little-endian
        "-acodec", "pcm_f32le",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=120,
        )
        if result.returncode != 0 or len(result.stdout) < 4:
            return None

        samples = np.frombuffer(result.stdout, dtype=np.float32)
        if len(samples) == 0:
            return None
        return samples, sample_rate
    except Exception:
        return None


# =====================================================================
# Silence detection
# =====================================================================

def _detect_silence(
    samples: np.ndarray,
    sr: int,
    threshold_db: float = -40.0,
    min_duration_sec: float = 0.3,
    window_sec: float = 0.1,
    hop_sec: float = 0.05,
) -> List[Tuple[float, float]]:
    """Find silent intervals in audio.

    Computes RMS energy in sliding windows. Frames below *threshold_db*
    are marked silent. Consecutive silent frames are merged into intervals.
    """
    window_samples = max(1, int(window_sec * sr))
    hop_samples = max(1, int(hop_sec * sr))
    threshold_linear = 10.0 ** (threshold_db / 20.0)

    n_frames = max(1, (len(samples) - window_samples) // hop_samples + 1)
    is_silent = np.zeros(n_frames, dtype=bool)

    for i in range(n_frames):
        start = i * hop_samples
        end = start + window_samples
        frame = samples[start:end]
        rms = np.sqrt(np.mean(frame ** 2) + 1e-12)
        if rms < threshold_linear:
            is_silent[i] = True

    # Merge consecutive silent frames into intervals
    intervals: List[Tuple[float, float]] = []
    in_silence = False
    seg_start = 0.0

    for i in range(n_frames):
        t = i * hop_sec
        if is_silent[i] and not in_silence:
            seg_start = t
            in_silence = True
        elif not is_silent[i] and in_silence:
            seg_end = t
            if seg_end - seg_start >= min_duration_sec:
                intervals.append((seg_start, seg_end))
            in_silence = False

    # Close trailing silence
    if in_silence:
        seg_end = (n_frames - 1) * hop_sec + window_sec
        if seg_end - seg_start >= min_duration_sec:
            intervals.append((seg_start, seg_end))

    return intervals


# =====================================================================
# Clipping detection
# =====================================================================

def _detect_clipping(samples: np.ndarray, clip_threshold: float = 0.99) -> float:
    """Return fraction of samples at or above *clip_threshold* amplitude."""
    if len(samples) == 0:
        return 0.0
    return float(np.mean(np.abs(samples) >= clip_threshold))


# =====================================================================
# SNR estimation
# =====================================================================

def _estimate_snr(
    samples: np.ndarray,
    sr: int,
    silence_intervals: List[Tuple[float, float]],
    snr_cap: float = 60.0,
) -> float:
    """Estimate SNR in dB using silent intervals as noise floor.

    Returns *snr_cap* if no noise detected.
    """
    # Collect noise samples from silent intervals
    noise_samples: List[np.ndarray] = []
    for s, e in silence_intervals:
        i_start = int(s * sr)
        i_end = int(e * sr)
        if i_start < len(samples) and i_end > i_start:
            noise_samples.append(samples[i_start:min(i_end, len(samples))])

    if not noise_samples:
        return snr_cap

    noise = np.concatenate(noise_samples)
    noise_rms = np.sqrt(np.mean(noise ** 2) + 1e-12)

    # Signal = everything outside silence
    signal_mask = np.ones(len(samples), dtype=bool)
    for s, e in silence_intervals:
        i_start = int(s * sr)
        i_end = min(int(e * sr), len(samples))
        signal_mask[i_start:i_end] = False

    signal = samples[signal_mask]
    if len(signal) == 0:
        return 0.0

    signal_rms = np.sqrt(np.mean(signal ** 2) + 1e-12)

    if noise_rms < 1e-10:
        return snr_cap

    snr = 20.0 * np.log10(signal_rms / noise_rms)
    return float(min(snr, snr_cap))


# =====================================================================
# Spectral flatness (Wiener entropy)
# =====================================================================

def _spectral_flatness(
    samples: np.ndarray,
    sr: int,
    frame_sec: float = 0.025,
    hop_sec: float = 0.010,
) -> float:
    """Compute mean spectral flatness over speech frames.

    Spectral flatness = geometric_mean(spectrum) / arithmetic_mean(spectrum).
    Values close to 0 → tonal/clean speech, close to 1 → noisy/white noise.
    Only computed on frames with enough energy (skip silence).
    """
    frame_len = max(1, int(frame_sec * sr))
    hop_len = max(1, int(hop_sec * sr))
    n_frames = max(1, (len(samples) - frame_len) // hop_len + 1)

    energy_threshold = 10.0 ** (-40.0 / 20.0)
    flatness_values: List[float] = []

    for i in range(n_frames):
        start = i * hop_len
        frame = samples[start:start + frame_len]
        rms = np.sqrt(np.mean(frame ** 2) + 1e-12)
        if rms < energy_threshold:
            continue  # skip silent frames

        spectrum = np.abs(np.fft.rfft(frame * np.hanning(len(frame))))
        spectrum = spectrum[1:]  # drop DC
        if len(spectrum) == 0 or np.max(spectrum) < 1e-12:
            continue

        # Clamp to avoid log(0)
        spectrum = np.maximum(spectrum, 1e-12)
        geo_mean = np.exp(np.mean(np.log(spectrum)))
        arith_mean = np.mean(spectrum)
        if arith_mean > 1e-12:
            flatness_values.append(geo_mean / arith_mean)

    if not flatness_values:
        return 0.0
    return float(np.mean(flatness_values))


# =====================================================================
# Speech activity detection (energy-based VAD)
# =====================================================================

def _detect_speech_segments(
    samples: np.ndarray,
    sr: int,
    energy_threshold_db: float = -35.0,
    min_speech_sec: float = 0.2,
    merge_gap_sec: float = 0.3,
    window_sec: float = 0.1,
    hop_sec: float = 0.05,
) -> List[Tuple[float, float]]:
    """Detect speech activity segments based on energy envelope.

    Frames above *energy_threshold_db* are speech. Short gaps are merged.
    Very short segments are dropped.
    """
    window_samples = max(1, int(window_sec * sr))
    hop_samples = max(1, int(hop_sec * sr))
    threshold_linear = 10.0 ** (energy_threshold_db / 20.0)

    n_frames = max(1, (len(samples) - window_samples) // hop_samples + 1)
    is_active = np.zeros(n_frames, dtype=bool)

    for i in range(n_frames):
        start = i * hop_samples
        end = start + window_samples
        frame = samples[start:end]
        rms = np.sqrt(np.mean(frame ** 2) + 1e-12)
        if rms >= threshold_linear:
            is_active[i] = True

    # Convert to intervals
    raw_intervals: List[Tuple[float, float]] = []
    in_speech = False
    seg_start = 0.0

    for i in range(n_frames):
        t = i * hop_sec
        if is_active[i] and not in_speech:
            seg_start = t
            in_speech = True
        elif not is_active[i] and in_speech:
            raw_intervals.append((seg_start, t))
            in_speech = False

    if in_speech:
        raw_intervals.append((seg_start, (n_frames - 1) * hop_sec + window_sec))

    # Merge gaps shorter than merge_gap_sec
    merged: List[Tuple[float, float]] = []
    for s, e in raw_intervals:
        if merged and s - merged[-1][1] < merge_gap_sec:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    # Drop very short segments
    return [(s, e) for s, e in merged if e - s >= min_speech_sec]


# =====================================================================
# Motion-to-segments (reuses CV FrameFeatures)
# =====================================================================

def _motion_to_segments(
    frame_features: list,
    fps: float,
    motion_threshold: int = 30,
    min_duration_sec: float = 0.2,
    merge_gap_sec: float = 0.3,
) -> List[Tuple[float, float]]:
    """Convert per-frame motion_pixels into active-motion interval list.

    Takes the already-extracted FrameFeatures list from cv_features
    and derives motion intervals without re-reading the video.
    """
    if not frame_features or fps <= 0:
        return []

    # Build active frame list
    raw_intervals: List[Tuple[float, float]] = []
    in_motion = False
    seg_start = 0.0

    for ff in frame_features:
        t = ff.frame / fps
        active = ff.motion_pixels >= motion_threshold
        if active and not in_motion:
            seg_start = t
            in_motion = True
        elif not active and in_motion:
            raw_intervals.append((seg_start, t))
            in_motion = False

    if in_motion:
        last_t = frame_features[-1].frame / fps + 1.0 / fps
        raw_intervals.append((seg_start, last_t))

    # Merge gaps
    merged: List[Tuple[float, float]] = []
    for s, e in raw_intervals:
        if merged and s - merged[-1][1] < merge_gap_sec:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    return [(s, e) for s, e in merged if e - s >= min_duration_sec]


# =====================================================================
# Interval IoU computation
# =====================================================================

def _interval_iou(
    intervals_a: List[Tuple[float, float]],
    intervals_b: List[Tuple[float, float]],
) -> Tuple[float, float, float]:
    """Compute temporal IoU between two sets of intervals.

    Returns (overlap_sec, iou, coverage_of_a_by_b).
    Uses a sweep-line algorithm over sorted endpoints.
    """
    if not intervals_a or not intervals_b:
        return 0.0, 0.0, 0.0

    # Build event list: +1 for interval start, -1 for interval end
    events_a: List[Tuple[float, int]] = []
    for s, e in intervals_a:
        events_a.append((s, 1))
        events_a.append((e, -1))

    events_b: List[Tuple[float, int]] = []
    for s, e in intervals_b:
        events_b.append((s, 1))
        events_b.append((e, -1))

    # Compute total durations
    total_a = sum(e - s for s, e in intervals_a)
    total_b = sum(e - s for s, e in intervals_b)

    if total_a < 1e-6 and total_b < 1e-6:
        return 0.0, 0.0, 0.0

    # Merge all events and sweep
    all_events: List[Tuple[float, str, int]] = []
    for t, delta in events_a:
        all_events.append((t, "a", delta))
    for t, delta in events_b:
        all_events.append((t, "b", delta))

    # Sort by time, then ends before starts at same time
    all_events.sort(key=lambda x: (x[0], -x[2]))

    active_a = 0
    active_b = 0
    prev_t = 0.0
    overlap = 0.0
    union = 0.0

    for t, source, delta in all_events:
        dt = t - prev_t
        if dt > 0:
            if active_a > 0 and active_b > 0:
                overlap += dt
            if active_a > 0 or active_b > 0:
                union += dt
        if source == "a":
            active_a += delta
        else:
            active_b += delta
        prev_t = t

    iou = overlap / union if union > 1e-6 else 0.0
    coverage = overlap / total_a if total_a > 1e-6 else 0.0

    return overlap, iou, coverage


# =====================================================================
# Top-level extraction functions
# =====================================================================

def _compute_audio_metrics_from_pcm(
    *,
    pcm_result: PCMResult,
    audio_dur: float,
    video_dur: float,
    cfg,
) -> AudioMetrics:
    metrics = AudioMetrics()
    samples, sr = pcm_result

    metrics.has_audio = True
    metrics.audio_duration_sec = audio_dur
    metrics.video_duration_sec = video_dur
    metrics.sample_rate = sr
    metrics.duration_mismatch_sec = abs(audio_dur - video_dur)

    # Silence detection
    silence_intervals = _detect_silence(
        samples, sr,
        threshold_db=cfg.silence_threshold_db,
        min_duration_sec=cfg.silence_min_duration,
        window_sec=cfg.silence_window_sec,
        hop_sec=cfg.silence_hop_sec,
    )

    total_audio_dur = len(samples) / sr

    # Leading silence
    if silence_intervals and silence_intervals[0][0] < 0.05:
        metrics.leading_silence_sec = silence_intervals[0][1]

    # Trailing silence
    if silence_intervals and silence_intervals[-1][1] >= total_audio_dur - 0.1:
        metrics.trailing_silence_sec = total_audio_dur - silence_intervals[-1][0]

    # Mid silence (exclude leading and trailing)
    for s, e in silence_intervals:
        is_leading = s < 0.05
        is_trailing = e >= total_audio_dur - 0.1
        if not is_leading and not is_trailing:
            metrics.mid_silence_total_sec += e - s
            metrics.mid_silence_segments += 1

    # Clipping
    metrics.clipping_ratio = _detect_clipping(samples, cfg.clip_threshold)

    # SNR
    metrics.snr_db = _estimate_snr(samples, sr, silence_intervals, cfg.snr_cap_db)

    # Spectral flatness
    metrics.spectral_flatness = _spectral_flatness(samples, sr)

    return metrics


def extract_audio_metrics_with_pcm(video_path: Path, cfg) -> Tuple[AudioMetrics, Optional[PCMResult]]:
    """Full audio quality analysis plus reusable decoded PCM.

    Returns `(metrics, pcm_result)`. If no audio stream exists, pcm_result is None
    and metrics.has_audio stays False.
    """
    metrics = AudioMetrics()

    # Probe durations
    audio_dur, video_dur = _probe_durations(video_path)
    metrics.video_duration_sec = video_dur

    if audio_dur <= 0:
        return metrics, None

    # Extract PCM
    pcm_result = _extract_audio_pcm(video_path, sample_rate=cfg.sample_rate)
    if pcm_result is None:
        return metrics, None

    metrics = _compute_audio_metrics_from_pcm(
        pcm_result=pcm_result,
        audio_dur=audio_dur,
        video_dur=video_dur,
        cfg=cfg,
    )
    return metrics, pcm_result


def extract_audio_metrics(video_path: Path, cfg) -> AudioMetrics:
    """Full audio quality analysis.

    Returns neutral AudioMetrics(has_audio=False) if no audio stream.
    *cfg* is an AudioConfig instance.
    """
    metrics, _ = extract_audio_metrics_with_pcm(video_path, cfg)
    return metrics


def extract_alignment_metrics(
    video_path: Path,
    frame_features: list,
    fps: float,
    cfg,
    *,
    pcm_result: Optional[PCMResult] = None,
) -> AlignmentMetrics:
    """Compute audio-visual alignment.

    Returns neutral AlignmentMetrics(has_audio=False) if no audio stream.
    *cfg* is an AudioConfig instance.
    """
    metrics = AlignmentMetrics()

    if pcm_result is None:
        pcm_result = _extract_audio_pcm(video_path, sample_rate=cfg.sample_rate)
    if pcm_result is None:
        return metrics

    samples, sr = pcm_result
    metrics.has_audio = True

    # Speech activity detection
    metrics.speech_segments = _detect_speech_segments(
        samples, sr,
        energy_threshold_db=cfg.speech_energy_threshold_db,
        min_speech_sec=cfg.speech_min_duration,
        merge_gap_sec=cfg.speech_merge_gap,
    )
    metrics.speech_total_sec = sum(e - s for s, e in metrics.speech_segments)

    # Motion activity from CV frame features
    metrics.motion_segments = _motion_to_segments(
        frame_features, fps,
        motion_threshold=cfg.motion_threshold,
        min_duration_sec=cfg.motion_min_duration,
        merge_gap_sec=cfg.motion_merge_gap,
    )
    metrics.motion_total_sec = sum(e - s for s, e in metrics.motion_segments)

    # Temporal IoU
    metrics.overlap_sec, metrics.iou, metrics.coverage = _interval_iou(
        metrics.speech_segments,
        metrics.motion_segments,
    )

    return metrics
