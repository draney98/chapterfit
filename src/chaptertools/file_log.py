"""A flushed log at /tmp/chapterfit.log.

The window and the neural child both append here, so the last line is on
disk when a job sticks. Crossing each 50 MB step stops in-flight work
until the user says whether to keep going.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime

LOG_PATH = "/tmp/chapterfit.log"
STEP = 50 * 1024 * 1024

_lock = threading.Lock()
_hooks: list = []
_acknowledged = 0
_prompt_step = 0
_generation = 0
_keep = True
_event = threading.Event()
_event.set()


def add_pause_hook(fn) -> None:
    """``fn()`` runs on the thread that notices a new size step."""
    _hooks.append(fn)


def size() -> int:
    try:
        return os.path.getsize(LOG_PATH)
    except OSError:
        return 0


def write(text: str) -> None:
    """Append ``text`` and flush. Callers include the trailing newline."""
    with _lock, open(LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()


def open_session() -> str:
    """Record this process at the end of the log. Returns the path."""
    stamp = datetime.now().strftime("%H:%M:%S")
    write(f"{stamp} session {os.getpid()}\n")
    return LOG_PATH


def prompt_pending() -> int:
    """The size step waiting for an answer, or 0."""
    return _prompt_step


def consider() -> int:
    """Arm a prompt when the file has passed the last answered step.

    Returns that step, or 0 when nothing new is waiting. Pause hooks run
    once, on the thread that arms the prompt.
    """
    global _prompt_step, _generation
    step = size() // STEP
    armed = False
    with _lock:
        if _prompt_step:
            return _prompt_step
        if step <= _acknowledged:
            return 0
        _generation += 1
        _prompt_step = step
        _event.clear()
        armed = True
    if armed:
        for hook in list(_hooks):
            hook()
    return _prompt_step


def resolve(keep_going: bool) -> None:
    """Record the answer and release workers waiting on the prompt."""
    global _acknowledged, _prompt_step, _keep
    with _lock:
        if keep_going:
            _acknowledged = max(_acknowledged, _prompt_step)
        _keep = keep_going
        _prompt_step = 0
    _event.set()


def wait_turn() -> None:
    """Block while a size prompt is open. ``Cancelled`` means the user said no."""
    from chaptertools.audio_quality.analysis import Cancelled

    seen = _generation
    consider()
    if prompt_pending() or not _event.is_set():
        _event.wait()
    if _generation != seen and not _keep:
        raise Cancelled()


def reset_for_tests() -> None:
    """Clear the size gate. Tests use this so one case cannot arm the next."""
    global _acknowledged, _prompt_step, _generation, _keep
    with _lock:
        _acknowledged = 0
        _prompt_step = 0
        _generation = 0
        _keep = True
    _event.set()
