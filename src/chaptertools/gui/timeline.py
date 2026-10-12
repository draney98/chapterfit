"""One timeline for the open book: levels, silences, chapters, selection."""

from __future__ import annotations

import array
import json
import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from chaptertools.util import hms

from . import palette, tips
from .table import gap_marks
from .theme import ctk_frame, require_customtkinter

ctk = require_customtkinter()

_HEIGHT = 96


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
        self.legend = tk.StringVar(value="")
        ctk.CTkLabel(bar, textvariable=self.legend,
                     text_color=palette.MUTED, text="").pack(side="left")
        menu = tk.Menu(bar, tearoff=0)
        menu.add_radiobutton(
            label="Similar by chapter cadence", variable=self.mode,
            value="cadence", command=self.redraw)
        menu.add_radiobutton(
            label="Similar by silence length", variable=self.mode,
            value="duration", command=self.redraw)
        more = ttk.Menubutton(bar, text="\u22ef", menu=menu, direction="below")
        more.pack(side="right")
        tips.tooltip(more, "which silences to mark")

        self.canvas = tk.Canvas(
            self, height=_HEIGHT, highlightthickness=0, background=palette.FIELD)
        self.canvas.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag_to)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-4>", lambda e: self._wheel(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self._wheel(e, -1))
        self.canvas.bind("<MouseWheel>", self._mousewheel)
        self.canvas.bind("<Leave>", lambda _e: self._show_tip(None))

    def redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 1)
        height = max(canvas.winfo_height(), _HEIGHT)
        if width < 8:
            return
        data = self._data()
        duration = data["duration"]
        if duration <= 0:
            canvas.create_text(
                8, height / 2, anchor="w", fill=palette.MUTED,
                text="Open a book to see levels, silences, and chapters.")
            self.legend.set("")
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
        return (seconds - start) / (end - start) * width

    def _time_at(self, x: int) -> float:
        duration = self._data()["duration"]
        if duration <= 0:
            return 0.0
        start, end = self._span(duration)
        width = max(self.canvas.winfo_width(), 1)
        t = start + max(0, min(width, x)) / width * (end - start)
        return max(0.0, min(duration, t))

    def _draw_noise(self, canvas, chunks, start, end, width, height) -> None:
        for chunk in chunks:
            if not chunk.get("noisy"):
                continue
            t0 = float(chunk.get("start") or 0)
            t1 = t0 + float(chunk.get("seconds") or chunk.get("duration") or 0)
            if t1 < start or t0 > end:
                continue
            x0 = self._x(max(t0, start), start, end, width)
            x1 = self._x(min(t1, end), start, end, width)
            canvas.create_rectangle(x0, 0, max(x0 + 1, x1), height,
                                    fill=palette.Q_NOISY, width=0)

    def _draw_levels(self, canvas, data, start, end, width, height) -> None:
        levels = self._level_bars(data, width)
        if not levels:
            self._draw_chunk_levels(canvas, data["chunks"], start, end, width, height)
            return
        slot = width / len(levels)
        for index, db in enumerate(levels):
            if db is None:
                continue
            bar = max(0.0, min(1.0, (db + 96) / 96))
            top = height - 14 - bar * (height - 22)
            x0 = index * slot
            canvas.create_rectangle(
                x0, top, x0 + max(1, slot - 1), height - 14,
                fill="#8d8a84", width=0)
        self._draw_clips(canvas, data["chunks"], start, end, width)

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

    def _draw_chunk_levels(self, canvas, chunks, start, end, width, height) -> None:
        values = [c.get("lufs") for c in chunks if isinstance(c.get("lufs"), (int, float))]
        if not values:
            return
        low, high = min(values) - 2, max(values) + 2
        span = high - low or 1
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
            top = height - 14 - ((value - low) / span) * (height - 22)
            canvas.create_rectangle(x0, top, max(x0 + 1, x1), height - 14,
                                    fill="#8d8a84", width=0)
        self._draw_clips(canvas, chunks, start, end, width)

    def _draw_clips(self, canvas, chunks, start, end, width) -> None:
        for chunk in chunks:
            if not chunk.get("clipping"):
                continue
            t0 = float(chunk.get("start") or 0)
            t1 = t0 + float(chunk.get("seconds") or 0)
            if t1 < start or t0 > end:
                continue
            x0 = self._x(max(t0, start), start, end, width)
            x1 = self._x(min(t1, end), start, end, width)
            canvas.create_rectangle(x0, 0, max(x0 + 1, x1), 4,
                                    fill=palette.Q_CLIP, width=0)

    def _draw_silences(self, canvas, data, start, end, width, height) -> None:
        _chapters, extras = gap_marks(data["gaps"], data["rows"], self.mode.get())
        for item in extras:
            gap = item.get("gap") or {}
            mid = gap.get("mid")
            if mid is None or not (start <= mid <= end):
                continue
            x = self._x(mid, start, end, width)
            tag = f"gap{round(mid, 3)}"
            canvas.create_line(x, height - 28, x, height - 14,
                               fill=palette.GAP_BAR, width=2, tags=(tag,))
            tip = f"{hms(mid)} · {gap.get('dur', 0):.2f} s silence"
            canvas.tag_bind(tag, "<Enter>", lambda _e, text=tip: self._show_tip(text))
            canvas.tag_bind(tag, "<Leave>", lambda _e: self._show_tip(None))
        count = len(extras)
        by = "cadence" if self.mode.get() == "cadence" else "silence length"
        placed = sum(1 for row in data["rows"] if row.placed is not None)
        self.legend.set(
            f"{placed} chapters · {count} silences by {by}" if count
            else f"{placed} chapters")

    def _draw_chapters(self, canvas, data, start, end, width, height) -> None:
        font = tkfont.nametofont("TkDefaultFont", root=self)
        last_x = -100.0
        for row in data["rows"]:
            t = row.placed if row.placed is not None else row.published
            if t is None or not (start <= t <= end):
                continue
            x = self._x(t, start, end, width)
            problem = any(tag in ("conflict", "unplaced", "lowconf") for tag in row.tags)
            color = "#9b1c1c" if problem else palette.PIN_BAR
            canvas.create_line(x, 8, x, height - 14, fill=color, width=1)
            if x - last_x >= 16:
                canvas.create_text(x + 2, 2, anchor="nw", text=str(row.index + 1),
                                   fill=color, font=font)
                last_x = x
        show_preview = getattr(self.app.workflow, "_step", "") == "match"
        if not show_preview:
            return
        for t in self.app.session.preview_marks:
            if not (start <= t <= end):
                continue
            x = self._x(t, start, end, width)
            canvas.create_line(x, 10, x, height - 16, fill=palette.PIN_BAR, dash=(3, 3))

    def _draw_selection(self, canvas, start, end, width, height) -> None:
        span = self._drag or self.app.session.selection
        if not span:
            return
        a, b = span
        x0 = self._x(min(a, b), start, end, width)
        x1 = self._x(max(a, b), start, end, width)
        canvas.create_rectangle(x0, 0, max(x0 + 2, x1), height,
                                fill="#d6e4f7", outline=palette.Q_ACCENT, width=1)

    def _draw_playhead(self, canvas, start, end, width, height) -> None:
        t = self.app.session.playhead
        if t < start or t > end:
            return
        x = self._x(t, start, end, width)
        canvas.create_line(x, 0, x, height, fill="#111111", width=2, tags=("playhead",))

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
        tk.Label(tip, text=text, background=palette.TIP, foreground=palette.TEXT,
                 relief="solid", borderwidth=1, padx=4, pady=1,
                 takefocus=0).pack()
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
