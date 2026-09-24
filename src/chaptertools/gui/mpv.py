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
        self._replies: dict[int, dict] = {}
        self._next_id = 0
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

    def _connect(self):
        if self._sock is not None or self.sock_path is None:
            return
        for _ in range(40):
            if not self.alive:
                return
            if os.path.exists(self.sock_path):
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(0.2)
                try:
                    s.connect(self.sock_path)
                except OSError:
                    # the socket file can appear before mpv accepts on it
                    s.close()
                    time.sleep(0.05)
                    continue
                self._sock = s
                self._buf = ""
                self._replies = {}
                return
            time.sleep(0.05)

    def poll(self) -> float | None:
        """Current playback time in seconds, or None when unavailable."""
        if not self.alive:
            self.stop()
            return None
        if self._sock is None:
            self._connect()
            if self._sock is None:
                return None
        reply = self._rpc("get_property", "playback-time")
        if reply and reply.get("error") == "success" \
                and reply.get("data") is not None:
            return float(reply["data"])
        return None

    def toggle_pause(self):
        self._paused = not self._paused
        self._rpc("set_property", "pause", self._paused)

    def seek(self, delta: float):
        """Move relative to the current position; works while paused."""
        self._rpc("seek", delta, "exact")

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
        self._buf, self._replies = "", {}
        self._paused = False
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    # ------------------------------------------------------------- ipc --
    def _rpc(self, *command) -> dict | None:
        if self._sock is None:
            return None
        self._next_id += 1
        rid = self._next_id
        msg = json.dumps({"command": list(command), "request_id": rid}) + "\n"
        try:
            self._sock.sendall(msg.encode())
        except OSError:
            self._sock = None
            return None
        return self._read_reply(rid)

    def _read_reply(self, rid: int, timeout: float = 0.5) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if rid in self._replies:
                return self._replies.pop(rid)
            try:
                chunk = self._sock.recv(65536)
            except TimeoutError:
                continue
            except OSError:
                self._sock = None
                return None
            if not chunk:
                self._sock = None
                return None
            self._buf += chunk.decode(errors="replace")
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                obj = parse_reply(line)
                if obj is not None:
                    self._replies[int(obj["request_id"])] = obj
        return self._replies.pop(rid, None)
