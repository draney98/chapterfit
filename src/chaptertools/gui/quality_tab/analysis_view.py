"""Analysis strip: metric tiles and the advice line. Levels live on the timeline."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from chaptertools.gui import tips

from .summary import format_number
from .widgets import MetricTile

_ADVICE = (
    "The background noise changes across this file. "
    "Re-estimating the noise per segment in Denoise will handle it better."
)


class AnalysisFrame(ttk.Frame):
    """The top strip. The legend is a row under the canvas, never drawn on it."""

    def __init__(self, master, on_rescan, on_turn_on, on_range):
        super().__init__(master, style="Quality.TFrame")
        self.columnconfigure(0, weight=1)
        self.selection = None
        self._report = {}

        header = ttk.Frame(self, style="Quality.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Analysis", style="Ground.TLabel").grid(
            row=0, column=0, sticky="w")
        self.source_var = tk.StringVar(value="No book selected")
        ttk.Label(header, textvariable=self.source_var, style="GroundMuted.TLabel").grid(
            row=0, column=1, sticky="w", padx=8)
        menu = tk.Menu(header, tearoff=0)
        menu.add_command(label="Re-scan", command=on_rescan)
        more = ttk.Menubutton(header, text="\u22ef", menu=menu, direction="below")
        more.grid(row=0, column=2, sticky="e")
        tips.tooltip(more, "measure the file again")

        tiles = ttk.Frame(self, style="Quality.TFrame")
        tiles.grid(row=1, column=0, sticky="w", pady=(4, 0))
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


def _metric(value, unit: str) -> str:
    if not isinstance(value, (int, float)):
        return "\u2014"
    return format_number(value, 1, unit)


