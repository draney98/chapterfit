"""The one playback bar. Resolve and Quality use this player."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from chaptertools.util import clock

from . import tips


class PlayerBar(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=(8, 2))
        self.app = app
        self.columnconfigure(7, weight=1)
        self._build()
        session = app.session
        session.subscribe("playhead", self._show_time)
        session.subscribe("cleanup", self._show_hear)
        self._bind_keys()

    def _build(self) -> None:
        for delta, text in ((-10, "−10"), (-5, "−5")):
            b = ttk.Button(self, text=text, width=4,
                           command=lambda d=delta: self._seek(d))
            b.grid(row=0, column=0 if delta == -10 else 1, padx=1)
        self.play_btn = ttk.Button(self, text="▶", width=3, command=self.toggle)
        self.play_btn.grid(row=0, column=2, padx=2)
        tips.tooltip(self.play_btn, "play or pause (Space)")
        for delta, text, col in ((5, "+5", 3), (10, "+10", 4)):
            ttk.Button(self, text=text, width=4,
                       command=lambda d=delta: self._seek(d)).grid(
                row=0, column=col, padx=1)
        self.time_var = tk.StringVar(value="0:00:00 / —")
        ttk.Label(self, textvariable=self.time_var).grid(
            row=0, column=5, padx=(8, 4))
        self.chapter_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.chapter_var).grid(row=0, column=6)
        self.hear_var = tk.BooleanVar(value=False)
        self.hear = ttk.Checkbutton(
            self, text="Hear original", variable=self.hear_var,
            command=self._hear, state="disabled")
        self.hear.grid(row=0, column=8, padx=(8, 4))
        tips.tooltip(self.hear, "play the file without the cleanup chain")
        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, anchor="e").grid(
            row=0, column=9, sticky="ew", padx=4)
        ttk.Label(self, textvariable=self.app.busy_var).grid(
            row=0, column=12, padx=(6, 0))
        self.log_btn = ttk.Button(self, text="Log", command=self.app.toggle_log)
        self.log_btn.grid(row=0, column=10, padx=2)
        tips.tooltip(self.log_btn, "open the log (Ctrl+Shift+L)")
        cancel = ttk.Button(self, text="Cancel job", command=self.app._cancel)
        cancel.grid(row=0, column=11, padx=(2, 0))
        tips.tooltip(cancel, "stop the running job and any playback")

    def _bind_keys(self) -> None:
        root = self.winfo_toplevel()
        root.bind_all("<space>", self._space, add="+")
        root.bind_all("<Left>", lambda e: self._arrow(e, -5))
        root.bind_all("<Right>", lambda e: self._arrow(e, 5))
        root.bind_all("<Shift-Left>", lambda e: self._arrow(e, -10))
        root.bind_all("<Shift-Right>", lambda e: self._arrow(e, 10))

    def _typing(self, event) -> bool:
        w = event.widget
        if not isinstance(w, tk.Misc):
            return False
        return w.winfo_class() in ("Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox")

    def _space(self, event):
        if self._typing(event):
            return None
        self.toggle()
        return "break"

    def _arrow(self, event, delta):
        if self._typing(event):
            return None
        self._seek(delta)
        return "break"

    def toggle(self) -> None:
        player = self.app.player
        if player.alive:
            player.toggle_pause()
        elif self.app.workflow.audio:
            start = self.app.session.playhead or None
            self.app.play(self.app.workflow.audio, start=start or None, length=0)
        self._sync_button()

    def _seek(self, delta: float) -> None:
        if self.app.player.alive:
            self.app.player.seek(delta)

    def _hear(self) -> None:
        quality = getattr(self.app.workflow, "quality", None)
        if quality is not None and hasattr(quality, "hear_var"):
            quality.hear_var.set(self.hear_var.get())
            if hasattr(quality, "_toggle_hear"):
                quality._toggle_hear()

    def show_status(self, line: str) -> None:
        self.status_var.set(line)

    def set_log_errors(self, count: int) -> None:
        self.log_btn.configure(text=f"Log {count}" if count else "Log")

    def update_time(self, seconds: float | None) -> None:
        session = self.app.session
        if seconds is not None:
            session.playhead = seconds
        total = session.duration
        shown = clock(session.playhead) if session.playhead else "0:00:00"
        end = clock(total) if total else "—"
        ch = ""
        if session.chapter_index is not None:
            ch = f"  · ch {session.chapter_index + 1}"
        self.time_var.set(f"{shown} / {end}{ch}")
        self._sync_button()

    def _show_time(self) -> None:
        self.update_time(None)

    def _show_hear(self) -> None:
        on = int(self.app.session.cleanup.get("stages_on") or 0) > 0
        self.hear.state(["!disabled"] if on else ["disabled"])

    def _sync_button(self) -> None:
        playing = self.app.player.alive and not self.app.player.paused
        self.play_btn.configure(text="❚❚" if playing else "▶")
