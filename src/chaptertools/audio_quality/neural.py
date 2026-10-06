"""Optional neural stages. Torch stays in a child process.

``apply`` writes a short WAV and runs this module again, so importing
DeepFilterNet never happens inside the GUI process. A full-file job can
hold one child open with :class:`NeuralSession` and the stages will use it.
"""

from __future__ import annotations

import contextlib
import contextvars
import importlib.util
import json
import os
import select
import subprocess
import sys
import tempfile
import threading

from chaptertools import file_log

_SESSION: contextvars.ContextVar[NeuralSession | None] = contextvars.ContextVar(
    "neural_session", default=None)
_live_lock = threading.Lock()
_live: NeuralSession | None = None


class NeuralUnavailable(RuntimeError):
    """The optional model is not installed, or the child process failed."""


def deepfilter_available() -> bool:
    return importlib.util.find_spec("df") is not None


def load_deepfilter():
    """Import DeepFilterNet.

    Release 0.5 still imports ``torchaudio.backend.common``, which current
    torchaudio removed. The names are only needed so the import succeeds;
    this app never calls DeepFilterNet's file loader.
    """
    _patch_torchaudio()
    from df.enhance import enhance, init_df

    return enhance, init_df


def _patch_torchaudio() -> None:
    import types

    try:
        import torchaudio
    except ImportError:
        return
    try:
        import importlib

        importlib.import_module("torchaudio.backend.common")
        return
    except ModuleNotFoundError:
        pass

    class AudioMetaData:
        def __init__(self, sample_rate=0, num_frames=0, num_channels=0,
                     bits_per_sample=0, encoding=""):
            self.sample_rate = sample_rate
            self.num_frames = num_frames
            self.num_channels = num_channels
            self.bits_per_sample = bits_per_sample
            self.encoding = encoding

    backend = types.ModuleType("torchaudio.backend")
    common = types.ModuleType("torchaudio.backend.common")
    common.AudioMetaData = AudioMetaData
    backend.common = common
    sys.modules["torchaudio.backend"] = backend
    sys.modules["torchaudio.backend.common"] = common
    if not hasattr(torchaudio, "info"):
        torchaudio.info = lambda *_args, **_kwargs: AudioMetaData()


def resemble_available() -> bool:
    return importlib.util.find_spec("resemble_enhance") is not None


def bind_session(session: NeuralSession):
    """Make ``apply`` use this child until the context exits."""
    class _Bind:
        def __enter__(self):
            self.token = _SESSION.set(session)
            return session

        def __exit__(self, exc_type, exc, tb):
            _SESSION.reset(self.token)
            return False

    return _Bind()


def apply(kind: str, audio, sr: int, strength: float):
    """Run ``kind`` (``deepfilter`` or ``resemble``) on one block."""
    session = _SESSION.get()
    if session is not None:
        return session.apply(kind, audio, sr, strength)
    if os.environ.get("CHAPTERFIT_NEURAL_INLINE") == "1":
        return _inline(kind, audio, sr, strength)
    return _one_shot(kind, audio, sr, strength)


class NeuralSession:
    """One long-lived child. The model loads once for the whole file."""

    def __init__(self) -> None:
        self.user_cancelled = False
        self.log_paused = False
        self._stderr = None
        self._token = None
        self.proc = None
        self._spawn()
        _register(self)

    def _spawn(self) -> None:
        from chaptertools.file_log import LOG_PATH
        from chaptertools.util import package_env

        self._close_stderr()
        self._stderr = open(LOG_PATH, "ab", buffering=0)  # noqa: SIM115
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "chaptertools.audio_quality.neural", "--serve"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr,
            text=True,
            env=package_env(),
        )
        self.log_paused = False

    def _close_stderr(self) -> None:
        handle = self._stderr
        self._stderr = None
        if handle is not None:
            handle.close()

    def __enter__(self):
        self._token = _SESSION.set(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._token is not None:
            _SESSION.reset(self._token)
        self.close()
        return False

    def apply(self, kind: str, audio, sr: int, strength: float):
        import soundfile as sf

        from .analysis import Cancelled, LogPaused

        if self.user_cancelled:
            raise Cancelled()
        self._ensure_running()
        with tempfile.TemporaryDirectory(prefix="chapterfit-neural-") as folder:
            src = os.path.join(folder, "in.wav")
            dest = os.path.join(folder, "out.wav")
            sf.write(src, audio, int(sr))
            if self.user_cancelled:
                raise Cancelled()
            if self.log_paused:
                raise LogPaused()
            assert self.proc is not None and self.proc.stdin is not None
            try:
                self.proc.stdin.write(json.dumps({
                    "kind": kind,
                    "in": src,
                    "out": dest,
                    "strength": float(strength),
                }) + "\n")
                self.proc.stdin.flush()
            except OSError:
                if self.user_cancelled:
                    raise Cancelled() from None
                if self.log_paused:
                    raise LogPaused() from None
                raise NeuralUnavailable("neural process exited") from None
            line = self._read_reply()
            reply = json.loads(line)
            if not reply.get("ok"):
                raise NeuralUnavailable(str(reply.get("error") or "neural stage failed"))
            processed, _ = sf.read(dest, dtype="float32", always_2d=True)
            return processed

    def _ensure_running(self) -> None:
        from .analysis import Cancelled

        if self.proc is not None and self.proc.poll() is None:
            return
        if self.user_cancelled:
            raise Cancelled()
        if self.log_paused:
            self._spawn()
            return
        raise NeuralUnavailable("neural process exited")

    def _read_reply(self) -> str:
        from .analysis import Cancelled, LogPaused

        stdout = self.proc.stdout
        if stdout is None:
            raise NeuralUnavailable("neural process returned nothing")
        while True:
            if self.user_cancelled:
                raise Cancelled()
            try:
                file_log.wait_turn()
            except Cancelled:
                raise
            if self.user_cancelled:
                raise Cancelled()
            if self.log_paused:
                raise LogPaused()
            try:
                ready, _, _ = select.select([stdout], [], [], 0.2)
            except (OSError, ValueError):
                ready = []
            if self.user_cancelled:
                raise Cancelled()
            if self.log_paused:
                if file_log.prompt_pending():
                    file_log.wait_turn()
                if self.user_cancelled:
                    raise Cancelled()
                raise LogPaused()
            if not ready:
                if self.proc.poll() is not None:
                    raise NeuralUnavailable("neural process exited")
                continue
            line = stdout.readline()
            if line:
                return line
            if self.user_cancelled:
                raise Cancelled()
            if self.log_paused:
                raise LogPaused()
            raise NeuralUnavailable("neural process returned nothing")

    def abort(self, *, for_log: bool = False) -> None:
        """Kill the child. ``for_log`` retries the chunk after the user agrees."""
        if for_log:
            self.log_paused = True
        else:
            self.user_cancelled = True
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    def close(self) -> None:
        _unregister(self)
        proc = self.proc
        if proc is not None and proc.poll() is None:
            if proc.stdin is not None:
                with contextlib.suppress(OSError):
                    proc.stdin.close()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._close_stderr()


def abort_active(*, for_log: bool = False) -> None:
    """Kill the session a cleanup job is waiting on, from any thread."""
    with _live_lock:
        session = _live
    if session is not None:
        session.abort(for_log=for_log)


def _register(session: NeuralSession) -> None:
    global _live
    with _live_lock:
        _live = session


def _unregister(session: NeuralSession) -> None:
    global _live
    with _live_lock:
        if _live is session:
            _live = None


def _pause_for_log() -> None:
    abort_active(for_log=True)


file_log.add_pause_hook(_pause_for_log)


def _one_shot(kind: str, audio, sr: int, strength: float):
    with NeuralSession() as session:
        return session.apply(kind, audio, sr, strength)


def _inline(kind: str, audio, sr: int, strength: float):
    if kind == "deepfilter":
        return _deepfilter(audio, sr, strength)
    if kind == "resemble":
        return _resemble(audio, sr, strength)
    raise NeuralUnavailable(f"unknown neural stage {kind}")


def _deepfilter(audio, sr: int, strength: float):
    try:
        import numpy as np
        import torch

        enhance, init_df = load_deepfilter()
    except ImportError as exc:
        raise NeuralUnavailable(
            "DeepFilterNet is not installed. From the chapterfit directory, run ./runme.sh"
        ) from exc
    from .signal import as_2d

    model, state, _ = init_df()
    block = as_2d(audio).T
    tensor = torch.from_numpy(np.ascontiguousarray(block)).float()
    target = int(state.sr())
    if int(sr) != target and tensor.shape[-1] > 0:
        tensor = torch.nn.functional.interpolate(
            tensor.unsqueeze(0), scale_factor=target / float(sr),
            mode="linear", align_corners=False,
        ).squeeze(0)
    wet = enhance(model, state, tensor).detach().cpu().numpy()
    if int(sr) != target and wet.shape[-1] > 0:
        wet_t = torch.from_numpy(wet).float().unsqueeze(0)
        wet = torch.nn.functional.interpolate(
            wet_t, scale_factor=float(sr) / target,
            mode="linear", align_corners=False,
        ).squeeze(0).numpy()
    wet = wet.T
    mix = (1.0 - strength) * as_2d(audio) + strength * wet
    return mix.astype("float32")


def _resemble(audio, sr: int, strength: float):
    try:
        import numpy as np
        import torch
        from resemble_enhance.enhancer.inference import enhance
    except ImportError as exc:
        raise NeuralUnavailable(
            "resemble-enhance is not installed. Its only release requires "
            "torch==2.1.1, which has no Python 3.12 wheel."
        ) from exc
    from .signal import as_2d

    x = as_2d(audio)
    # The model is mono. Each channel is enhanced, then mixed back by strength.
    out = np.empty_like(x, dtype=np.float32)
    for ch in range(x.shape[1]):
        dwav = torch.from_numpy(np.ascontiguousarray(x[:, ch])).float()
        wet, _out_sr = enhance(dwav, int(sr), device="cpu")
        wet_np = wet.detach().cpu().numpy()
        if len(wet_np) != len(x):
            wet_np = np.interp(
                np.linspace(0, 1, len(x)),
                np.linspace(0, 1, len(wet_np)),
                wet_np,
            )
        out[:, ch] = (1.0 - strength) * x[:, ch] + strength * wet_np
    return out


def _serve() -> None:
    """Read requests from stdin and write one JSON reply per request.

    DeepFilterNet's logger writes to ``sys.stdout``. That stream is moved
    to stderr before the model is imported, and replies go out on a copy
    of the original stdout. A log line that starts with the year would
    otherwise parse as a JSON number and raise ``Extra data``.
    """
    protocol = os.fdopen(
        os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr
    _use_cpu_threads()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
                _serve_one(req)
                _reply(protocol, {"ok": True})
            except Exception as exc:
                _reply(protocol, {"ok": False, "error": str(exc)})
    finally:
        protocol.close()


def _use_cpu_threads() -> None:
    """Use the cores this CPU actually has. The default can stay at one."""
    try:
        import torch
    except ImportError:
        return
    torch.set_num_threads(max(1, os.cpu_count() or 1))


def _reply(protocol, payload: dict) -> None:
    protocol.write(json.dumps(payload) + "\n")
    protocol.flush()


def _serve_one(req: dict) -> None:
    import soundfile as sf

    audio, sr = sf.read(req["in"], dtype="float32", always_2d=True)
    processed = _inline(str(req["kind"]), audio, int(sr), float(req["strength"]))
    sf.write(req["out"], processed, int(sr))


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--serve"]:
        _serve()
        return
    if len(args) != 4:
        raise SystemExit(
            "usage: python -m chaptertools.audio_quality.neural "
            "--serve | KIND IN.wav OUT.wav STRENGTH"
        )
    kind, src, dest, strength = args
    import soundfile as sf
    audio, sr = sf.read(src, dtype="float32", always_2d=True)
    processed = _inline(kind, audio, int(sr), float(strength))
    sf.write(dest, processed, int(sr))


if __name__ == "__main__":
    main()
