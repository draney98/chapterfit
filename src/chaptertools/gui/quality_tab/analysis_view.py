"""Analysis strip: metric tiles and the advice line. Levels live on the timeline."""

from __future__ import annotations

import tkinter as tk

from chaptertools.gui import palette, tips
from chaptertools.gui.theme import require_customtkinter

from .summary import format_number
from .widgets import MetricTile

ctk = require_customtkinter()

_ADVICE = (
    "The background noise changes across this file. "
    "Re-estimating the noise per segment in Denoise will handle it better."
)


class AnalysisFrame(ctk.CTkFrame):
    """The top strip. The legend is a row under the canvas, never drawn on it."""

    def __init__(self, master, on_rescan, on_turn_on, on_range):
        super().__init__(master, fg_color=palette.Q_GROUND, corner_radius=0)
        self.columnconfigure(0, weight=1)
        self.selection = None
        self._report = {}

        header = ctk.CTkFrame(self, fg_color=palette.Q_GROUND, corner_radius=0)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        from chaptertools.gui.ui_kit import section, small
        section(header, "Analysis").grid(row=0, column=0, sticky="w")
        self.source_var = tk.StringVar(value="No book selected")
        source = small(header, "")
        source.configure(textvariable=self.source_var)
        source.grid(row=1, column=0, columnspan=3, sticky="w")
        from chaptertools.gui.ui_kit import primary, text_button
        rescan = text_button(header, "Re-scan", command=on_rescan, width=90)
        rescan.grid(row=0, column=2, sticky="e")
        tips.tooltip(rescan, "measure the file again")

        tiles = ctk.CTkFrame(self, fg_color=palette.Q_GROUND, corner_radius=0)
        tiles.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self._tiles = tiles
        for col in range(4):
            tiles.columnconfigure(col, weight=1, uniform="metric")
        self.tiles = {}
        for index, (key, title) in enumerate((
            ("integrated_lufs", "Integrated"),
            ("true_peak_db", "True peak"),
            ("noise_floor_db", "Noise floor"),
            ("dynamic_range_db", "Dynamic range"),
        )):
            tile = MetricTile(tiles, title)
            tile.grid(row=0, column=index, sticky="nsew", padx=2, pady=2)
            self.tiles[key] = tile
        pending = ctk.CTkFrame(self, fg_color=palette.Q_GROUND, corner_radius=0)
        self._pending = pending
        from chaptertools.gui.ui_kit import secondary, small
        small(pending, "Not analysed yet").pack(side="left")
        secondary(pending, "Analyse", command=on_rescan).pack(
            side="left", padx=(8, 0))
        pending.grid(row=1, column=0, sticky="w", pady=(4, 0))
        tiles.grid_remove()

        self.advice = ctk.CTkFrame(
            self, fg_color=palette.Q_WARN_BG, corner_radius=8)
        self.advice.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self.advice.columnconfigure(0, weight=1)
        advice = ctk.CTkLabel(self.advice, text=_ADVICE, text_color=palette.Q_WARN_TEXT,
                     justify="left", anchor="w")
        advice.grid(
            row=0, column=0, sticky="w", padx=4, pady=4)
        from chaptertools.gui.ui_kit import wrap_to_parent
        wrap_to_parent(advice)
        primary(self.advice, "Use per-segment noise estimate",
                command=on_turn_on, width=280).grid(
            row=0, column=1, sticky="e", padx=(8, 4))
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
        analysed = any(
            isinstance(report.get(key), (int, float)) for key in self.tiles)
        if analysed:
            self._tiles.grid()
            self._pending.grid_remove()
        else:
            self._tiles.grid_remove()
            self._pending.grid()
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
