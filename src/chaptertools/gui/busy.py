"""What the running job is doing, and the window for jobs that must stay put."""

from __future__ import annotations

import contextlib
import tkinter as tk

from .theme import require_customtkinter

ctk = require_customtkinter()

_SENTENCES = {
    "measure": "Measuring the audio",
    "identify": "Matching an edition",
    "fit-probe": "Checking an edition",
    "find-asin": "Searching for an ASIN",
    "align": "Placing chapters",
    "sweep": "Sweeping for spoken chapters",
    "listen": "Listening near the gaps",
    "pin": "Saving a pin",
    "emit": "Writing chapters",
    "finalize": "Applying to the library",
    "scan": "Reading the file",
    "preview": "Preparing a 30 s preview",
    "process": "Rendering cleaned audio",
    "replace": "Replacing the library file",
    "learn": "Learning noise from the selection",
}


def opening_sentence(name: str, stage: str | None = None) -> str:
    """The first sentence for a job, before any progress line arrives."""
    if stage == "finalize-dry":
        return "Dry-running the library write"
    return _SENTENCES.get(name, "Working")


def job_is_modal(name: str, stage: str | None = None) -> bool:
    """True when the user can still edit a setting this job already captured.

    Align reads the Hints box. Apply, the cleanup render, and the library
    replace read embed, the chain, bitrate, and book info. Learn noise reads
    the timeline selection. Those open a window. The rest leave the window
    usable and only update the hover on "busy".
    """
    if name in {"align", "learn", "process", "replace", "emit"}:
        return True
    return name == "finalize" and stage == "finalize"


def progress_sentence(line: str) -> str | None:
    """A progress line worth showing. Command echoes and blank lines are not."""
    text = " ".join(line.split())
    if not text or text.startswith("$") or text.startswith("exit "):
        return None
    if text.startswith("!"):
        return None
    return text


class JobWindow:
    """A small window with the current sentence and Cancel.

    Updated from the app pump. It is not a messagebox, so the pump keeps
    running and the sentence can change.
    """

    def __init__(self, app):
        self.app = app
        self.win: ctk.CTkToplevel | None = None
        self.var = tk.StringVar(value="")
        self.bar: ctk.CTkProgressBar | None = None

    def sync(self) -> None:
        if not getattr(self.app, "modal_busy", True):
            self.close()
            return
        job = self.app.session.job
        if job.running and job.modal:
            self._open()
            self.var.set(job.detail or "Working")
            if self.bar is not None:
                self.bar.start()
            return
        self.close()

    def _open(self) -> None:
        if self.win is not None and self.win.winfo_exists():
            return
        win = ctk.CTkToplevel(self.app)
        win.title("Working")
        win.transient(self.app)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self.app._cancel)
        ctk.CTkLabel(win, textvariable=self.var, wraplength=420,
                     justify="left", text="").pack(padx=16, pady=(16, 8))
        self.bar = ctk.CTkProgressBar(win, mode="indeterminate", width=280)
        self.bar.pack(padx=16, pady=(0, 8))
        self.bar.start()
        ctk.CTkButton(win, text="Cancel", width=100, height=28,
                      command=self.app._cancel).pack(pady=(0, 16))
        win.update_idletasks()
        # Local grab only: this job already captured settings the user
        # must not edit mid-run. Never grab_set_global.
        win.grab_set()
        self.win = win

    def close(self) -> None:
        win = self.win
        self.win = None
        bar = self.bar
        self.bar = None
        if bar is not None:
            with contextlib.suppress(tk.TclError):
                bar.stop()
        if win is None:
            return
        try:
            win.grab_release()
            win.destroy()
        except tk.TclError:
            return
