"""Full-file cleanup: decode once, process in chunks, encode once.

The library file is never written here. ``replace_library`` is the separate
confirm that copies a finished MP3 over the source, after keeping the
original in the work folder.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time

from chaptertools import file_log
from chaptertools.util import clock

CHUNK_SECONDS = 600.0
NEURAL_CHUNK_SECONDS = 15.0
OVERLAP_SECONDS = 1.0
_BITRATE = re.compile(r"\d{2,3}k")


class SpaceError(RuntimeError):
    """Not enough free disk for the temporary WAV."""


class QualityJobs:
    """One background job at a time. Events are ``(kind, name, payload)``
    for the Tk thread to drain; the worker never touches a widget."""

    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._busy = False

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def start(self, name: str, fn) -> bool:
        """``fn(cancel, progress)`` runs on a daemon thread."""
        with self._lock:
            if self._busy:
                self.events.put(("refused", name, "a quality job is already running"))
                return False
            self._busy = True
            self.cancel_event = threading.Event()
            cancel = self.cancel_event

        def run():
            from .analysis import Cancelled
            try:
                result = fn(cancel, self._progress)
                self.events.put(("done", name, result))
            except Cancelled:
                self.events.put(("cancelled", name, None))
            except Exception as exc:
                self.events.put(("error", name, f"{type(exc).__name__}: {exc}"))
            finally:
                with self._lock:
                    self._busy = False

        threading.Thread(target=run, name="quality", daemon=True).start()
        return True

    def cancel(self) -> None:
        self.cancel_event.set()

    def _progress(self, fraction: float, eta, message: str) -> None:
        self.events.put(("progress", "", (fraction, eta, message)))


def decoded_wav_path(work_dir: str, source: str) -> str:
    stem = os.path.splitext(os.path.basename(source))[0]
    return os.path.join(work_dir, stem + ".quality.wav")


def output_mp3_path(work_dir: str, source: str) -> str:
    stem = os.path.splitext(os.path.basename(source))[0]
    return os.path.join(work_dir, stem + ".quality.mp3")


def noise_profile_path(work_dir: str, source: str) -> str:
    return os.path.join(work_dir, os.path.basename(source) + ".noise.npy")


def clean_bitrate(value: str | None) -> str:
    text = (value or "").strip().lower()
    if _BITRATE.fullmatch(text):
        return text
    return "64k"


def nearest_bitrate(bits_per_second: int | None) -> str:
    choices = (48, 64, 96, 128, 160, 192, 256, 320)
    if not bits_per_second or bits_per_second <= 0:
        return "64k"
    kbps = bits_per_second / 1000.0
    best = min(choices, key=lambda item: abs(item - kbps))
    return f"{best}k"


def source_facts(path: str) -> dict:
    """Duration, rate, channels, and bitrate from ffprobe. Empty when it fails."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels,bit_rate",
        "-show_entries", "format=duration,bit_rate",
        "-of", "json", path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        data = json.loads(result.stdout or "{}")
    except (OSError, ValueError):
        return {}
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    def _num(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    rate = _num(stream.get("sample_rate"))
    channels = _num(stream.get("channels"))
    duration = _num(fmt.get("duration"))
    bitrate = _num(stream.get("bit_rate") or fmt.get("bit_rate"))
    return {
        "sample_rate": int(rate) if rate else None,
        "channels": int(channels) if channels else None,
        "duration": duration,
        "bit_rate": int(bitrate) if bitrate else None,
    }


def read_segment(path: str, start: float, duration: float):
    """``(audio, sample_rate)`` for a preview. Audio is float32."""
    import soundfile as sf
    with sf.SoundFile(path) as handle:
        sr = int(handle.samplerate)
        origin = min(len(handle), max(0, int(start * sr)))
        handle.seek(origin)
        count = max(1, int(duration * sr))
        data = handle.read(count, dtype="float32", always_2d=False)
        return data, sr


def ensure_decoded(source: str, work_dir: str, cancel, progress=None) -> str:
    """Decode ``source`` to a WAV in the work folder, reusing it when the
    source has not changed."""
    os.makedirs(work_dir, exist_ok=True)
    dest = decoded_wav_path(work_dir, source)
    meta_path = dest + ".meta.json"
    stat = os.stat(source)
    meta = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    if os.path.isfile(dest) and _meta_matches(meta_path, meta):
        return dest
    facts = source_facts(source)
    _require_space(work_dir, facts)
    partial = _beside(dest)
    if progress:
        progress(0.0, None, "decoding")
    _ffmpeg([
        "ffmpeg", "-y", "-v", "error", "-i", source,
        "-c:a", "pcm_s16le", partial,
    ], cancel)
    os.replace(partial, dest)
    _write_meta(meta_path, meta)
    return dest


def process_file(source: str, work_dir: str, snapshot: dict, bitrate: str,
                 cancel, progress=None, *,
                 chunk_seconds: float = CHUNK_SECONDS,
                 overlap_seconds: float = OVERLAP_SECONDS) -> dict:
    """Decode, process, and encode to the work folder. Returns paths and the
    gain that was applied. Raises ``Cancelled`` before the MP3 is published."""
    from . import neural
    from .stages import default_chain

    chain = default_chain()
    chain.apply(snapshot)
    _reject_missing_neural(chain, neural)
    wav = ensure_decoded(source, work_dir, cancel, progress)
    return process_wav(
        wav, source, work_dir, snapshot, bitrate, cancel, progress,
        chunk_seconds=chunk_seconds, overlap_seconds=overlap_seconds,
    )


def process_wav(wav: str, source: str, work_dir: str, snapshot: dict,
                bitrate: str, cancel, progress=None, *,
                chunk_seconds: float = CHUNK_SECONDS,
                overlap_seconds: float = OVERLAP_SECONDS) -> dict:
    """Two passes over an existing WAV. ``source`` supplies tags and chapters."""
    from .analysis import Cancelled
    from .signal import lin_to_db
    from . import neural
    from .stages import default_chain, pass1_skip

    chain = default_chain()
    chain.apply(snapshot)
    _reject_missing_neural(chain, neural)
    os.makedirs(work_dir, exist_ok=True)
    pass1 = os.path.join(
        work_dir, os.path.splitext(os.path.basename(source))[0] + ".quality.pass1.wav")
    dest = output_mp3_path(work_dir, source)
    partial_mp3 = _beside(dest)
    started = time.monotonic()

    def report(fraction: float, message: str) -> None:
        if progress is None:
            return
        fraction = max(0.0, min(1.0, float(fraction)))
        eta = None
        if fraction >= 0.02:
            elapsed = time.monotonic() - started
            eta = elapsed * (1.0 - fraction) / fraction
        progress(fraction, eta, message)

    try:
        session = _neural_session(chain, neural)
        try:
            meter, peak = _pass_one(
                wav, pass1, chain, pass1_skip(), cancel, report,
                render_chunk_seconds(chain, chunk_seconds), overlap_seconds, session,
            )
            gain = _arm_gain(chain, meter)
            report(0.75, "encoding")
            _pass_two(
                pass1, partial_mp3, source, chain, clean_bitrate(bitrate),
                cancel, report, chunk_seconds, overlap_seconds, session,
            )
        finally:
            if session is not None:
                session.close()
        os.replace(partial_mp3, dest)
    except Cancelled:
        _remove(pass1, partial_mp3)
        raise
    except Exception:
        _remove(partial_mp3)
        raise
    else:
        _remove(pass1)
    report(1.0, "done")
    return {
        "mp3": dest,
        "wav": wav,
        "gain_db": gain,
        "integrated_lufs": meter.integrated(),
        "true_peak_db": lin_to_db(peak),
    }


def replace_library(source: str, cleaned: str, work_dir: str) -> str:
    """Keep the first original in the work folder, then copy ``cleaned``
    onto the library path. Returns the original's path."""
    source = os.path.abspath(source)
    os.makedirs(work_dir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(source))
    original = os.path.join(work_dir, f"{stem}.original{ext}")
    if not os.path.exists(original):
        shutil.copy2(source, original)
    temporary = source + ".replacing"
    shutil.copy2(cleaned, temporary)
    os.replace(temporary, source)
    return original


def render_chunk_seconds(chain, requested: float) -> float:
    """DeepFilter runs one forward pass per chunk, so keep those short.

    A ten-minute block is one model call. On this machine that call can
    sit for hours with no progress line and no return to the cancel check.
    """
    denoise = chain.stage("denoise")
    if not denoise.bypass and denoise.params.get("mode") == "deepfilter":
        return min(float(requested), NEURAL_CHUNK_SECONDS)
    return float(requested)


def render_chunked(path: str, chain, *, skip: set[str] | None = None,
                   only: set[str] | None = None,
                   chunk_seconds: float = CHUNK_SECONDS,
                   overlap_seconds: float = OVERLAP_SECONDS,
                   cancel=None, on_chunk=None):
    """Yield ``(emitted, fraction)`` covering ``path`` exactly once.

    ``on_chunk(start, end, duration)`` runs before the chunk is processed,
    so a slow stage still leaves a progress line.
    """
    import soundfile as sf
    import numpy as np
    from .analysis import Cancelled, LogPaused, check_cancel
    with sf.SoundFile(path) as handle:
        sr = int(handle.samplerate)
        total = len(handle)
        if total == 0:
            return
        duration = total / float(sr)
        chunk_n = max(1, int(chunk_seconds * sr))
        overlap_n = int(overlap_seconds * sr)
        if overlap_n >= chunk_n:
            overlap_n = chunk_n // 4
        hop = max(1, chunk_n - overlap_n)
        pos = 0
        prev_tail = None
        while pos < total:
            file_log.wait_turn()
            check_cancel(cancel)
            handle.seek(pos)
            data = handle.read(chunk_n, dtype="float32", always_2d=True)
            if len(data) == 0:
                break
            if on_chunk is not None:
                start = pos / float(sr)
                end = min(duration, (pos + len(data)) / float(sr))
                on_chunk(start, end, duration)
            try:
                processed = chain.process(data, sr, skip=skip, only=only)
            except LogPaused:
                continue
            processed = np.asarray(processed, dtype=np.float32)
            if processed.ndim == 1:
                processed = processed[:, None]
            at_end = pos + len(data) >= total
            emit, prev_tail = _join(processed, prev_tail, overlap_n, at_end)
            done = min(1.0, (pos + len(data)) / float(total))
            if len(emit):
                yield emit, done
            if at_end:
                break
            pos += hop


def _join(processed, prev_tail, overlap_n: int, at_end: bool):
    import numpy as np
    from .signal import crossfade
    if overlap_n <= 0 or len(processed) == 0:
        return processed, None
    if prev_tail is None:
        if at_end or len(processed) <= overlap_n:
            return processed, None
        return processed[:-overlap_n], processed[-overlap_n:].copy()
    n = min(len(prev_tail), overlap_n, len(processed))
    faded = crossfade(prev_tail[:n], processed[:n])
    if at_end or len(processed) <= overlap_n:
        rest = processed[n:]
        emit = faded if len(rest) == 0 else np.concatenate([faded, rest], axis=0)
        return emit, None
    middle = processed[n:-overlap_n]
    emit = faded if len(middle) == 0 else np.concatenate([faded, middle], axis=0)
    return emit, processed[-overlap_n:].copy()


def _pass_one(wav, dest, chain, skip, cancel, report,
              chunk_seconds, overlap_seconds, session):
    import soundfile as sf
    from .signal import StreamingLoudness, true_peak
    info = sf.info(wav)
    meter = StreamingLoudness(int(info.samplerate))
    peak = 0.0
    span = float(info.duration or 0)

    def _mark(at: float) -> None:
        report(
            0.05 + 0.70 * (at / span if span else 0.0),
            f"processing {clock(at)} / {clock(span)}",
        )

    with _activating(session), sf.SoundFile(dest, "w", samplerate=info.samplerate,
                      channels=info.channels, subtype="PCM_16") as out:
        for emit, fraction in render_chunked(
            wav, chain, skip=skip, chunk_seconds=chunk_seconds,
            overlap_seconds=overlap_seconds, cancel=cancel,
            on_chunk=lambda start, end, duration: _mark(start),
        ):
            out.write(emit)
            meter.add(emit)
            peak = max(peak, true_peak(emit, int(info.samplerate)))
            _mark(fraction * span)
    return meter, peak


def _pass_two(pass1, dest_mp3, source, chain, bitrate, cancel, report,
              chunk_seconds, overlap_seconds, session):
    import soundfile as sf
    from .analysis import Cancelled
    info = sf.info(pass1)
    only = set()
    if not chain.stage("loudness").bypass:
        only.add("loudness")
    if not chain.stage("limiter").bypass:
        only.add("limiter")
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "s16le", "-ar", str(info.samplerate), "-ac", str(info.channels),
        "-i", "pipe:0",
        "-i", source,
        "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
        "-c:a", "libmp3lame", "-b:a", bitrate,
        dest_mp3,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        with _activating(session):
            for emit, fraction in render_chunked(
                pass1, chain, only=only, chunk_seconds=chunk_seconds,
                overlap_seconds=overlap_seconds, cancel=cancel,
            ):
                _write_s16(proc, emit)
                report(0.75 + 0.24 * fraction, "encoding")
        if proc.stdin is not None:
            proc.stdin.close()
        err = proc.stderr.read() if proc.stderr is not None else b""
        code = proc.wait()
    except Cancelled:
        if proc.poll() is None:
            proc.terminate()
        raise
    if code != 0:
        text = err.decode("utf-8", "replace").strip()
        raise RuntimeError(text or "ffmpeg encode failed")


def _write_s16(proc: subprocess.Popen, emit: np.ndarray) -> None:
    import numpy as np
    if proc.stdin is None:
        return
    clipped = np.clip(emit, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    try:
        proc.stdin.write(pcm.tobytes())
    except BrokenPipeError as exc:
        raise RuntimeError("ffmpeg closed the encode pipe") from exc


def _arm_gain(chain, meter: StreamingLoudness) -> float:
    from .signal import gain_for_target
    loud = chain.stage("loudness")
    gain = gain_for_target(
        meter.integrated(),
        float(loud.params["target_lufs"]),
    )
    loud.extra["fixed_gain"] = gain
    return gain


def _reject_missing_neural(chain, neural) -> None:
    denoise = chain.stage("denoise")
    enhance = chain.stage("enhance")
    if (not denoise.bypass and denoise.params.get("mode") == "deepfilter"
            and not neural.deepfilter_available()):
        raise neural.NeuralUnavailable(
            "DeepFilterNet is not installed. "
            "From the chapterfit directory, run ./runme.sh"
        )
    if not enhance.bypass and not neural.resemble_available():
        raise neural.NeuralUnavailable(
            "resemble-enhance is not installed. Its only release requires "
            "torch==2.1.1, which has no Python 3.12 wheel."
        )


def _neural_session(chain, neural):
    denoise = chain.stage("denoise")
    enhance = chain.stage("enhance")
    wants = (
        (not denoise.bypass and denoise.params.get("mode") == "deepfilter")
        or not enhance.bypass
    )
    if not wants:
        return None
    return neural.NeuralSession()


class _activating:
    """Bind a neural child to this thread for the duration of a pass."""

    def __init__(self, session):
        self.session = session
        self._bind = None

    def __enter__(self):
        if self.session is None:
            return None
        from . import neural
        self._bind = neural.bind_session(self.session)
        return self._bind.__enter__()

    def __exit__(self, exc_type, exc, tb):
        if self._bind is not None:
            self._bind.__exit__(exc_type, exc, tb)
        return False


def _beside(path: str) -> str:
    """A temporary path that keeps the extension, so ffmpeg can pick a muxer."""
    root, ext = os.path.splitext(path)
    return root + ".partial" + ext


def _remove(*paths: str) -> None:
    for path in paths:
        if path and os.path.isfile(path):
            os.remove(path)


def _require_space(directory: str, facts: dict) -> None:
    duration = facts.get("duration")
    rate = facts.get("sample_rate") or 44100
    channels = facts.get("channels") or 1
    if not duration:
        return
    wav_bytes = float(duration) * float(rate) * float(channels) * 2.0
    free = shutil.disk_usage(directory).free
    if free < wav_bytes * 2.0:
        raise SpaceError(
            f"Need about {wav_bytes * 2 / 1e9:.1f} GB free for the temporary "
            f"WAV in {directory}; {free / 1e9:.1f} GB is free."
        )


def _ffmpeg(cmd: list[str], cancel) -> None:
    from .analysis import Cancelled
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        while proc.poll() is None:
            file_log.wait_turn()
            if cancel is not None and cancel.is_set():
                proc.terminate()
                raise Cancelled()
            time.sleep(0.2)
    except Cancelled:
        if proc.poll() is None:
            proc.terminate()
        raise
    if proc.returncode != 0:
        err = b""
        if proc.stderr is not None:
            err = proc.stderr.read()
        text = err.decode("utf-8", "replace").strip()
        raise RuntimeError(text or "ffmpeg failed")


def _meta_matches(path: str, meta: dict) -> bool:
    try:
        with open(path, encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, ValueError):
        return False
    return (
        saved.get("size") == meta["size"]
        and saved.get("mtime_ns") == meta["mtime_ns"]
    )


def _write_meta(path: str, meta: dict) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(meta, handle)
