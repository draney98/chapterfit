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
                 on_line=None, env: dict | None = None):
        self.name = name
        self.argv = list(argv)
        self.env = env  # None: inherit the GUI's environment
        self.on_done = on_done
        self.on_line = on_line
        self.cancelled = False


class Runner:
    """Serial subprocess queue with line streaming and cancellation."""

    def __init__(self):
        self.events: queue.Queue = queue.Queue()
        self._proc: subprocess.Popen | None = None
        self._job: Job | None = None
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        """True from run() until the job's done event is queued."""
        with self._lock:
            return self._job is not None

    def run(self, job: Job):
        with self._lock:
            if self._job is not None:
                self.events.put(("refused", job, "a job is already running"))
                return
            self._job = job
        threading.Thread(target=self._work, args=(job,), daemon=True).start()

    def cancel(self):
        with self._lock:
            proc, job = self._proc, self._job
        if job is not None:
            job.cancelled = True
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def _work(self, job: Job):
        code = None
        try:
            with self._lock:
                self._proc = subprocess.Popen(
                    job.argv,
                    env=job.env,
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
        except OSError as e:
            self.events.put(("line", job, str(e)))
        finally:
            with self._lock:
                self._proc = None
                self._job = None
            # queued last, so a done handler may start the next job at once
            self.events.put(("done", job, code))
