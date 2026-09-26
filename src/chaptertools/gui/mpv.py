"""mpv playback control with JSON-IPC time polling (stdlib only)."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
from contextlib import suppress


def parse_reply(line: str) -> dict | None:
    """Return the object if the line is an IPC reply (events return None)."""
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if isinstance(obj, dict) and "request_id" in obj:
        return obj
    return None


class Player:
    """One mpv at a time, addressable over its IPC socket."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.sock_path: str | None = None
        self._sock: socket.socket | None = None
        self._buf = ""
        self._next_id = 0
        self._time_rid: int | None = None   # outstanding playback-time ask
        self._time: float | None = None     # newest answer
        self._paused = False

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def paused(self) -> bool:
        return self._paused

    def play(self, path: str, start: str | None = None, length: int | None = None,
             extra: list[str] | None = None):
        self.stop()
        self.sock_path = os.path.join(
            tempfile.gettempdir(),
            f"chaptergui-{os.getpid()}-{time.time_ns() % 1000000}.sock")
        argv = ["mpv", "--no-video", "--no-terminal",
                f"--input-ipc-server={self.sock_path}"]
        if start:
            argv.append(f"--start={start}")
        if length:
            argv.append(f"--length={length}")
        argv.extend(extra or [])
        argv.append(path)
        self.proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        self._paused = False
        self._connect()

    def _connect(self) -> bool:
        """One non-blocking attempt; the pump simply tries again next tick
        (the socket file can appear before mpv accepts on it)."""
        if self._sock is not None:
            return True
        if self.sock_path is None or not self.alive \
                or not os.path.exists(self.sock_path):
            return False
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(self.sock_path)
        except OSError:
            s.close()
            return False
        s.setblocking(False)
        self._sock, self._buf, self._time_rid = s, "", None
        return True

    def poll(self) -> float | None:
        """Newest known playback time in seconds, or None when unavailable.
        Never waits: it reads what mpv already sent and asks again."""
        if not self.alive:
            self.stop()
            return None
        if not self._connect():
            return None
        self._drain()
        now = time.monotonic()
        if self._time_rid is None or now - self._time_asked > 1.0:
            self._time_rid = self._send("get_property", "playback-time")
            self._time_asked = now
        return self._time

    _time_asked = 0.0

    def toggle_pause(self):
        self._paused = not self._paused
        self._send("set_property", "pause", self._paused)

    def seek(self, delta: float):
        """Move relative to the current position; works while paused."""
        self._send("seek", delta, "exact")

    def seek_to(self, seconds: float):
        """Jump to an absolute time. Works while paused."""
        seconds = max(0.0, seconds)
        self._send("seek", seconds, "absolute")
        self._time = seconds

    def stop(self):
        proc, self.proc = self.proc, None
        sock, self._sock = self._sock, None
        path, self.sock_path = self.sock_path, None
        if sock is not None:
            with suppress(OSError):
                sock.close()
        if path and os.path.exists(path):
            with suppress(OSError):
                os.unlink(path)
        self._buf, self._time_rid, self._time = "", None, None
        self._paused = False
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ------------------------------------------------------------- ipc --
    def _send(self, *command) -> int | None:
        """Fire a command; replies are read later by _drain."""
        if self._sock is None:
            return None
        self._next_id += 1
        rid = self._next_id
        msg = json.dumps({"command": list(command), "request_id": rid}) + "\n"
        try:
            self._sock.sendall(msg.encode())
        except BlockingIOError:
            return None  # a full socket buffer: drop this one command
        except OSError:
            self._sock = None
            return None
        return rid

    def _drain(self):
        """Read everything mpv has sent so far without waiting."""
        while self._sock is not None:
            try:
                chunk = self._sock.recv(65536)
            except BlockingIOError:
                break
            except OSError:
                self._sock = None
                break
            if not chunk:
                self._sock = None
                break
            self._buf += chunk.decode(errors="replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            obj = parse_reply(line)
            if obj is None or obj.get("request_id") != self._time_rid:
                continue
            self._time_rid = None
            if obj.get("error") == "success" and obj.get("data") is not None:
                self._time = float(obj["data"])
        if self._sock is None:
            self._time_rid = None
