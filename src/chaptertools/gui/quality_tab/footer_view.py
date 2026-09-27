"""Preview and output bar. Cancel takes the place of Process while a job runs."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from chaptertools.gui import tips


class FooterBar(ttk.Frame):
    def __init__(self, master, hear_var, on_play, on_stop, on_hear,
                 on_process, on_replace, on_cancel):
        super().__init__(master, style="Quality.TFrame", padding=4)
        del on_process, on_replace, on_cancel
        self.columnconfigure(0, weight=1)

        preview = ttk.Frame(self, style="Quality.TFrame")
        preview.grid(row=0, column=0, sticky="ew")
        ttk.Label(preview, text="Preview", style="GroundMuted.TLabel").pack(side="left")
        # Playback lives on the window player bar.
        self.play_btn = ttk.Button(preview, text="Play", command=on_play)
        self.stop_btn = ttk.Button(preview, text="Stop", command=on_stop)
        ttk.Label(preview, text="from", style="Ground.TLabel").pack(side="left")
        self.start_var = tk.StringVar(value="0:00:00")
        ttk.Entry(preview, textvariable=self.start_var, width=12).pack(side="left", padx=4)
        ttk.Label(preview, text="for 30 s", style="Ground.TLabel").pack(side="left")
        hear = ttk.Checkbutton(
            preview, text="Hear original", variable=hear_var, style="Hear.Toolbutton",
            command=on_hear,
        )
        tips.tooltip(hear, "play the unprocessed 30 seconds")

        output = ttk.Frame(self, style="Quality.TFrame")
        output.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.format_var = tk.StringVar(value="MP3")
        self.bitrate_var = tk.StringVar(value="64k")
        self.progress = ttk.Progressbar(output, maximum=1.0, length=120)
        self.progress.pack(side="left", padx=4)
        self._running = False

        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, style="GroundMuted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(2, 0))

    def set_running(self, running: bool) -> None:
        self._running = running
        state = "disabled" if running else "normal"
        self.play_btn.configure(state=state)

    def set_progress(self, fraction: float) -> None:
        self.progress["value"] = max(0.0, min(1.0, float(fraction or 0)))

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def set_replace(self, enabled: bool) -> None:
        """The library write lives on Finish. This only records readiness."""
        self._replace_ready = bool(enabled) and not self._running
