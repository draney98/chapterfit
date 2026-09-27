"""One pass over a WAV: loudness, noise floor, clipping, true peak.

The result is what the Quality tab plots. It describes the source, not
the processed file, so the user can see which stages are worth turning on.
"""

from __future__ import annotations

import json
import math
import os

import numpy as np
import soundfile as sf

from .signal import StreamingLoudness, lin_to_db, true_peak

CHUNK_SECONDS = 10.0
_FRAME_SECONDS = 0.05
_CLIP = 0.999
_NOISY_ABOVE_MEDIAN_DB = 6.0
# lin_to_db floors true zeros near −240 dB. A silent gap against a real
# room floor then looks like a 200 dB shift. Ignore anything this quiet
# when comparing the noise floor; speech and room tone sit well above it.
_SILENCE_DB = -90.0


class Cancelled(Exception):
    """The user asked the scan or the render to stop."""


def check_cancel(cancel) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def quality_json_path(work_dir: str, source: str) -> str:
    return os.path.join(work_dir, os.path.basename(source) + ".quality.json")


def scan_wav(path: str, *, cancel=None, progress=None,
             chunk_seconds: float = CHUNK_SECONDS) -> dict:
    """Read ``path`` in blocks. Never holds more than one block."""
    chunks = []
    meter = None
    peak = 0.0
    duration = 0.0
    sample_rate = 0
    channels = 0
    with sf.SoundFile(path) as handle:
        sample_rate = int(handle.samplerate)
        channels = int(handle.channels)
        total = len(handle) / float(sample_rate) if sample_rate else 0.0
        meter = StreamingLoudness(sample_rate)
        block = max(1, int(chunk_seconds * sample_rate))
        start = 0.0
        while True:
            check_cancel(cancel)
            data = handle.read(block, dtype="float32", always_2d=True)
            if len(data) == 0:
                break
            seconds = len(data) / float(sample_rate)
            chunks.append(_chunk_stats(data, sample_rate, start))
            meter.add(data)
            peak = max(peak, true_peak(data, sample_rate))
            start += seconds
            duration = start
            if progress is not None and total > 0:
                progress(min(0.99, duration / total), None, "scanning")
    report = _summarize(chunks, meter, peak, sample_rate, channels, duration)
    if progress is not None:
        progress(1.0, 0.0, "scan done")
    return report


def save_report(path: str, report: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)


def load_report(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _chunk_stats(data: np.ndarray, sr: int, start: float) -> dict:
    peak = float(np.max(np.abs(data))) if len(data) else 0.0
    lufs = _short_lufs(data, sr)
    noise = _noise_floor_db(data, sr)
    return {
        "start": round(start, 3),
        "duration": round(len(data) / float(sr), 3),
        "lufs": None if not math.isfinite(lufs) else round(lufs, 2),
        "noise_floor_db": None if not math.isfinite(noise) else round(noise, 2),
        "peak_db": round(lin_to_db(peak), 2),
        "clipping": bool(peak >= _CLIP),
        "noisy": False,
    }


def _short_lufs(data: np.ndarray, sr: int) -> float:
    if len(data) < int(0.4 * sr):
        rms = float(np.sqrt(np.mean(data ** 2))) if len(data) else 0.0
        return lin_to_db(rms)
    import pyloudnorm
    meter = pyloudnorm.Meter(sr)
    try:
        return float(meter.integrated_loudness(np.clip(data, -1.0, 1.0)))
    except ValueError:
        rms = float(np.sqrt(np.mean(np.square(data))))
        return lin_to_db(rms)


def _noise_floor_db(data: np.ndarray, sr: int) -> float:
    frame = max(1, int(_FRAME_SECONDS * sr))
    if len(data) < frame:
        rms = float(np.sqrt(np.mean(data ** 2))) if len(data) else 0.0
        return lin_to_db(rms)
    hop = frame
    levels = []
    for start in range(0, len(data) - frame + 1, hop):
        piece = data[start:start + frame]
        levels.append(float(np.sqrt(np.mean(piece ** 2))))
    if not levels:
        return float("nan")
    quiet = float(np.percentile(levels, 20))
    return lin_to_db(quiet)


def _audible(noise) -> bool:
    return isinstance(noise, (int, float)) and math.isfinite(noise) and noise > _SILENCE_DB


def _summarize(chunks, meter: StreamingLoudness | None, peak: float,
               sr: int, channels: int, duration: float) -> dict:
    noises = [c["noise_floor_db"] for c in chunks if _audible(c["noise_floor_db"])]
    louds = [c["lufs"] for c in chunks if c["lufs"] is not None]
    median_noise = float(np.median(noises)) if noises else float("nan")
    for chunk in chunks:
        noise = chunk["noise_floor_db"]
        chunk["noisy"] = (
            _audible(noise)
            and math.isfinite(median_noise)
            and noise > median_noise + _NOISY_ABOVE_MEDIAN_DB
        )
    span = (max(noises) - min(noises)) if len(noises) >= 2 else 0.0
    if louds and math.isfinite(median_noise):
        dynamic = float(np.percentile(louds, 95)) - median_noise
    else:
        dynamic = float("nan")
    integrated = meter.integrated() if meter is not None else float("nan")
    return {
        "sample_rate": sr,
        "channels": channels,
        "duration": round(duration, 3),
        "integrated_lufs": _round(integrated),
        "true_peak_db": _round(lin_to_db(peak)),
        "noise_floor_db": _round(median_noise),
        "dynamic_range_db": _round(dynamic),
        "noise_floor_span_db": _round(span),
        "chunks": chunks,
    }


def _round(value: float):
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), 2)
