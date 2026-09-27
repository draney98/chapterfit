"""Preview and output bar. Cancel takes the place of Process while a job runs."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from chaptertools.gui import tips

_BITRATES = ("48k", "64k", "96k", "128k", "160k", "192k", "256k", "320k")


class FooterBar(ttk.Frame):
    def __init__(self, master, hear_var, on_play, on_stop, on_hear,
                 on_process, on_replace, on_cancel):
        super().__init__(master, style="Quality.TFrame", padding=4)
        self.on_process = on_process
        self.on_cancel = on_cancel
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
        ttk.Label(output, text="Output", style="GroundMuted.TLabel").pack(side="left")
        self.format_var = tk.StringVar(value="MP3")
        ttk.Combobox(output, textvariable=self.format_var, values=("MP3",),
                     width=6, state="readonly").pack(side="left", padx=(8, 0))
        self.bitrate_var = tk.StringVar(value="64k")
        ttk.Combobox(output, textvariable=self.bitrate_var, values=_BITRATES,
                     width=6, state="readonly").pack(side="left", padx=4)
        self.progress = ttk.Progressbar(output, maximum=1.0, length=120)
        self.progress.pack(side="left", padx=4)
        self.replace_btn = ttk.Button(output, text="Replace library file",
                                      command=on_replace, state="disabled")
        self.replace_btn.pack(side="right")
        tips.tooltip(self.replace_btn,
                     "copy the cleaned MP3 over the library file, "
                     "keeping the original in the work folder")
        self.process_btn = ttk.Button(output, text="Process full file",
                                      style="Quality.Accent.TButton", command=self._process)
        self.process_btn.pack(side="right", padx=(0, 4))
        tips.tooltip(self.process_btn,
                     "write a cleaned MP3 in the work folder; "
                     "the library file stays as it is")
        self._running = False

        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, style="GroundMuted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(2, 0))

    def set_running(self, running: bool) -> None:
        self._running = running
        state = "disabled" if running else "normal"
        self.play_btn.configure(state=state)
        if running:
            self.process_btn.configure(text="Cancel", style="TButton",
                                       command=self.on_cancel)
        else:
            self.process_btn.configure(text="Process full file",
                                       style="Quality.Accent.TButton",
                                       command=self._process)

    def set_progress(self, fraction: float) -> None:
        self.progress["value"] = max(0.0, min(1.0, float(fraction or 0)))

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def set_replace(self, enabled: bool) -> None:
        self.replace_btn.configure(state="normal" if enabled and not self._running else "disabled")

    def _process(self) -> None:
        self.on_process()
