"""Status line for a cleanup job. Playback lives on the window player."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class FooterBar(ttk.Frame):
    def __init__(self, master, hear_var, on_play, on_stop, on_hear,
                 on_process, on_replace, on_cancel):
        super().__init__(master, style="Quality.TFrame", padding=4)
        del hear_var, on_play, on_stop, on_hear, on_process, on_replace, on_cancel
        self.columnconfigure(0, weight=1)
        self.format_var = tk.StringVar(value="MP3")
        self.bitrate_var = tk.StringVar(value="64k")
        self._running = False
        self._replace_ready = False
        self.status_var = tk.StringVar(value="")

    def set_running(self, running: bool) -> None:
        self._running = running

    def set_progress(self, fraction: float) -> None:
        del fraction

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def set_replace(self, enabled: bool) -> None:
        """The library write lives on Finish. This only records readiness."""
        self._replace_ready = bool(enabled) and not self._running
