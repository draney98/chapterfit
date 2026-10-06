"""Block helpers shared by the stages, the scan, and the full-file worker.

Audio is float32 (or float64) shaped ``(samples,)`` or ``(samples, channels)``,
in roughly the -1..1 range. Pedalboard wants the transpose of that.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.signal import resample_poly

# Channel weights from ITU-R BS.1770 for L, R, C, Ls, Rs.
_CHANNEL_GAIN = (1.0, 1.0, 1.0, 1.41, 1.41)


def as_2d(audio) -> np.ndarray:
    """``(samples, channels)`` float64, at least one channel."""
    x = np.asarray(audio, dtype=np.float64)
    if x.ndim == 1:
        return x.reshape(-1, 1)
    if x.ndim != 2:
        raise ValueError(f"audio must be 1-D or 2-D, got shape {x.shape}")
    return x


def like(original, processed: np.ndarray):
    """Return ``processed`` with the same rank as ``original``."""
    out = np.asarray(processed, dtype=np.float32)
    if np.ndim(original) == 1:
        return out[:, 0] if out.ndim == 2 else out
    return out


def db_to_lin(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def lin_to_db(value: float) -> float:
    return float(20.0 * math.log10(max(value, 1e-12)))


def true_peak(audio, sr: int) -> float:
    """4x-oversampled peak as a linear amplitude. 0 when the block is empty."""
    x = as_2d(audio)
    if len(x) == 0:
        return 0.0
    peak = 0.0
    step = max(1, int(sr))
    for start in range(0, len(x), step):
        part = x[start:start + step]
        up = resample_poly(part, 4, 1, axis=0)
        peak = max(peak, float(np.max(np.abs(up))))
    return peak


def true_peak_db(audio, sr: int) -> float:
    return lin_to_db(true_peak(audio, sr))


def gain_for_target(integrated_lufs: float, target_lufs: float,
                    max_gain_db: float = 24.0) -> float:
    """One gain for a whole file so it lands on the LUFS target.

    The limiter holds the peak ceiling. Silence is left alone.
    """
    if not math.isfinite(integrated_lufs) or integrated_lufs < -70.0:
        return 0.0
    gain = float(target_lufs) - float(integrated_lufs)
    return float(min(gain, max_gain_db))


def crossfade(previous, following) -> np.ndarray:
    """Linear blend of two overlap regions, both ``(samples, channels)``.

    The two sides are the same audio, processed in neighbouring chunks, so
    the blend has to leave a constant signal unchanged. An equal-power curve
    would bump the level wherever the two copies agree.
    """
    a = as_2d(previous)
    b = as_2d(following)
    n = min(len(a), len(b))
    if n == 0:
        return a[:0]
    t = np.linspace(0.0, 1.0, n, dtype=np.float64)[:, None]
    return (a[:n] * (1.0 - t) + b[:n] * t).astype(np.float32)


def k_weight(audio, sr: int) -> np.ndarray:
    """BS.1770 K-weighting, via pyloudnorm's filter design."""
    from pyloudnorm.iirfilter import IIRfilter

    x = as_2d(audio).astype(np.float64, copy=True)
    shelf = IIRfilter(4.0, 1.0 / np.sqrt(2.0), 1500.0, sr, "high_shelf")
    high = IIRfilter(0.0, 0.5, 38.0, sr, "high_pass")
    for ch in range(x.shape[1]):
        x[:, ch] = high.apply_filter(shelf.apply_filter(x[:, ch]))
    return x


class StreamingLoudness:
    """Integrated LUFS over a file that never sits in memory at once.

    400 ms blocks, 100 ms hop, absolute gate at -70 LUFS and a relative
    gate 10 LU below the ungated mean. Same sums as pyloudnorm's meter,
    with the leftover tail of each chunk carried into the next one.
    """

    def __init__(self, sr: int):
        self.sr = int(sr)
        self.block = max(1, int(round(0.400 * self.sr)))
        self.hop = max(1, int(round(0.100 * self.sr)))
        self._pending = np.zeros((0, 1), dtype=np.float64)
        self.powers: list[float] = []

    def add(self, audio) -> None:
        weighted = k_weight(audio, self.sr)
        data = (np.concatenate([self._pending, weighted], axis=0)
                if len(self._pending) else weighted)
        channels = data.shape[1]
        pos = 0
        while pos + self.block <= len(data):
            block = data[pos:pos + self.block]
            power = 0.0
            for ch in range(min(channels, len(_CHANNEL_GAIN))):
                power += _CHANNEL_GAIN[ch] * float(np.mean(block[:, ch] ** 2))
            self.powers.append(power)
            pos += self.hop
        self._pending = data[pos:]

    def integrated(self) -> float:
        if not self.powers:
            return float("nan")
        power = np.asarray(self.powers, dtype=np.float64)
        loud = -0.691 + 10.0 * np.log10(np.maximum(power, 1e-20))
        absolute = loud >= -70.0
        if not np.any(absolute):
            return float("nan")
        ungated = -0.691 + 10.0 * math.log10(float(np.mean(power[absolute])))
        relative = ungated - 10.0
        keep = loud > max(relative, -70.0)
        if not np.any(keep):
            return float("nan")
        return float(-0.691 + 10.0 * math.log10(float(np.mean(power[keep]))))
