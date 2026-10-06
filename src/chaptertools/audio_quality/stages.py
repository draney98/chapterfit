"""One class per cleanup stage. Adding a filter is a class plus a line in
``default_chain``; the Quality tab builds its controls from ``spec``."""

from __future__ import annotations

import noisereduce as nr
import numpy as np
from pedalboard import (
    Compressor,
    Gain,
    HighpassFilter,
    HighShelfFilter,
    Limiter,
    NoiseGate,
    PeakFilter,
    Pedalboard,
)
from scipy.ndimage import median_filter
from scipy.signal import butter, sosfiltfilt

from .chain import ParamSpec, ProcessingChain, Stage, StageAction
from .signal import as_2d, db_to_lin, gain_for_target, like

_PASS1_SKIP = frozenset({"loudness", "limiter"})


def _run_board(audio, sr: int, plugins: list):
    x = as_2d(audio)
    if len(x) == 0 or not plugins:
        return like(audio, x)
    board = Pedalboard(plugins)
    block = np.ascontiguousarray(x.T, dtype=np.float32)
    processed = board(block, int(sr))
    if processed.ndim == 1:
        processed = processed.reshape(1, -1)
    return like(audio, _fit_length(processed.T, len(x)))


def _quietest(audio: np.ndarray, sr: int, seconds: float = 0.5) -> np.ndarray:
    """The quietest slice of ``audio``, used as a per-chunk noise profile."""
    x = as_2d(audio)
    n = max(1, int(seconds * sr))
    if len(x) <= n:
        return x
    hop = max(1, n // 2)
    best = 0
    best_energy = float("inf")
    for start in range(0, len(x) - n + 1, hop):
        energy = float(np.mean(x[start:start + n] ** 2))
        if energy < best_energy:
            best_energy = energy
            best = start
    return x[best:best + n]


class HighpassStage(Stage):
    name = "highpass"
    title = "High-pass"
    spec = (
        ParamSpec("cutoff_hz", "Cutoff", "float", 70.0,
                  minimum=20.0, maximum=150.0, step=1.0, unit="Hz"),
    )

    def process(self, audio, sr: int):
        cutoff = float(self.params["cutoff_hz"])
        if cutoff <= 0 or cutoff >= sr * 0.45:
            return audio
        # Two first-order sections: 12 dB/octave, enough to take rumble out
        # without a steep phase bump in the voice.
        plugins = [
            HighpassFilter(cutoff_frequency_hz=cutoff),
            HighpassFilter(cutoff_frequency_hz=cutoff),
        ]
        return _run_board(audio, sr, plugins)


class DenoiseStage(Stage):
    name = "denoise"
    title = "Denoise"
    spec = (
        ParamSpec("mode", "Mode", "choice", "spectral",
                  choices=("spectral", "deepfilter")),
        ParamSpec("strength", "Strength", "float", 0.3,
                  minimum=0.0, maximum=1.0, step=0.01),
        ParamSpec("per_segment", "Re-estimate noise per segment", "bool", False),
    )

    def __init__(self) -> None:
        super().__init__()
        self.bypass = True

    def enabled_choices(self, key: str):
        if key == "mode" and not _deepfilter_installed():
            return ("spectral",)
        return None

    def disabled_note(self, key: str) -> str:
        if key == "mode" and not _deepfilter_installed():
            return "Neural (DeepFilterNet): install the neural extra to enable"
        return ""

    def actions(self):
        return (
            StageAction("learn_noise", "Learn noise profile from selection"),
        )

    def learn_noise(self, audio, sr: int, dest: str) -> None:
        np.save(dest, as_2d(audio))
        self.extra["noise_profile"] = dest

    def process(self, audio, sr: int):
        strength = float(self.params["strength"])
        if strength <= 0:
            return audio
        mode = str(self.params["mode"])
        if mode == "deepfilter":
            from . import neural
            if not neural.deepfilter_available():
                raise neural.NeuralUnavailable(
                    "DeepFilterNet is not installed. "
                    "From the chapterfit directory, run ./runme.sh"
                )
            wet = neural.apply("deepfilter", audio, sr, strength)
            return like(audio, as_2d(wet))
        return self._spectral(audio, sr, strength)

    def _spectral(self, audio, sr: int, strength: float):
        x = as_2d(audio)
        if len(x) < int(0.2 * sr):
            return audio
        profile = self._profile(x, sr)
        reduced = _reduce(x, sr, strength, profile)
        return like(audio, _fit_length(reduced, len(x)))

    def _profile(self, audio: np.ndarray, sr: int):
        if bool(self.params["per_segment"]):
            return _quietest(audio, sr)
        path = self.extra.get("noise_profile")
        if not path:
            return None
        try:
            loaded = np.load(path)
        except OSError:
            return None
        loaded = as_2d(loaded)
        if loaded.shape[1] != audio.shape[1]:
            return None
        return loaded


class DeclickStage(Stage):
    name = "declick"
    title = "De-click"
    spec = (
        ParamSpec("threshold", "Threshold", "float", 0.6,
                  minimum=0.05, maximum=1.0, step=0.01),
        ParamSpec("sensitivity", "Sensitivity", "float", 0.3,
                  minimum=0.1, maximum=1.0, step=0.01),
    )

    def __init__(self) -> None:
        super().__init__()
        self.bypass = True

    def process(self, audio, sr: int):
        x = as_2d(audio)
        if len(x) < 8:
            return audio
        window = max(3, int(sr * 0.0015) | 1)
        threshold = float(self.params["threshold"])
        sensitivity = max(0.1, float(self.params["sensitivity"]))
        out = x.copy()
        for ch in range(out.shape[1]):
            column = out[:, ch]
            med = median_filter(column, size=window, mode="nearest")
            residual = np.abs(column - med)
            mad = float(np.median(residual)) + 1e-8
            # Higher sensitivity lowers the bar, so more spikes are replaced.
            bar = max(mad * (8.0 / sensitivity) * threshold, threshold * 0.02)
            spikes = residual > bar
            column = column.copy()
            column[spikes] = med[spikes]
            out[:, ch] = column
        return like(audio, out)


class EqStage(Stage):
    name = "eq"
    title = "EQ"
    spec = (
        ParamSpec("low_freq", "Low frequency", "float", 120.0,
                  minimum=40.0, maximum=400.0, step=1.0, unit="Hz"),
        ParamSpec("low_gain", "Low gain", "float", 0.0,
                  minimum=-12.0, maximum=12.0, step=0.1, unit="dB"),
        ParamSpec("low_q", "Low Q", "float", 0.7,
                  minimum=0.3, maximum=8.0, step=0.1),
        ParamSpec("box_freq", "Low-mid frequency", "float", 250.0,
                  minimum=80.0, maximum=800.0, step=1.0, unit="Hz"),
        ParamSpec("box_gain", "Low-mid gain", "float", 0.0,
                  minimum=-12.0, maximum=12.0, step=0.1, unit="dB"),
        ParamSpec("box_q", "Low-mid Q", "float", 1.0,
                  minimum=0.3, maximum=8.0, step=0.1),
        ParamSpec("presence_freq", "Presence frequency", "float", 3000.0,
                  minimum=800.0, maximum=8000.0, step=10.0, unit="Hz"),
        ParamSpec("presence_gain", "Presence gain", "float", 0.0,
                  minimum=-12.0, maximum=12.0, step=0.1, unit="dB"),
        ParamSpec("presence_q", "Presence Q", "float", 1.0,
                  minimum=0.3, maximum=8.0, step=0.1),
        ParamSpec("air_freq", "High frequency", "float", 7000.0,
                  minimum=2000.0, maximum=16000.0, step=10.0, unit="Hz"),
        ParamSpec("air_gain", "High gain", "float", 0.0,
                  minimum=-12.0, maximum=12.0, step=0.1, unit="dB"),
        ParamSpec("air_q", "High Q", "float", 0.7,
                  minimum=0.3, maximum=8.0, step=0.1),
        ParamSpec("air_shape", "High shape", "choice", "peak",
                  choices=("peak", "shelf")),
        ParamSpec("trim_db", "Input trim", "float", 0.0,
                  minimum=-12.0, maximum=12.0, step=0.1, unit="dB"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.bypass = True

    def process(self, audio, sr: int):
        plugins = []
        trim = float(self.params.get("trim_db", 0.0))
        if abs(trim) >= 0.05:
            plugins.append(Gain(gain_db=trim))
        bands = (
            ("low_freq", "low_gain", "low_q"),
            ("box_freq", "box_gain", "box_q"),
            ("presence_freq", "presence_gain", "presence_q"),
        )
        nyquist = sr * 0.45
        for freq_key, gain_key, q_key in bands:
            gain = float(self.params[gain_key])
            freq = float(self.params[freq_key])
            if abs(gain) < 0.05 or freq <= 10 or freq >= nyquist:
                continue
            plugins.append(PeakFilter(
                cutoff_frequency_hz=freq,
                gain_db=gain,
                q=float(self.params[q_key]),
            ))
        air_gain = float(self.params["air_gain"])
        air_freq = float(self.params["air_freq"])
        if abs(air_gain) >= 0.05 and 10 < air_freq < nyquist:
            q = float(self.params["air_q"])
            if self.params["air_shape"] == "shelf":
                plugins.append(HighShelfFilter(
                    cutoff_frequency_hz=air_freq, gain_db=air_gain, q=q))
            else:
                plugins.append(PeakFilter(
                    cutoff_frequency_hz=air_freq, gain_db=air_gain, q=q))
        return _run_board(audio, sr, plugins)


class DeesserStage(Stage):
    name = "deesser"
    title = "De-esser"
    spec = (
        ParamSpec("frequency_hz", "Frequency", "float", 6500.0,
                  minimum=3000.0, maximum=12000.0, step=50.0, unit="Hz"),
        ParamSpec("threshold_db", "Threshold", "float", -8.0,
                  minimum=-40.0, maximum=-6.0, step=0.5, unit="dB"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.bypass = True

    def process(self, audio, sr: int):
        x = as_2d(audio)
        if len(x) < 32:
            return audio
        freq = float(self.params["frequency_hz"])
        low = max(80.0, freq / 1.4)
        high = min(freq * 1.4, sr * 0.45)
        if high <= low * 1.05:
            return audio
        sos = butter(2, [low, high], btype="bandpass", fs=sr, output="sos")
        band = sosfiltfilt(sos, x, axis=0)
        win = max(1, int(sr * 0.005))
        kernel = np.ones(win, dtype=np.float64) / win
        env = np.empty_like(band)
        for ch in range(band.shape[1]):
            env[:, ch] = np.convolve(np.abs(band[:, ch]), kernel, mode="same")
        env_db = 20.0 * np.log10(env + 1e-8)
        over = np.clip(env_db - float(self.params["threshold_db"]), 0.0, 12.0)
        gain = np.power(10.0, -over / 20.0)
        y = x - band + band * gain
        return like(audio, y)


class CompressorStage(Stage):
    name = "compressor"
    title = "Compressor"
    spec = (
        ParamSpec("threshold_db", "Threshold", "float", -20.0,
                  minimum=-40.0, maximum=0.0, step=0.5, unit="dB"),
        ParamSpec("ratio", "Ratio", "float", 1.8,
                  minimum=1.0, maximum=8.0, step=0.1),
        ParamSpec("attack_ms", "Attack", "float", 25.0,
                  minimum=1.0, maximum=80.0, step=1.0, unit="ms"),
        ParamSpec("release_ms", "Release", "float", 160.0,
                  minimum=20.0, maximum=500.0, step=1.0, unit="ms"),
        ParamSpec("makeup_db", "Makeup", "float", 0.0,
                  minimum=-6.0, maximum=12.0, step=0.1, unit="dB"),
    )

    def process(self, audio, sr: int):
        plugins = [
            Compressor(
                threshold_db=float(self.params["threshold_db"]),
                ratio=float(self.params["ratio"]),
                attack_ms=float(self.params["attack_ms"]),
                release_ms=float(self.params["release_ms"]),
            ),
            Gain(gain_db=float(self.params["makeup_db"])),
        ]
        return _run_board(audio, sr, plugins)


class NoiseGateStage(Stage):
    """Ducks quiet gaps. It does not delete time, so chapter marks stay put.

    ``hold_ms`` is how long the gate stays open after the signal falls
    below the threshold (pedalboard's release time).
    """

    name = "gate"
    title = "Noise gate"
    spec = (
        ParamSpec("threshold_db", "Threshold", "float", -55.0,
                  minimum=-80.0, maximum=-20.0, step=1.0, unit="dB"),
        ParamSpec("hold_ms", "Hold", "float", 400.0,
                  minimum=20.0, maximum=1000.0, step=5.0, unit="ms"),
    )

    def __init__(self) -> None:
        super().__init__()
        self.bypass = True

    def process(self, audio, sr: int):
        plugins = [
            NoiseGate(
                threshold_db=float(self.params["threshold_db"]),
                ratio=12.0,
                attack_ms=5.0,
                release_ms=float(self.params["hold_ms"]),
            ),
        ]
        return _run_board(audio, sr, plugins)


class LoudnessStage(Stage):
    """Applies one gain. The full-file worker sets ``extra['fixed_gain']``
    from a first pass; a preview measures the block it was given."""

    name = "loudness"
    title = "Loudness"
    spec = (
        ParamSpec("target_lufs", "Target", "float", -18.0,
                  minimum=-30.0, maximum=-12.0, step=0.5, unit="LUFS"),
        ParamSpec("true_peak_db", "True-peak ceiling", "float", -3.0,
                  minimum=-6.0, maximum=-0.5, step=0.1, unit="dBTP"),
    )

    def process(self, audio, sr: int):
        x = as_2d(audio)
        fixed = self.extra.get("fixed_gain")
        gain = self._measure_gain(x, sr) if fixed is None else float(fixed)
        return like(audio, x * db_to_lin(gain))

    def _measure_gain(self, audio: np.ndarray, sr: int) -> float:
        if len(audio) < int(0.4 * sr):
            return 0.0
        import pyloudnorm
        meter = pyloudnorm.Meter(sr)
        try:
            lufs = float(meter.integrated_loudness(np.clip(audio, -1.0, 1.0)))
        except ValueError:
            return 0.0
        return gain_for_target(lufs, float(self.params["target_lufs"]))


class LimiterStage(Stage):
    name = "limiter"
    title = "Limiter"
    spec = (
        ParamSpec("ceiling_db", "Ceiling", "float", -3.0,
                  minimum=-6.0, maximum=-0.1, step=0.1, unit="dB"),
        ParamSpec("release_ms", "Release", "float", 100.0,
                  minimum=10.0, maximum=300.0, step=1.0, unit="ms"),
    )

    def process(self, audio, sr: int):
        plugins = [
            Limiter(
                threshold_db=float(self.params["ceiling_db"]),
                release_ms=float(self.params["release_ms"]),
            ),
        ]
        limited = np.asarray(_run_board(audio, sr, plugins), dtype=np.float32)
        ceiling = db_to_lin(float(self.params["ceiling_db"]))
        return like(audio, np.clip(limited, -ceiling, ceiling))


class EnhanceStage(Stage):
    """Optional resemble-enhance pass. Bypassed unless a preset turns it on."""

    name = "enhance"
    title = "Neural enhance"
    spec = (
        ParamSpec("strength", "Strength", "float", 0.4,
                  minimum=0.0, maximum=1.0, step=0.01),
    )

    def __init__(self) -> None:
        super().__init__()
        self.bypass = True

    def availability(self) -> tuple[bool, str]:
        from . import neural
        if neural.resemble_available():
            return True, ""
        return False, "install the neural extra to enable"

    def process(self, audio, sr: int):
        strength = float(self.params["strength"])
        if strength <= 0:
            return audio
        from . import neural
        if not neural.resemble_available():
            raise neural.NeuralUnavailable(
                "resemble-enhance is not installed. Its only release requires "
                "torch==2.1.1, which has no Python 3.12 wheel."
            )
        wet = neural.apply("resemble", audio, sr, strength)
        return like(audio, as_2d(wet))


def default_chain() -> ProcessingChain:
    return ProcessingChain([
        HighpassStage(),
        DenoiseStage(),
        EnhanceStage(),
        DeclickStage(),
        EqStage(),
        DeesserStage(),
        CompressorStage(),
        NoiseGateStage(),
        LoudnessStage(),
        LimiterStage(),
    ])


def pass1_skip() -> set[str]:
    """Stages the first full-file pass must not run.

    Loudness is one gain for the whole file, computed after this pass.
    The limiter runs with that gain on the second pass.
    """
    return set(_PASS1_SKIP)


def _deepfilter_installed() -> bool:
    from . import neural
    return neural.deepfilter_available()


def _fit_length(audio: np.ndarray, n: int) -> np.ndarray:
    y = as_2d(audio)
    if len(y) == n:
        return y
    if len(y) > n:
        return y[:n]
    pad = np.zeros((n - len(y), y.shape[1]), dtype=y.dtype)
    return np.concatenate([y, pad], axis=0)


def _reduce(audio: np.ndarray, sr: int, strength: float, profile):
    """Spectral denoise. ``audio`` is ``(samples, channels)``."""
    y = np.ascontiguousarray(audio.T, dtype=np.float32)
    y_noise = None
    if profile is not None:
        y_noise = np.ascontiguousarray(as_2d(profile).T, dtype=np.float32)
        if y_noise.shape[0] == 1 and y.shape[0] == 1:
            y_noise = y_noise[0]
    if y.shape[0] == 1:
        y = y[0]
    reduced = nr.reduce_noise(
        y=y,
        sr=int(sr),
        stationary=True,
        y_noise=y_noise,
        prop_decrease=float(strength),
        use_tqdm=False,
        n_jobs=1,
        use_torch=False,
    )
    reduced = np.asarray(reduced, dtype=np.float32)
    if reduced.ndim == 1:
        reduced = reduced.reshape(1, -1)
    return reduced.T
