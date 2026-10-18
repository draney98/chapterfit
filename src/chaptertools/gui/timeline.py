"""One timeline for the open book: levels, silences, chapters, selection."""

from __future__ import annotations

import array
import json
import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from chaptertools.util import hms

from . import tips
from .table import gap_marks
from .theme import ctk_frame, require_customtkinter
from .ui_kit import FONT, PAD, SMALL, C, panel, secondary, section, small

ctk = require_customtkinter()

_HEIGHT = 52
_TICK = 6
_LABEL_GAP = 28
PLOT_INSET = 14


def plot_box(width: int, height: int, inset: int | None = None
             ) -> tuple[int, int, int, int]:
    """Inner plot rect: 14 px inside the canvas border, on every side."""
    if inset is None:
        inset = PLOT_INSET
    if width <= inset * 2 + 1 or height <= inset * 2 + 1:
        return 0, 0, max(1, width), max(1, height)
    return inset, inset, width - 2 * inset, height - 2 * inset


def time_to_x(seconds: float, start: float, end: float, width: int,
              height: int = _HEIGHT) -> float:
    """Map a time onto the inner plot, not the card border."""
    x0, _y0, plot_w, _plot_h = plot_box(width, height)
    span = end - start
    if span <= 0:
        return float(x0)
    return x0 + (seconds - start) / span * plot_w


def label_indexes(xs: list[float], min_gap: float = _LABEL_GAP) -> list[int]:
    """Every Nth label, N the smallest stride that avoids overlap.

    The first and last chapter are always labelled. Every mark is still drawn.
    """
    count = len(xs)
    if count == 0:
        return []
    if count == 1:
        return [0]

    def chosen(stride: int) -> list[int]:
        indexes = list(range(0, count, stride))
        if indexes[-1] != count - 1:
            indexes.append(count - 1)
        return indexes

    def fits(indexes: list[int]) -> bool:
        last = xs[indexes[0]]
        for index in indexes[1:]:
            if xs[index] - last < min_gap:
                return False
            last = xs[index]
        return True

    for stride in range(1, count):
        indexes = chosen(stride)
        if fits(indexes):
            return indexes
    return [0, count - 1]


def silence_pixel_xs(gaps: list[dict], start: float, end: float,
                     width: int) -> list[int]:
    """Pixel columns for silence mids in view, one tick per column."""
    span = end - start
    if span <= 0 or width < 1:
        return []
    seen: set[int] = set()
    out: list[int] = []
    for gap in gaps:
        mid = gap.get("mid")
        if mid is None or not (start <= mid <= end):
            continue
        x = int(round((mid - start) / span * width))
        x = max(0, min(width - 1, x))
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


class Timeline(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.app = app
        self.mode = tk.StringVar(value="cadence")
        self.view: tuple[float, float] | None = None
        self._drag: tuple[float, float] | None = None
        self._press_x: int | None = None
        self._levels_key = None
        self._levels: list[int] = []
        self._tip = None
        self.columnconfigure(0, weight=1)

        bar = ctk_frame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        section(bar, "Timeline").grid(row=0, column=0, sticky="w")
        legend = ctk_frame(bar, fg_color="transparent")
        legend.grid(row=0, column=1, sticky="w", padx=(PAD, 0))
        self._swatch(legend, C["mark"], "Chapter")
        self._swatch(legend, C["bad_fg"], "Problem")
        self._swatch(legend, C["silence"], "Silence")
        self._menu = tk.Menu(bar, tearoff=0)
        self._menu.add_radiobutton(
            label="Similar by chapter cadence", variable=self.mode,
            value="cadence", command=self.redraw)
        self._menu.add_radiobutton(
            label="Similar by silence length", variable=self.mode,
            value="duration", command=self.redraw)
        self._opts = secondary(
            bar, "Options ▾", command=self._open_options, width=110)
        self._opts.grid(row=0, column=2, sticky="e", padx=(PAD, PLOT_INSET))
        tips.tooltip(self._opts, "which candidate silences to emphasise")
        self.legend = tk.StringVar(value="")  # kept for callers; unused in chrome

        plot = panel(self)
        plot.grid(row=1, column=0, sticky="ew", pady=(PAD // 2, 0))
        plot.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            plot, height=_HEIGHT, highlightthickness=0, background=C["panel"],
            borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky="ew")
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag_to)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-4>", lambda e: self._wheel(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self._wheel(e, -1))
        self.canvas.bind("<MouseWheel>", self._mousewheel)
        self.canvas.bind("<Leave>", lambda _e: self._show_tip(None))

    def _swatch(self, parent, color: str, label: str) -> None:
        cell = ctk_frame(parent, fg_color="transparent")
        cell.pack(side="left", padx=(0, PAD))
        chip = ctk.CTkFrame(
            cell, width=10, height=10, corner_radius=2, fg_color=color)
        chip.pack(side="left", padx=(0, 4))
        chip.pack_propagate(False)
        small(cell, text=label).pack(side="left")

    def _open_options(self) -> None:
        try:
            x = self._opts.winfo_rootx()
            y = self._opts.winfo_rooty() + self._opts.winfo_height()
            self._menu.tk_popup(x, y)
        finally:
            self._menu.grab_release()

    def redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), _HEIGHT)
        if width < 8:
            return
        data = self._data()
        duration = data["duration"]
        x0, y0, _plot_w, plot_h = plot_box(width, height)
        if duration <= 0:
            canvas.create_text(
                x0, y0 + plot_h / 2, anchor="w", fill=C["muted"],
                text="Open a book to see levels, silences, and chapters.")
            return
        start, end = self._span(duration)
        self._draw_noise(canvas, data["chunks"], start, end, width, height)
        self._draw_levels(canvas, data, start, end, width, height)
        self._draw_silences(canvas, data, start, end, width, height)
        self._draw_chapters(canvas, data, start, end, width, height)
        self._draw_selection(canvas, start, end, width, height)
        self._draw_playhead(canvas, start, end, width, height)

    def _data(self) -> dict:
        wf = getattr(self.app, "workflow", None)
        if wf is None:
            return {"duration": 0, "rows": [], "gaps": [], "chunks": [],
                    "audio": None, "work": None}
        analysis = getattr(wf, "analysis", None) or {}
        env = analysis.get("envelope") or {}
        duration = float(env.get("duration") or self.app.session.duration or 0)
        rows = getattr(getattr(wf, "_projection", None), "rows", None) or []
        gaps = (analysis.get("gaps") or {}).get("gaps") or []
        report = getattr(getattr(wf, "quality", None), "report", None) or {}
        if not report and getattr(wf, "audio", None) and getattr(wf, "work_dir", None):
            report = _load_report(wf.work_dir, wf.audio)
        return {
            "duration": duration,
            "rows": rows,
            "gaps": gaps,
            "chunks": (report or {}).get("chunks") or [],
            "audio": getattr(wf, "audio", None),
            "work": getattr(wf, "work_dir", None),
        }

    def _span(self, duration: float) -> tuple[float, float]:
        if self.view is None:
            return 0.0, duration
        start, end = self.view
        start = max(0.0, min(start, duration))
        end = max(start + 1.0, min(end, duration))
        return start, end

    def _x(self, seconds: float, start: float, end: float, width: int) -> float:
        height = max(self.canvas.winfo_height(), _HEIGHT)
        return time_to_x(seconds, start, end, width, height)

    def _time_at(self, x: int) -> float:
        duration = self._data()["duration"]
        if duration <= 0:
            return 0.0
        start, end = self._span(duration)
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), _HEIGHT)
        x0, _y0, plot_w, _plot_h = plot_box(width, height)
        rel = max(0.0, min(1.0, (x - x0) / plot_w))
        t = start + rel * (end - start)
        return max(0.0, min(duration, t))

    def _draw_noise(self, canvas, chunks, start, end, width, height) -> None:
        _x0, y0, _plot_w, plot_h = plot_box(width, height)
        for chunk in chunks:
            if not chunk.get("noisy"):
                continue
            t0 = float(chunk.get("start") or 0)
            t1 = t0 + float(chunk.get("seconds") or chunk.get("duration") or 0)
            if t1 < start or t0 > end:
                continue
            x0 = self._x(max(t0, start), start, end, width)
            x1 = self._x(min(t1, end), start, end, width)
            canvas.create_rectangle(
                x0, y0 + _TICK, max(x0 + 1, x1), y0 + plot_h,
                fill=C["warn_bg"], width=0)

    def _draw_levels(self, canvas, data, start, end, width, height) -> None:
        x0, y0, plot_w, plot_h = plot_box(width, height)
        levels = self._level_bars(data, plot_w)
        lane_top = y0 + _TICK + 2
        bottom = y0 + plot_h - 2
        if not levels:
            self._draw_chunk_levels(canvas, data["chunks"], start, end, width,
                                    height, lane_top)
            return
        slot = plot_w / len(levels)
        span = max(1, plot_h - _TICK - 10)
        for index, db in enumerate(levels):
            if db is None:
                continue
            bar = max(0.0, min(1.0, (db + 96) / 96))
            top = bottom - 2 - bar * span
            left = x0 + index * slot
            canvas.create_rectangle(
                left, max(lane_top, top), left + max(1, slot - 1), bottom,
                fill=C["border"], width=0)
        self._draw_clips(canvas, data["chunks"], start, end, width, height)

    def _level_bars(self, data, width: int) -> list[int] | None:
        audio, work = data["audio"], data["work"]
        if not audio or not work:
            return None
        from chaptertools.workdir import envelope_path
        path = envelope_path(audio, work)
        if not os.path.isfile(path):
            return None
        columns = max(1, width // 3)
        key = (path, os.path.getmtime(path), columns)
        if key != self._levels_key:
            self._levels_key = key
            self._levels = _bin_envelope(path, columns)
        return self._levels

    def _draw_chunk_levels(self, canvas, chunks, start, end, width, height,
                           lane_top: int) -> None:
        values = [c.get("lufs") for c in chunks if isinstance(c.get("lufs"), (int, float))]
        if not values:
            return
        low, high = min(values) - 2, max(values) + 2
        span = high - low or 1
        _px, y0, _pw, plot_h = plot_box(width, height)
        bottom = y0 + plot_h - 2
        scale = max(1, plot_h - _TICK - 10)
        for chunk in chunks:
            value = chunk.get("lufs")
            if not isinstance(value, (int, float)):
                continue
            t0 = float(chunk.get("start") or 0)
            t1 = t0 + float(chunk.get("seconds") or 0)
            if t1 < start or t0 > end:
                continue
            x0 = self._x(max(t0, start), start, end, width)
            x1 = self._x(min(t1, end), start, end, width)
            top = bottom - 2 - ((value - low) / span) * scale
            canvas.create_rectangle(
                x0, max(lane_top, top), max(x0 + 1, x1), bottom,
                fill=C["border"], width=0)
        self._draw_clips(canvas, chunks, start, end, width, height)

    def _draw_clips(self, canvas, chunks, start, end, width, height) -> None:
        _x0, y0, _plot_w, _plot_h = plot_box(width, height)
        for chunk in chunks:
            if not chunk.get("clipping"):
                continue
            t0 = float(chunk.get("start") or 0)
            t1 = t0 + float(chunk.get("seconds") or 0)
            if t1 < start or t0 > end:
                continue
            x0 = self._x(max(t0, start), start, end, width)
            x1 = self._x(min(t1, end), start, end, width)
            canvas.create_rectangle(x0, y0, max(x0 + 1, x1), y0 + 3,
                                    fill=C["bad_fg"], width=0)

    def _draw_silences(self, canvas, data, start, end, width, height) -> None:
        x0, y0, plot_w, plot_h = plot_box(width, height)
        # Silences sit in the bottom third so chapter marks stay readable.
        band = max(_TICK, plot_h // 3)
        bottom = y0 + plot_h
        for x in silence_pixel_xs(data["gaps"], start, end, plot_w):
            canvas.create_line(
                x0 + x, bottom - band, x0 + x, bottom,
                fill=C["silence"], width=1)
        # Candidate extras (Options mode) get a slightly taller tick + tip.
        _chapters, extras = gap_marks(data["gaps"], data["rows"], self.mode.get())
        for item in extras:
            gap = item.get("gap") or {}
            mid = gap.get("mid")
            if mid is None or not (start <= mid <= end):
                continue
            x = self._x(mid, start, end, width)
            tag = f"gap{round(mid, 3)}"
            canvas.create_line(
                x, bottom - band - 4, x, bottom, fill=C["silence"], width=1,
                tags=(tag,))
            tip = f"{hms(mid)} · {gap.get('dur', 0):.2f} s silence"
            canvas.tag_bind(tag, "<Enter>",
                            lambda _e, text=tip: self._show_tip(text))
            canvas.tag_bind(tag, "<Leave>", lambda _e: self._show_tip(None))

    def _draw_chapters(self, canvas, data, start, end, width, height) -> None:
        font = tkfont.Font(family=FONT, size=SMALL)
        x0, y0, plot_w, plot_h = plot_box(width, height)
        marks: list[tuple[float, object, bool, str]] = []
        for row in data["rows"]:
            t = row.placed if row.placed is not None else row.published
            if t is None or not (start <= t <= end):
                continue
            x = self._x(t, start, end, width)
            problem = any(tag in ("conflict", "unplaced", "lowconf")
                          for tag in row.tags)
            tip = f"{row.index + 1}. {row.title or 'chapter'} · {hms(t)}"
            marks.append((x, row, problem, tip))
        shown = set(label_indexes([m[0] for m in marks], _LABEL_GAP))
        for index, (x, row, problem, tip) in enumerate(marks):
            color = C["bad_fg"] if problem else C["mark"]
            tag = f"ch{row.index}"
            canvas.create_line(
                x, y0, x, y0 + plot_h, fill=color, width=1, tags=(tag,))
            if index in shown:
                label = str(row.index + 1)
                if x + 2 + font.measure(label) > x0 + plot_w:
                    tx, anchor = x - 2, "ne"
                else:
                    tx, anchor = x + 2, "nw"
                canvas.create_text(
                    tx, y0 + _TICK + 1, anchor=anchor, text=label,
                    fill=color, font=font, tags=(tag,))
            canvas.tag_bind(tag, "<Enter>",
                            lambda _e, text=tip: self._show_tip(text))
            canvas.tag_bind(tag, "<Leave>", lambda _e: self._show_tip(None))
        show_preview = getattr(self.app.workflow, "_step", "") == "match"
        if not show_preview:
            return
        for t in self.app.session.preview_marks:
            if not (start <= t <= end):
                continue
            x = self._x(t, start, end, width)
            canvas.create_line(
                x, y0, x, y0 + plot_h, fill=C["mark"], dash=(3, 3))

    def _draw_selection(self, canvas, start, end, width, height) -> None:
        span = self._drag or self.app.session.selection
        if not span:
            return
        _x0, y0, _plot_w, plot_h = plot_box(width, height)
        a, b = span
        x0 = self._x(min(a, b), start, end, width)
        x1 = self._x(max(a, b), start, end, width)
        canvas.create_rectangle(
            x0, y0, max(x0 + 2, x1), y0 + plot_h,
            fill=C["accent_soft"], outline="", width=0)

    def _draw_playhead(self, canvas, start, end, width, height) -> None:
        t = self.app.session.playhead
        if t < start or t > end:
            return
        _x0, y0, _plot_w, plot_h = plot_box(width, height)
        x = self._x(t, start, end, width)
        canvas.create_line(x, y0, x, y0 + plot_h, fill=C["text"], width=1,
                           tags=("playhead",))

    def _press(self, event) -> None:
        self._press_x = event.x
        t = self._time_at(event.x)
        self._drag = (t, t)

    def _drag_to(self, event) -> None:
        if self._drag is None:
            return
        self._drag = (self._drag[0], self._time_at(event.x))
        self.redraw()

    def _release(self, event) -> None:
        if self._drag is None:
            return
        start, end = self._drag
        self._drag = None
        moved = abs((self._press_x or 0) - event.x)
        self._press_x = None
        if moved < 4 or abs(end - start) < 0.3:
            self._seek(self._time_at(event.x))
            self.redraw()
            return
        lo, hi = sorted((start, end))
        self.app.workflow.apply_timeline_selection((lo, hi))
        self.redraw()

    def _seek(self, seconds: float) -> None:
        workflow = getattr(self.app, "workflow", None)
        if workflow is not None and getattr(workflow, "section_files", None):
            workflow.play_at_book_time(seconds)
            self.app.player_bar.update_time(seconds)
            return
        self.app.session.set_playhead(seconds)
        if self.app.player.alive:
            self.app.player.seek_to(seconds)
        self.app.player_bar.update_time(seconds)

    def _mousewheel(self, event) -> None:
        self._wheel(event, 1 if event.delta > 0 else -1)

    def _wheel(self, event, direction: int) -> None:
        duration = self._data()["duration"]
        if duration <= 0:
            return
        start, end = self._span(duration)
        span = end - start
        if event.state & 0x0001:
            shift = span * 0.1 * (-1 if direction > 0 else 1)
            self.view = (max(0.0, start + shift), min(duration, end + shift))
        else:
            factor = 0.8 if direction > 0 else 1.25
            anchor = self._time_at(event.x)
            new_span = max(5.0, min(duration, span * factor))
            left = (anchor - start) / span if span else 0.5
            new_start = anchor - new_span * left
            new_start = max(0.0, min(new_start, duration - new_span))
            self.view = (new_start, new_start + new_span)
        self.redraw()

    def _show_tip(self, text: str | None) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
        if not text:
            return
        tip = tk.Toplevel(self)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{self.winfo_pointerx() + 8}+{self.winfo_rooty() - 8}")
        tip.configure(background=C["border"])
        ttk.Label(tip, text=text, style="Tip.TLabel", takefocus=0).pack(
            padx=1, pady=1)
        self._tip = tip


def _bin_envelope(path: str, columns: int) -> list[int]:
    samples = array.array("b")
    with open(path, "rb") as handle:
        samples.frombytes(handle.read())
    if not samples:
        return []
    count = len(samples)
    out = []
    for index in range(columns):
        lo = index * count // columns
        hi = max(lo + 1, (index + 1) * count // columns)
        out.append(max(samples[lo:hi]))
    return out


def _load_report(work_dir: str, audio: str) -> dict:
    from chaptertools.audio_quality.analysis import quality_json_path
    path = quality_json_path(work_dir, audio)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}
