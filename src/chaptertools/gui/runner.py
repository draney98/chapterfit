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
        self.log_hold = False
        self.log_keep = False
        self.log_ready: threading.Event | None = None


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

    def pause_for_log(self) -> None:
        """Stop the live process and hold the job until ``release_log_hold``."""
        with self._lock:
            job, proc = self._job, self._proc
            if job is None or job.log_hold:
                return
            job.log_hold = True
            job.log_keep = False
            job.log_ready = threading.Event()
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def release_log_hold(self, keep: bool) -> None:
        """Yes runs the same command again. No marks the held job cancelled."""
        with self._lock:
            job = self._job
        if job is None or job.log_ready is None:
            return
        job.log_keep = keep
        job.log_ready.set()

    def _execute(self, job: Job):
        line_buffered = 1
        with self._lock:
            self._proc = subprocess.Popen(
                job.argv,
                env=job.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=line_buffered,
            )
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:
            self.events.put(("line", job, line.rstrip("\n")))
        return proc.wait()

    def _work(self, job: Job):
        code = None
        try:
            while True:
                code = self._execute(job)
                if not job.log_hold:
                    break
                ready = job.log_ready
                if ready is None:
                    break
                ready.wait()
                keep = job.log_keep
                job.log_hold = False
                job.log_ready = None
                if not keep:
                    job.cancelled = True
                    break
        except OSError as e:
            self.events.put(("line", job, str(e)))
        finally:
            with self._lock:
                self._proc = None
                self._job = None
            # queued last, so a done handler may start the next job at once
            self.events.put(("done", job, code))
