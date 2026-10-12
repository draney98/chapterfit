"""Status line for a cleanup job. Playback lives on the window player."""

from __future__ import annotations

import tkinter as tk

from chaptertools.gui import palette
from chaptertools.gui.theme import require_customtkinter

ctk = require_customtkinter()


class FooterBar(ctk.CTkFrame):
    def __init__(self, master, hear_var, on_play, on_stop, on_hear,
                 on_process, on_replace, on_cancel):
        super().__init__(master, fg_color=palette.Q_GROUND, corner_radius=0)
        del hear_var, on_play, on_stop, on_hear, on_process, on_replace, on_cancel
        self.columnconfigure(0, weight=1)
        self.format_var = tk.StringVar(value="MP3")
        self.bitrate_var = tk.StringVar(value="64k")
        self._running = False
        self._replace_ready = False
        self.status_var = tk.StringVar(value="")
        self.bar = ctk.CTkProgressBar(self, width=200)
        self.bar.set(0)
        self.bar.grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ctk.CTkLabel(self, textvariable=self.status_var, anchor="w", text="").grid(
            row=0, column=1, sticky="w", padx=8)

    def set_running(self, running: bool) -> None:
        self._running = running

    def set_progress(self, fraction: float) -> None:
        try:
            self.bar.set(max(0.0, min(1.0, float(fraction or 0))))
        except (TypeError, ValueError):
            self.bar.set(0)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def set_replace(self, enabled: bool) -> None:
        """The library write lives on Finish. This only records readiness."""
        self._replace_ready = bool(enabled) and not self._running
