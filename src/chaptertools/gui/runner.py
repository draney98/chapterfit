"""Run pipeline CLIs off the UI thread and stream their output.

Jobs run one at a time. All events land on a plain queue that the Tk main
loop drains with `after()`, so no widget is ever touched from a worker
thread.
"""
from __future__ import annotations

import queue
import subprocess
import threading


class Job:
    """One subprocess run. `on_done(job, code)` and `on_line(line)` fire
    in the UI thread (via the app's pump)."""

    def __init__(self, name: str, argv: list[str], on_done=None,
                 on_line=None):
        self.name = name
        self.argv = list(argv)
        self.on_done = on_done
        self.on_line = on_line


class Runner:
    """Serial subprocess queue with line streaming and cancellation."""

    def __init__(self):
        self.events: queue.Queue = queue.Queue()
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def run(self, job: Job):
        if self.busy:
            self.events.put(("refused", job, "a job is already running"))
            return
        threading.Thread(target=self._work, args=(job,), daemon=True).start()

    def cancel(self):
        with self._lock:
            proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def _work(self, job: Job):
        try:
            with self._lock:
                self._proc = subprocess.Popen(
                    job.argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            proc = self._proc
            assert proc is not None and proc.stdout is not None
            for line in proc.stdout:
                self.events.put(("line", job, line.rstrip("\n")))
            code = proc.wait()
            self.events.put(("done", job, code))
        except OSError as e:
            self.events.put(("line", job, str(e)))
            self.events.put(("done", job, None))
        finally:
            with self._lock:
                self._proc = None
