"""Analysis strip: waveform, legend, metric tiles, and the advice line."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from chaptertools.gui import palette, tips

from .summary import format_number
from .widgets import MetricTile

_ADVICE = (
    "The background noise changes across this file. "
    "Re-estimating the noise per segment in Denoise will handle it better."
)


class AnalysisFrame(ttk.Frame):
    """The top strip. The legend is a row under the canvas, never drawn on it."""

    def __init__(self, master, on_scan, on_turn_on, on_range):
        super().__init__(master, style="Quality.TFrame")
        self.on_range = on_range
        self.columnconfigure(0, weight=1)
        self.selection = None
        self._report = {}
        self._anchor = None
        self._drag = None
        self._width = 0

        header = ttk.Frame(self, style="Quality.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Analysis", style="Ground.TLabel").grid(
            row=0, column=0, sticky="w")
        self.source_var = tk.StringVar(value="No book selected")
        ttk.Label(header, textvariable=self.source_var, style="GroundMuted.TLabel").grid(
            row=0, column=1, sticky="w", padx=8)
        self.scan_btn = ttk.Button(header, text="Scan file", command=on_scan)
        self.scan_btn.grid(row=0, column=2, sticky="e")
        tips.tooltip(self.scan_btn, "measure loudness, noise, and clipping")

        self.plot = tk.Canvas(self, height=84, highlightthickness=0,
                              background=palette.Q_PANEL)
        self.plot.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.plot.bind("<Configure>", self._configured)
        self.plot.bind("<ButtonPress-1>", self._press)
        self.plot.bind("<B1-Motion>", self._drag_to)
        self.plot.bind("<ButtonRelease-1>", self._release)

        tiles = ttk.Frame(self, style="Quality.TFrame")
        tiles.grid(row=1, column=1, rowspan=2, sticky="ne", padx=(8, 0))
        self.tiles = {}
        for index, (key, title) in enumerate((
            ("integrated_lufs", "Integrated"),
            ("true_peak_db", "True peak"),
            ("noise_floor_db", "Noise floor"),
            ("dynamic_range_db", "Dynamic range"),
        )):
            tile = MetricTile(tiles, title)
            tile.grid(row=index // 2, column=index % 2, sticky="nsew", padx=2, pady=2)
            self.tiles[key] = tile

        legend = ttk.Frame(self, style="Quality.TFrame")
        legend.grid(row=2, column=0, sticky="w", pady=(2, 0))
        _swatch(legend, palette.Q_TEXT, "Level").pack(side="left")
        _swatch(legend, palette.Q_CLIP, "Clipping").pack(side="left", padx=(8, 0))
        _swatch(legend, palette.Q_NOISY, "Noisier").pack(side="left", padx=(8, 0))

        self.advice = ttk.Frame(self, style="Warn.TFrame", padding=4)
        self.advice.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.advice.columnconfigure(0, weight=1)
        ttk.Label(self.advice, text=_ADVICE, style="Warn.TLabel",
                  wraplength=640, justify="left").grid(row=0, column=0, sticky="w")
        ttk.Button(self.advice, text="Turn it on", command=on_turn_on).grid(
            row=0, column=1, sticky="e", padx=(8, 0))
        self.advice.grid_remove()

    def set_source(self, text: str) -> None:
        self.source_var.set(text)

    def set_report(self, report: dict | None, *, target_lufs: float | None) -> None:
        self._report = report or {}
        self._fill_tiles(target_lufs)
        self.redraw()

    def set_target(self, target_lufs: float | None) -> None:
        self._fill_tiles(target_lufs)

    def show_advice(self, visible: bool) -> None:
        if visible:
            self.advice.grid()
        else:
            self.advice.grid_remove()

    def _fill_tiles(self, target_lufs) -> None:
        report = self._report
        integrated = report.get("integrated_lufs")
        target = ""
        if target_lufs is not None:
            target = "target " + format_number(target_lufs, 1, "LUFS")
        self.tiles["integrated_lufs"].set(_metric(integrated, "LUFS"), target)
        peak = report.get("true_peak_db")
        clipping = isinstance(peak, (int, float)) and peak > 0
        self.tiles["true_peak_db"].set(
            _metric(peak, "dBTP"),
            "over 0 \u2014 clips" if clipping else "",
            warn=clipping,
        )
        self.tiles["noise_floor_db"].set(_metric(report.get("noise_floor_db"), "dB"))
        self.tiles["dynamic_range_db"].set(
            _metric(report.get("dynamic_range_db"), "dB"))

    def _configured(self, event):
        if event.width != self._width:
            self._width = event.width
            self.redraw(event.width)

    def redraw(self, width: int | None = None) -> None:
        canvas = self.plot
        canvas.delete("all")
        width = width or max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), 1)
        chunks = self._report.get("chunks") or []
        if width < 2:
            return
        if not chunks:
            canvas.create_text(
                8, height / 2, anchor="w", fill=palette.Q_MUTED,
                text="Scan a file to see loudness, clipping, and noisy stretches.",
            )
            return
        values = [c.get("lufs") for c in chunks]
        finite = [v for v in values if isinstance(v, (int, float))]
        low = (min(finite) - 2) if finite else -40
        high = (max(finite) + 2) if finite else 0
        span = high - low or 1
        count = len(chunks)
        slot = width / count
        for index, chunk in enumerate(chunks):
            x0 = index * slot
            x1 = x0 + slot
            if chunk.get("noisy"):
                canvas.create_rectangle(x0, 0, x1, height, fill=palette.Q_NOISY, outline="")
            value = values[index]
            if isinstance(value, (int, float)):
                bar = (value - low) / span
                top = height - max(2, bar * (height - 4))
                canvas.create_rectangle(x0 + 1, top, max(x0 + 2, x1 - 1), height - 1,
                                        fill=palette.Q_TEXT, outline="")
            if chunk.get("clipping"):
                canvas.create_rectangle(x0, 0, x1, 4, fill=palette.Q_CLIP, outline="")
        self._draw_selection(width, height)

    def _draw_selection(self, width, height) -> None:
        if not self.selection and self._anchor is None:
            return
        if self.selection:
            start, end = self.selection
        else:
            start, end = self._anchor, self._drag if self._drag is not None else self._anchor
        x0 = self._x_at(min(start, end), width)
        x1 = self._x_at(max(start, end), width)
        if abs(x1 - x0) < 2:
            x1 = x0 + 2
        self.plot.create_rectangle(x0, 1, x1, height - 1, outline=palette.Q_ACCENT, width=2)

    def _press(self, event):
        self._anchor = self._time_at(event.x)
        self._drag = self._anchor

    def _drag_to(self, event):
        if self._anchor is None:
            return
        self._drag = self._time_at(event.x)
        self.redraw()

    def _release(self, event):
        if self._anchor is None:
            return
        end = self._time_at(event.x)
        start, stop = sorted((self._anchor, end))
        self._anchor = None
        self._drag = None
        if stop - start < 0.05:
            self.selection = None
            self.on_range(start, None)
        else:
            self.selection = (start, stop)
            self.on_range(start, stop)
        self.redraw()

    def _time_at(self, x: int) -> float:
        duration = float(self._report.get("duration") or 0)
        width = max(self.plot.winfo_width(), 1)
        if duration <= 0:
            return 0.0
        return max(0.0, min(duration, x / width * duration))

    def _x_at(self, seconds: float, width: int) -> float:
        duration = float(self._report.get("duration") or 0)
        if duration <= 0:
            return 0.0
        return max(0.0, min(width, seconds / duration * width))


def _metric(value, unit: str) -> str:
    if not isinstance(value, (int, float)):
        return "\u2014"
    return format_number(value, 1, unit)


def _swatch(master, color: str, text: str) -> ttk.Frame:
    row = ttk.Frame(master, style="Quality.TFrame")
    mark = tk.Canvas(row, width=12, height=12, highlightthickness=0,
                     background=palette.Q_GROUND)
    mark.pack(side="left")
    mark.create_rectangle(1, 1, 11, 11, fill=color, outline="")
    ttk.Label(row, text=text, style="GroundMuted.TLabel").pack(side="left", padx=(4, 0))
    return row


