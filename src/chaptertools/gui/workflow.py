"""Main-area controller and stage panels: measure → … → finalize.

WorkflowPanel owns the command plumbing (one method per chapterfit
subcommand, all subprocess-driven via app.runner) and builds the main
area: header, stage bar, one visible stage panel, and the notebook tabs
(Chapters, Transcripts). The Log tab and the status bar belong to the app.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from chaptertools.envelope.emit import select_chapters
from chaptertools.util import clock, hms, parse_time

from . import fonts, palette, tips
from .harvest import found_summary, harvest_book
from .library import analysis_path, load_analysis, work_dir_for
from .runner import Job
from .stages import (
    ATTENTION,
    BLOCKED,
    STAGE_KEYS,
    STAGE_LABELS,
    DiskFacts,
    StageBar,
    next_stage,
    recommend,
    resolve_evidence,
    stage_states,
)


def resolve_chapterfit() -> list[str] | None:
    """Root chapterfit.py when running from the project tree, else installed.

    Runs with -u: stdout is unbuffered, so when a step dies its stderr
    (errors, tracebacks) interleaves in true order in the log instead of
    appearing above the flushed stdout block.
    """
    root = Path(__file__).resolve().parents[3]
    script = root / "chapterfit.py"
    if script.is_file():
        return [sys.executable, "-u", str(script)]
    exe = shutil.which("chapterfit")
    return [exe] if exe else None


def whisper_python() -> str | None:
    p = os.environ.get("WHISPER_PYTHON") \
        or os.path.expanduser("~/.venvs/whisper/bin/python")
    return p if os.path.isfile(p) and os.access(p, os.X_OK) else None


def _live(t: float) -> str:
    """Playback clock with hundredths: 1:02:03.45."""
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _elide(text: str, font, room: int) -> str:
    """Drop characters from the middle until `text` fits in `room` px."""
    if font.measure(text) <= room:
        return text
    lo, hi = 0, len(text)
    while lo < hi:  # most characters kept that still fit
        mid = (lo + hi + 1) // 2
        cand = text[:(mid + 1) // 2] + "…" + text[len(text) - mid // 2:]
        if font.measure(cand) <= room:
            lo = mid
        else:
            hi = mid - 1
    return text[:(lo + 1) // 2] + "…" + text[len(text) - lo // 2:]


def _t(v) -> str:
    """No empty time cells: a zero clock instead of '-' or blank."""
    if v is None or v == "" or v == "-":
        return "0:00:00.000"
    return str(v) if isinstance(v, str) and ":" in v else hms(v)


CADENCE_WINDOW = 300.0  # a predicted position only claims silences this near

# (column id, heading, widest value it can hold, anchor); Title stretches
CHAPTER_COLUMNS = (
    ("index", "#", "?999", "e"),
    ("title", "Title", None, "w"),
    ("published", "Published", "12:34:56.789", "e"),
    ("time", "Placed", "12:34:56.789", "e"),
    ("gap", "Gap", "12.34 s", "e"),
    ("confidence", "Conf", "0.00", "e"),
    ("pinned", "Pin", "! pin", "center"),
)
CELL_PAD = 24  # treeview cell padding + heading separator, both sides
SHORTCUTS = ("Space play/pause · ←/→ ±5 s · Shift+←/→ ±10 s · "
             "Enter pin · Delete remove pin")
TITLE_MIN = "An Average Chapter Title"


def similar_gaps(gaps: list[dict], pins: dict[int, float],
                 chapters: list[dict], mode: str,
                 limit: int = 25) -> list[dict]:
    """The gaps most similar to the pinned chapters.

    mode "cadence": the pins (published start -> pin time) anchor a local
    reading rate; each unpinned chapter's position is predicted and the
    silences nearest those predictions are the candidates.
    mode "duration": the reference is the median duration of the silences
    the pins sit on; the closest-duration silences are the candidates.

    Returns [{"gap": g, "pred": s|None, "chapter_title": str,
              "ref": s|None}] best-first, capped at `limit`.
    """
    if not pins or not gaps:
        return []

    def on_gap(t):
        best, bd = None, 1e9
        for g in gaps:
            d = abs(g["mid"] - t)
            if d < bd:
                best, bd = g, d
        return best if bd <= 0.06 else None

    pin_mids = {round(g["mid"], 3) for t in pins.values()
                if (g := on_gap(t))}

    if mode == "duration":
        ref_durs = [g["dur"] for t in pins.values() if (g := on_gap(t))]
        if not ref_durs:
            return []
        ref = statistics.median(ref_durs)
        out = []
        for g in sorted(gaps, key=lambda g: abs(g["dur"] - ref)):
            if round(g["mid"], 3) in pin_mids:
                continue
            out.append({"gap": g, "pred": None, "chapter_title": "",
                        "ref": ref})
            if len(out) >= limit:
                break
        return out

    # cadence: predict each unpinned chapter from the pin anchors
    anchors = sorted((chapters[i]["start"], t) for i, t in pins.items()
                     if i < len(chapters))

    def predict(i):
        if not anchors:
            return None
        p = chapters[i]["start"]
        if len(anchors) == 1:
            return anchors[0][1] + (p - anchors[0][0])
        if anchors[0][0] <= p <= anchors[-1][0]:
            for (p1, t1), (p2, t2) in zip(anchors, anchors[1:], strict=False):
                if p1 <= p <= p2:
                    if p2 == p1:
                        return t1
                    return t1 + (t2 - t1) * (p - p1) / (p2 - p1)
        if p < anchors[0][0]:
            (p1, t1), (p2, t2) = anchors[0], anchors[1]
        else:
            (p1, t1), (p2, t2) = anchors[-2], anchors[-1]
        rate = (t2 - t1) / (p2 - p1)
        return t1 + rate * (p - p1)

    candidates = []
    for i, ch in enumerate(chapters):
        if i in pins:
            continue
        pred = predict(i)
        if pred is None:
            continue
        for g in gaps:
            dist = abs(g["mid"] - pred)
            if dist <= CADENCE_WINDOW and round(g["mid"], 3) not in pin_mids:
                candidates.append((dist, g, pred, ch.get("title", "")))
    candidates.sort(key=lambda x: x[0])
    out, seen = [], set()
    for _dist, g, pred, title in candidates:
        key = round(g["mid"], 3)
        if key in seen:
            continue
        seen.add(key)
        out.append({"gap": g, "pred": pred, "chapter_title": title,
                    "ref": None})
        if len(out) >= limit:
            break
    return out


class WorkflowPanel(ttk.Frame):
    """The main area: header, stage bar, stage panels, notebook tabs."""

    def __init__(self, master, app):
        super().__init__(master, padding=(8, 4))
        self.app = app
        self.notebook = ttk.Notebook(self)
        self.audio: str | None = None
        self.work_dir: str | None = None
        self.analysis: dict = {}
        self.harvest: dict = {}
        self._row_data: dict = {}
        self._cf = resolve_chapterfit()
        self._stage: str = "measure"
        self._disk: DiskFacts | None = None
        # audio path -> artifact mtime when its finalize dry run succeeded
        self._dry_ok: dict[str, float | None] = {}
        self._pin_autofilled = False
        self._sweep_autofilled = False
        self._busy = False
        self._tips: dict = {}

        self._build()
        style = ttk.Style(self)
        style.configure("Accent.TButton",
                        font=fonts.derived(self, weight="bold"))
        self._refresh()

    def _tip(self, widget, text: str):
        self._tips[widget] = tip = tips.tooltip(widget, text)
        return tip

    # ------------------------------------------------------------ widgets --
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.book_var = tk.StringVar(value="(nothing selected)")
        book_lbl = ttk.Label(head, textvariable=self.book_var,
                             font=tkfont.nametofont("TkHeadingFont", root=self))
        book_lbl.grid(row=0, column=0, sticky="w")
        self._book_label = book_lbl
        self._header_text = "(nothing selected)"
        head.bind("<Configure>", lambda e: self._fit_header(e.width))
        self.head_tooltip = None
        book_lbl.bind("<Enter>", self._show_path_tip)
        book_lbl.bind("<Leave>", self._hide_tip)
        self.found_var = tk.StringVar(value="")

        self.stage_bar = StageBar(self, self.set_stage)
        self.stage_bar.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        # amber explainer strip: shown when the SELECTED stage needs attention
        self.banner_var = tk.StringVar(value="")
        self.banner = ttk.Label(self, textvariable=self.banner_var,
                                background=palette.BANNER,
                                foreground=palette.WARN,
                                padding=(8, 3))

        # panel host sizes to the tallest visible panel (nothing clips)
        self._panel_host = ttk.Frame(self)
        self._panel_host.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self._panel_host.columnconfigure(0, weight=1)
        self._panel_host.rowconfigure(0, weight=1)
        self._wrap_labels = [self.banner]
        self._panel_host.bind("<Configure>", self._on_host_resize)
        self._panels = {
            "measure": self._panel_measure(self._panel_host),
            "identify": self._panel_identify(self._panel_host),
            "align": self._panel_align(self._panel_host),
            "resolve": self._panel_resolve(self._panel_host),
            "emit": self._panel_emit(self._panel_host),
            "finalize": self._panel_finalize(self._panel_host),
        }

        self._build_chapters_tab()
        self._build_gaps_tab()
        self._build_transcripts_tab()
        self.notebook.grid(row=4, column=0, columnspan=2, sticky="nsew")
        self.set_stage("measure")

    TABLE_MIN_ROWS = 4

    def _panel_need(self, key) -> tuple[int, int]:
        """(width, height) a stage panel needs; Resolve counted stacked."""
        if key != "resolve":
            p = self._panels[key]
            return p.winfo_reqwidth(), p.winfo_reqheight()
        left, right = self._resolve_left, self._resolve_right
        summary = self._panels["resolve"].grid_slaves(row=0)[0]
        w = max(left.winfo_reqwidth(), right.winfo_reqwidth(),
                summary.winfo_reqwidth()) + 8
        h = (summary.winfo_reqheight() + left.winfo_reqheight()
             + right.winfo_reqheight() + 16)
        return w, h

    def min_width(self) -> int:
        self.update_idletasks()
        table = sum(int(self.tree.column(c, "minwidth"))
                    for c, *_ in CHAPTER_COLUMNS) + 24
        return max([self._panel_need(k)[0] for k in self._panels]
                   + [self.stage_bar.winfo_reqwidth(), table]) + 16

    def min_height(self) -> int:
        """Header, stage bar, banner, the tallest panel, and a table that
        still shows a few rows."""
        self.update_idletasks()
        rowh = int(ttk.Style(self).lookup("Chapter.Treeview", "rowheight")
                   or 24)
        chrome = sum(w.winfo_reqheight() for w in
                     (self.grid_slaves(row=0)[0], self.stage_bar,
                      self.banner))
        tab_extra = sum(w.winfo_reqheight() for w in
                        self._chapters_tab.grid_slaves()
                        if w is not self.tree)
        tallest = max(self._panel_need(k)[1] for k in self._panels)
        return (chrome + tallest + tab_extra + 40
                + rowh * (self.TABLE_MIN_ROWS + 1) + 24)

    def _on_host_resize(self, event):
        """Long text wraps to the panel width instead of widening it."""
        width = event.width
        for lbl in self._wrap_labels:
            lbl.configure(wraplength=max(width - 24, 200))
        need = (self._resolve_left.winfo_reqwidth()
                + self._resolve_right.winfo_reqwidth() + 16)
        self._layout_resolve(side_by_side=need <= width)

    # ---------------------------------------------------------- stage panels
    def _result_line(self, parent, row, colspan=1):
        var = tk.StringVar(value="")
        lbl = ttk.Label(parent, textvariable=var, foreground=palette.MUTED,
                        justify="left")
        lbl.grid(row=row, column=0, columnspan=colspan, sticky="w",
                 pady=(4, 0))
        self._wrap_labels.append(lbl)
        return var

    def _panel_measure(self, host):
        p = ttk.Frame(host)
        p.columnconfigure(0, weight=1)
        row = ttk.Frame(p)
        row.grid(row=0, column=0, sticky="ew")
        self.measure_btn = self._primary(row, 0, 0, "Run measure",
                                         self.cmd_measure)
        ttk.Label(row, text="analyzes the audio: loudness envelope, timeline "
                            "check, noise calibration, gaps").grid(
            row=0, column=1, sticky="w", padx=8)
        self.measure_result = self._result_line(p, row=1, colspan=1)
        self._tip(self.measure_btn, "decode the whole file once: envelope, "
                     "timeline check, calibration, and the gap list")
        return p

    def _build_gaps_tab(self):
        """Gap strip: pinned chapters + the gaps most similar to them."""
        p = ttk.Frame(self.notebook, padding=4)
        p.columnconfigure(0, weight=1)
        p.rowconfigure(1, weight=1)
        controls = ttk.Frame(p)
        controls.grid(row=0, column=0, sticky="ew")
        ttk.Label(controls, text="Similar gaps by:").pack(side="left")
        self.gap_mode_var = tk.StringVar(value="cadence")
        for text, mode in (("chapter cadence", "cadence"),
                           ("silence length", "duration")):
            ttk.Radiobutton(controls, text=text, value=mode,
                            variable=self.gap_mode_var,
                            command=self._draw_gap_graph).pack(
                side="left", padx=(6, 0))
        self._tip(controls.winfo_children()[1],
                     "silences nearest where the pin spacing predicts the "
                     "unpinned chapters should fall")
        self._tip(controls.winfo_children()[2],
                     "silences whose duration matches the ones your pins "
                     "sit on")
        self.gap_canvas = tk.Canvas(p, height=120, highlightthickness=0,
                                    background=palette.FIELD)
        self.gap_canvas.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        self.gap_canvas.bind("<Configure>",
                             lambda e: self._draw_gap_graph())
        self.gap_legend = tk.StringVar(value="")
        ttk.Label(p, textvariable=self.gap_legend,
                  foreground=palette.MUTED).grid(
            row=2, column=0, sticky="w")
        self.notebook.add(p, text="Gaps")
        self._gaps_tab = p

    def _draw_gap_graph(self):
        """Bar strip: pinned chapters (purple) + the gaps most similar to
        them (blue) - by cadence (default) or by silence length."""
        c = self.gap_canvas
        c.delete("all")
        gaps = (self.analysis.get("gaps") or {}).get("gaps") or []
        dur = (self.analysis.get("envelope") or {}).get("duration") or 0.0
        pins = {int(k): v for k, v in (self.analysis.get("pins") or {})
                .items()}
        chapters = (self.analysis.get("identification") or {}) \
            .get("chapters") or []
        if not gaps or not dur:
            self.gap_legend.set("")
            return
        if not pins:
            self.gap_legend.set("pin chapters to focus the strip")
            return

        # placed chapters still claim gaps for hover context
        claimed = {}
        for r in (self.analysis.get("alignment") or {}).get("results", []):
            t = r.get("time")
            if t is None:
                continue
            for g in gaps:
                if abs(g["mid"] - t) <= 0.06:
                    claimed[round(g["mid"], 3)] = r

        mode = self.gap_mode_var.get()
        similar = similar_gaps(gaps, pins, chapters, mode)

        # the silences the pins sit on define pin-bar heights
        def on_gap(t):
            return min(gaps, key=lambda g: abs(g["mid"] - t)) \
                if min((abs(g["mid"] - t) for g in gaps), default=1e9) \
                <= 0.06 else None

        pin_draws = []
        for idx in sorted(pins):
            g = on_gap(pins[idx])
            title = chapters[idx]["title"] if idx < len(chapters) else "?"
            pin_draws.append({"x": pins[idx], "gap": g,
                              "title": title, "n": idx + 1})

        durs = [d["gap"]["dur"] for d in pin_draws if d["gap"]] \
            + [s["gap"]["dur"] for s in similar]
        maxdur = max(durs) if durs else 1.0
        width = max(c.winfo_width(), 400)
        height = max(c.winfo_height(), int(c["height"]))
        axis_h = tkfont.nametofont("TkDefaultFont", root=self).metrics("linespace") + 4
        mid_y = height - axis_h
        PURPLE, BLUE = palette.PIN_BAR, palette.GAP_BAR

        def bar(x_seconds, d, color, tip):
            x = 4 + (x_seconds / dur) * (width - 8)
            h = 6 + (d / maxdur) * (mid_y - 10) if d else 8
            tag = f"g{round(x_seconds, 3)}"
            c.create_rectangle(x - 2, mid_y - h, x + 2, mid_y,
                               fill=color, width=0, tags=(tag,))
            if tip:
                c.tag_bind(tag, "<Enter>",
                           lambda e, t=tip: self._gap_tip(t))
                c.tag_bind(tag, "<Leave>", lambda e: self._gap_tip(None))

        for d in pin_draws:
            g = d["gap"]
            tip = (f"{hms(d['x'])} · {d['title']} (#{d['n']}) · pinned"
                   + (f" · on a {g['dur']:.2f} s silence" if g
                      else " · off-gap"))
            bar(d["x"], g["dur"] if g else None, PURPLE, tip)

        for s in similar:
            g = s["gap"]
            r = claimed.get(round(g["mid"], 3))
            note = ""
            if r is not None:
                note = f" · claimed by {r['title']} (#{r['index'] + 1})"
            if mode == "cadence" and s.get("pred") is not None:
                note += (f" · near {s['chapter_title']}"
                         f" (predicted {hms(s['pred'])})")
            elif mode == "duration" and s.get("ref") is not None:
                note = f" · pins' silences run ~{s['ref']:.2f} s"
            bar(g["mid"], g["dur"], BLUE,
                f"{hms(g['mid'])} · {g['dur']:.2f} s{note or ' · unclaimed'}")

        c.create_line(0, mid_y, width, mid_y, fill=palette.MUTED)
        for frac, anchor in ((0, "w"), (0.5, "center"), (1, "e")):
            c.create_text(4 + frac * (width - 8), height - axis_h // 2,
                          text=hms(dur * frac), anchor=anchor,
                          font="TkDefaultFont", fill=palette.MUTED)
        by = "cadence" if mode == "cadence" else "silence length"
        self.gap_legend.set(
            f"{len(pin_draws)} pinned chapters (purple) · "
            f"{len(similar)} similar gaps by {by} (blue) · hover for detail")

    _gap_tipwin = None

    def _gap_tip(self, text):
        if self._gap_tipwin is not None:
            self._gap_tipwin.destroy()
            self._gap_tipwin = None
        if text:
            c = self.gap_canvas
            self._gap_tipwin = tw = tk.Toplevel(self)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{c.winfo_pointerx()}+{c.winfo_rooty() - 24}")
            tk.Label(tw, text=text, background=palette.TIP,
                     foreground=palette.TEXT, font="TkDefaultFont",
                     relief="solid", borderwidth=1, padx=4, pady=1).pack()

    def _panel_identify(self, host):
        p = ttk.Frame(host)
        p.columnconfigure(1, weight=1)
        ttk.Label(p, text="Source:").grid(row=0, column=0, sticky="w")
        self.identify_mode = tk.StringVar(value="asin")
        self.identify_value = tk.StringVar()
        radios = ttk.Frame(p)
        radios.grid(row=0, column=1, columnspan=3, sticky="w")
        modes = [("ASIN", "asin", "an Audible ASIN; chapters come from "
                  "Audnexus"),
                 ("JSON file", "json", "a chapters JSON file on disk"),
                 ("Embedded", "emb", "the chapters already in the audio "
                  "file")]
        self.identify_radios = []
        for text, mode, tip in modes:
            rb = ttk.Radiobutton(radios, text=text, value=mode,
                                 variable=self.identify_mode,
                                 command=self._identify_mode_changed)
            rb.pack(side="left", padx=(4, 8))
            self.identify_radios.append(rb)
            self._tip(rb, tip)
        self.identify_entry = ttk.Entry(p, textvariable=self.identify_value,
                                        width=24)
        self.identify_entry.grid(row=1, column=1, sticky="ew", padx=(4, 4),
                                 pady=(4, 0))
        self.identify_browse = ttk.Button(p, text="Browse…",
                                          command=self._identify_browse)
        self.identify_browse.grid(row=1, column=2, sticky="w", pady=(4, 0))
        self._tip(self.identify_browse, "pick a chapters JSON file")
        self.identify_btn = self._primary(p, 1, 3, "Identify",
                                          self.cmd_identify_primary)
        self.identify_btn.grid_configure(pady=(4, 0))
        ttk.Label(p, text="chooses and verifies the chapter list against "
                          "the audio").grid(row=2, column=0, columnspan=4,
                                            sticky="w", pady=(4, 0))
        self.identify_result = self._result_line(p, row=3, colspan=4)
        self._tip(self.identify_btn, "check the chosen chapter list "
                     "against the real silences before aligning")
        self._identify_mode_changed()
        return p

    def _panel_align(self, host):
        p = ttk.Frame(host)
        p.columnconfigure(3, weight=1)
        ttk.Label(p, text="Minimum confidence:").grid(row=0, column=0,
                                                      sticky="w")
        self.min_conf = tk.DoubleVar(value=0.5)
        self.min_conf.trace_add("write", lambda *_: self._refresh())
        self.min_conf_spin = ttk.Spinbox(p, from_=0.0, to=1.0, increment=0.05,
                                         width=5, textvariable=self.min_conf)
        self.min_conf_spin.grid(row=0, column=1, sticky="w")
        ttk.Label(p, text="Near (CH=TIME):").grid(row=0, column=2, sticky="e",
                                                  padx=(10, 2))
        self.near_var = tk.StringVar()
        self.near_entry = ttk.Entry(p, textvariable=self.near_var, width=12)
        self.near_entry.grid(row=0, column=3, sticky="w")
        self.align_btn = self._primary(p, 0, 4, "Align", self.cmd_align)
        ttk.Label(p, text="places every published chapter on a real gap").grid(
            row=1, column=0, columnspan=5, sticky="w")
        self.align_result = self._result_line(p, row=2, colspan=5)
        self._tip(self.align_btn, "place every published chapter on a "
                     "real gap, keeping local reading rate consistent")
        self._tip(self.min_conf_spin, "chapters below this confidence are "
                  "withheld from Emit (shaded amber in the table)")
        self._tip(self.near_entry, "optional CH=TIME hint, e.g. 12=3:45:00, "
                  "that aims the alignment near a known position")
        return p

    def _panel_resolve(self, host):
        p = ttk.Frame(host, padding=(4, 2))

        # summary line: unresolved chapters + recommended next action
        summary = ttk.Frame(p)
        summary.grid(row=0, column=0, columnspan=2, sticky="ew")
        # only the chapter links are rebuilt on refresh; the next label stays
        self.unresolved_frame = ttk.Frame(summary)
        self.unresolved_frame.pack(side="left")
        self.next_var = tk.StringVar(value="")
        self.next_label = ttk.Label(summary, textvariable=self.next_var,
                                    foreground=palette.OK,
                                    font=fonts.derived(self, weight="bold"))
        self.next_label.pack(side="right")

        # left: automatic escalation - label, input, button, cost, status
        left = ttk.LabelFrame(p, text="Automatic", padding=4)
        self._resolve_left = left
        left.columnconfigure(4, weight=1)

        def option(row, label, button, cmd, cost_var, status_var, tip):
            ttk.Label(left, text=label).grid(row=row, column=0, sticky="e",
                                             padx=(0, 4))
            b = ttk.Button(left, text=button, command=cmd)
            b.grid(row=row, column=2, sticky="ew", padx=4)
            ttk.Label(left, textvariable=cost_var).grid(row=row, column=3,
                                                        sticky="w")
            ttk.Label(left, textvariable=status_var).grid(
                row=row, column=4, sticky="w", padx=(6, 0))
            self._tip(b, tip)
            return b

        self.hint_var = tk.StringVar()
        self.hint_entry = ttk.Entry(left, textvariable=self.hint_var, width=12)
        self.hint_entry.grid(row=0, column=1, sticky="ew")
        self._tip(self.hint_entry, "CH=TIME, e.g. 12=3:45:00")
        self.hint_status = tk.StringVar(value="")
        self.hint_btn = option(
            0, "Position hint:", "Align with hint", self._resolve_hint,
            tk.StringVar(value="free"), self.hint_status,
            "re-runs Align aimed at roughly this position (CH=TIME); "
            "free - it is not a pin")

        self.listen_status = tk.StringVar(value="not run")
        self.listen_btn = option(
            1, "Listen:", "Listen", self.cmd_listen,
            tk.StringVar(value="~4 min"), self.listen_status,
            "transcribes ~13 s after each candidate gap (about 4 minutes "
            "for a typical book) and pins the chapter announcements it "
            "hears")

        rng = ttk.Frame(left)
        rng.grid(row=2, column=1, sticky="ew")
        self.sweep_start = tk.StringVar()
        self.sweep_end = tk.StringVar()
        self.sweep_start_entry = ttk.Entry(rng, textvariable=self.sweep_start,
                                           width=9)
        self.sweep_start_entry.pack(side="left")
        ttk.Label(rng, text="–").pack(side="left")
        self.sweep_end_entry = ttk.Entry(rng, textvariable=self.sweep_end,
                                         width=9)
        self.sweep_end_entry.pack(side="left")
        for e in (self.sweep_start_entry, self.sweep_end_entry):
            # a typed range belongs to the user: row selection stops filling it
            e.bind("<Key>", self._sweep_edited, add="+")
            self._tip(e, "start and end of the span to sweep (H:MM:SS); "
                         "selecting a chapter fills them until you type")
        self.range_cost = tk.StringVar(value="")
        self.range_status = tk.StringVar(value="")
        self.range_btn = option(
            2, "Sweep range:", "Sweep range", self._sweep_range,
            self.range_cost, self.range_status,
            "reads this span continuously and pins announced chapters - "
            "the targeted fix for one stubborn chapter")

        self.probe_status = tk.StringVar(value="not run")
        self.probe_btn = option(
            3, "Sweep probe:", "Probe 10 min", self._sweep_probe,
            tk.StringVar(value="10 min"), self.probe_status,
            "sweeps 10 minutes from the quarter point to learn whether "
            "chapters are announced at all")

        self.full_cost = tk.StringVar(value="")
        self.swept_status = tk.StringVar(value="not run")
        self.full_btn = option(
            4, "Full sweep:", "Full sweep", self._sweep_full,
            self.full_cost, self.swept_status,
            "sweeps the whole book; already-swept spans are cached and "
            "cost nothing to redo")

        # right: pin by ear - one row per step, top to bottom
        right = ttk.LabelFrame(p, text="Pin by ear", padding=4)
        self._resolve_right = right
        right.columnconfigure(0, weight=1)
        what = ttk.Frame(right)
        what.grid(row=0, column=0, sticky="w")
        self.byear_title = tk.StringVar(value="Select a chapter in the table")
        ttk.Label(what, textvariable=self.byear_title,
                  font=fonts.derived(self, weight="bold")).pack(side="left")
        self.byear_info = tk.StringVar(value="")
        ttk.Label(what, textvariable=self.byear_info).pack(
            side="left", padx=(8, 0))

        transport = ttk.Frame(right)
        transport.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.seek_btns = []

        # clam gives buttons an 11-character minimum; transport keys are short
        ttk.Style(self).configure("Transport.TButton", width=0)

        def seek(delta):
            b = ttk.Button(transport, text=f"{delta:+d} s",
                           style="Transport.TButton",
                           command=lambda d=delta: self._key_seek(d),
                           state=["disabled"])
            b.pack(side="left", padx=1)
            self.seek_btns.append(b)
            self._tip(b, f"skip {'back' if delta < 0 else 'forward'} "
                         f"{abs(delta)} s ({'Shift+' if abs(delta) == 10 else ''}"
                         f"{'←' if delta < 0 else '→'} in the table)")

        seek(-10)
        seek(-5)
        self.play_btn = ttk.Button(transport, text="▶ Play",
                                   command=self._key_playpause,
                                   state=["disabled"])
        self.play_btn.pack(side="left", padx=1)
        self._tip(self.play_btn, "play from the selected chapter "
                     "(double-click a row or Space does the same); "
                     "pauses/resumes while playing")
        seek(5)
        seek(10)
        self.stop_btn = ttk.Button(transport, text="■ Stop playback",
                                   command=self.app.stop_audio,
                                   state=["disabled"])
        self.stop_btn.pack(side="left", padx=(6, 0))
        self._tip(self.stop_btn, "stop playback")

        ttk.Label(transport, text="Playback:").pack(side="left",
                                                    padx=(12, 0))
        self.playback_var = tk.StringVar(value="0:00:00.000")
        ttk.Label(transport, textvariable=self.playback_var,
                  font=tkfont.nametofont("TkFixedFont", root=self)).pack(
            side="left", padx=(4, 0))

        time_row = ttk.Frame(right)
        time_row.grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Label(time_row, text="Time (H:MM:SS):").pack(side="left")
        self.pin_time = tk.StringVar(value="0:00:00.000")
        pin_time_entry = ttk.Entry(time_row, textvariable=self.pin_time,
                                   width=12)
        pin_time_entry.pack(side="left", padx=(4, 6))
        pin_time_entry.bind("<Return>",
                            lambda e: self.play_from_time() or "break")
        self._tip(pin_time_entry, "the time Pin will mark and Play from "
                     "time will start at (H:MM:SS); Enter plays from it")
        self._set_btn = ttk.Button(time_row, text="Set from playback",
                                   command=self.set_from_playback,
                                   state=["disabled"])
        self._set_btn.pack(side="left", padx=(0, 4))
        self._tip(self._set_btn, "copy the current (frozen) playback "
                     "clock into the time field")
        self.playfrom_btn = ttk.Button(time_row, text="▶ Play from time",
                                       command=self.play_from_time,
                                       state=["disabled"])
        self.playfrom_btn.pack(side="left")
        self._tip(self.playfrom_btn, "start playback (no time limit) at "
                     "the time in the field")
        self.pin_ch = tk.StringVar()
        self._pin_autofilled = False

        pin_row = ttk.Frame(right)
        pin_row.grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.pin_btn = ttk.Button(pin_row, text="Pin", command=self.cmd_pin)
        self.pin_btn.pack(side="left", padx=(0, 4))
        self._tip(self.pin_btn, "fix the selected chapter at the time in "
                     "the field, then re-align (Enter in the table)")
        self.unpin_btn = ttk.Button(pin_row, text="Remove pin",
                                    command=self.cmd_unpin)
        self.unpin_btn.pack(side="left")
        self._tip(self.unpin_btn, "drop this chapter's pin and re-align "
                     "(Delete in the table)")
        self._resolve_stacked = None
        self._layout_resolve(side_by_side=True)
        return p

    def _layout_resolve(self, side_by_side: bool):
        """Automatic and By ear sit side by side when the window is wide
        enough, and stack when it is not - neither ever clips."""
        if self._resolve_stacked is (not side_by_side):
            return
        self._resolve_stacked = not side_by_side
        left, right = self._resolve_left, self._resolve_right
        p = left.master
        if side_by_side:
            p.columnconfigure(0, weight=3)
            p.columnconfigure(1, weight=2)
            left.grid(row=1, column=0, columnspan=1, sticky="nsew")
            right.grid(row=1, column=1, columnspan=1, sticky="nsew",
                       padx=(8, 0), pady=0)
        else:
            p.columnconfigure(0, weight=1)
            p.columnconfigure(1, weight=0)
            left.grid(row=1, column=0, columnspan=2, sticky="nsew")
            right.grid(row=2, column=0, columnspan=2, sticky="nsew",
                       padx=0, pady=(6, 0))

    def _panel_emit(self, host):
        p = ttk.Frame(host)
        info = ttk.LabelFrame(p, text="Book info (written to ID3 on emit)",
                              padding=6)
        info.pack(side="left", fill="both", expand=True)
        for c in range(6):
            info.columnconfigure(c, weight=1)
        self.meta_vars = {}
        for n, key in enumerate(("title", "author", "album", "year",
                                 "genre", "comment")):
            row, col = divmod(n, 3)
            ttk.Label(info, text=f"{key}:").grid(row=row * 2, column=col * 2,
                                                 sticky="e", padx=(8, 2))
            var = tk.StringVar()
            self.meta_vars[key] = var
            ttk.Entry(info, textvariable=var, width=14).grid(
                row=row * 2, column=col * 2 + 1, sticky="ew", padx=(0, 8),
                pady=1)
        self.reread_btn = ttk.Button(info, text="Re-read",
                                     command=self._reharvest)
        self.reread_btn.grid(row=2, column=6, sticky="w")
        self._tip(self.reread_btn, "re-read tags and chapters from the "
                     "folder (after emit changed the file)")
        found = ttk.Label(info, textvariable=self.found_var,
                          foreground=palette.MUTED, justify="left")
        found.grid(row=4, column=0, columnspan=7, sticky="w", pady=(4, 0))
        self._wrap_labels.append(found)
        side = ttk.Frame(p)
        side.pack(side="left", fill="y", padx=(12, 0))
        self.embed_var = tk.BooleanVar(value=True)
        embed_cb = ttk.Checkbutton(side, text="Embed chapters",
                                   variable=self.embed_var)
        embed_cb.grid(row=0, column=0, sticky="w")
        self._tip(embed_cb, "also write a chaptered copy of the audio "
                  "(<name>.chapters.<ext>) for Finalize to put in place; "
                  "off writes only chapters.txt and chapters.json")
        self.emit_btn = ttk.Button(side, text="Emit", command=self.cmd_emit)
        self.emit_btn.grid(row=1, column=0, sticky="ew", pady=4)
        self._tip(self.emit_btn, "write the supported chapters into a "
                     "chaptered copy of the file (and the ID3 tags above)")
        self.emit_result = self._result_line(side, row=2)
        return p

    def _panel_finalize(self, host):
        p = ttk.Frame(host)
        p.columnconfigure(1, weight=1)
        self.finalize_btn = self._primary(p, 0, 0, "Dry run",
                                          self.cmd_finalize_dry)
        self.apply_btn = ttk.Button(p, text="Apply", state=["disabled"],
                                    command=self.cmd_finalize_apply)
        self.apply_btn.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._tip(self.finalize_btn, "preview everything finalize "
                     "would do - nothing is changed")
        self.allow_delete_var = tk.BooleanVar(value=False)
        allow_delete_cb = ttk.Checkbutton(
            p, text="allow delete-and-re-add (clears listening progress)",
            variable=self.allow_delete_var)
        allow_delete_cb.grid(row=1, column=0, columnspan=2, sticky="w")
        self._tip(allow_delete_cb, "last resort: delete the ABS database "
                     "entry and let the library scan re-add it; clears "
                     "listening progress")
        self.finalize_result = self._result_line(p, row=2, colspan=2)
        self._tip(self.apply_btn, "stage the original away, rename the "
                     "chaptered file into place, write album.nfo, and "
                     "reload Audiobookshelf")
        return p

    def _primary(self, parent, row, col, text, cmd):
        b = ttk.Button(parent, text=text, command=cmd)
        b.grid(row=row, column=col, sticky="w", padx=2)
        return b

    def _btn(self, parent, col, row, text, cmd):
        b = ttk.Button(parent, text=text, command=cmd)
        b.grid(row=row, column=col, sticky="ew", padx=2)
        return b

    # ------------------------------------------------------------- tabs --
    def size_table(self):
        """Table font is the UI base + 2 pt (owner directive). Each column
        is as wide as its heading or its widest possible value; re-run
        after a font size change."""
        row_font = fonts.derived(self, grow=2)
        head_font = tkfont.nametofont("TkHeadingFont", root=self)
        ttk.Style(self).configure(
            "Chapter.Treeview", font=row_font,
            rowheight=row_font.metrics("linespace") + 2)
        self.table_font = row_font
        for col, heading, sample, _anchor in CHAPTER_COLUMNS:
            need = max(head_font.measure(heading),
                       row_font.measure(sample or TITLE_MIN)) + CELL_PAD
            self.tree.column(col, minwidth=need, width=need)

    def _build_chapters_tab(self):
        tab = ttk.Frame(self.notebook)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        cols = [c[0] for c in CHAPTER_COLUMNS]
        self.tree = ttk.Treeview(tab, columns=cols, show="headings",
                                 style="Chapter.Treeview")
        for col, heading, _sample, anchor in CHAPTER_COLUMNS:
            self.tree.heading(col, text=heading, anchor=anchor)
            self.tree.column(col, anchor=anchor, stretch=col == "title")
        self.size_table()
        self.tree.tag_configure("unplaced", background=palette.ROW_UNPLACED)
        self.tree.tag_configure("lowconf", background=palette.ROW_LOWCONF)
        self.tree.tag_configure("conflict", background=palette.ROW_CONFLICT)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tab, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Double-1>", lambda e: self.preview_row())
        self.tree.bind("<<TreeviewSelect>>", self._fill_from_selection)
        for seq, fn in (("space", self._key_playpause),
                        ("Left", lambda e: self._key_seek(-5)),
                        ("Right", lambda e: self._key_seek(5)),
                        ("Shift-Left", lambda e: self._key_seek(-10)),
                        ("Shift-Right", lambda e: self._key_seek(10)),
                        ("Return", lambda e: self.cmd_pin()),
                        ("KP_Enter", lambda e: self.cmd_pin()),
                        ("Delete", lambda e: self.cmd_unpin())):
            self.tree.bind(seq, fn)
        foot = ttk.Frame(tab)
        foot.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        legend = ttk.Frame(foot)
        for color, text in ((palette.ROW_UNPLACED, "unplaced"),
                            (palette.ROW_LOWCONF,
                             "below minimum confidence (withheld)"),
                            (palette.ROW_CONFLICT,
                             "conflicting pin (! in Pin)")):
            tk.Label(legend, text="   ", background=color, relief="solid",
                     borderwidth=1).pack(side="left", padx=(0, 4))
            ttk.Label(legend, text=text).pack(side="left", padx=(0, 12))
        ttk.Label(legend, text="?N = not written to the file").pack(
            side="left")
        self.shortcut_label = ttk.Label(foot, text=SHORTCUTS,
                                        foreground=palette.MUTED)
        self._table_legend = legend
        self._foot_split = None
        foot.bind("<Configure>", self._layout_foot)
        self._layout_foot()
        self.conflict_note = tk.StringVar(value="")
        self.conflict_label = ttk.Label(tab, textvariable=self.conflict_note,
                                        foreground=palette.WARN)
        self.notebook.add(tab, text="Chapters")
        self._chapters_tab = tab

    def _layout_foot(self, event=None):
        """Legend and shortcuts share a line when they fit, else stack."""
        legend, keys = self._table_legend, self.shortcut_label
        width = event.width if event else 10 ** 6
        split = legend.winfo_reqwidth() + keys.winfo_reqwidth() + 24 > width
        if split == self._foot_split:
            return
        self._foot_split = split
        legend.grid(row=0, column=0, sticky="w")
        if split:
            keys.grid(row=1, column=0, sticky="w", padx=0)
        else:
            keys.grid(row=0, column=1, sticky="e", padx=(24, 0))
            legend.master.columnconfigure(1, weight=1)

    def _build_transcripts_tab(self):
        tab = ttk.Frame(self.notebook)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(1, weight=1)
        ttk.Label(tab, text="grep:").grid(row=0, column=0, sticky="w")
        self.grep_var = tk.StringVar()
        e = ttk.Entry(tab, textvariable=self.grep_var)
        e.grid(row=0, column=1, sticky="ew", padx=4)
        e.bind("<Return>", lambda e: self.cmd_transcripts())
        ttk.Button(tab, text="Transcripts", command=self.cmd_transcripts).grid(
            row=0, column=2, sticky="w")
        self.transcripts_text = tk.Text(
            tab, height=10, wrap="none",
            font=tkfont.nametofont("TkFixedFont", root=self))
        self.transcripts_text.grid(row=1, column=0, columnspan=3,
                                   sticky="nsew")
        self.notebook.add(tab, text="Transcripts")

    # -------------------------------------------------------------- state --
    def set_stage(self, key: str):
        if key not in STAGE_KEYS:
            return
        self._stage = key
        states = self._compute_states()
        self.stage_bar.update(states, key)
        self._update_banner(states)
        for k, panel in self._panels.items():
            if k == key:
                panel.grid(row=0, column=0, sticky="nsew")
            else:
                panel.grid_remove()

    def _update_banner(self, states: dict | None = None):
        """Amber strip: why the stage we're ON needs attention."""
        states = states or self._compute_states()
        st = states.get(self._stage) or {}
        REMEDY = {
            "measure": "rewrite the Xing header before trusting timestamps",
            "identify": "pick a different edition and identify again",
            "align": "use Resolve to place them: Listen, Sweep, or pin by ear",
            "resolve": "use Listen, Sweep, or pin by ear to place them",
            "emit": "run Emit to write the supported chapters",
            "finalize": "run the dry run first",
        }
        # Resolve's own summary line already says what is unresolved
        if st.get("state") == ATTENTION and self._stage != "resolve":
            detail = st.get("detail") or "needs attention"
            self.banner_var.set(f"{STAGE_LABELS[self._stage]}: {detail} — "
                                f"{REMEDY.get(self._stage, '')}")
            self.banner.grid(row=2, column=0, sticky="ew")
        else:
            self.banner_var.set("")
            self.banner.grid_remove()

    def _disk_facts(self) -> DiskFacts:
        """Folder facts from disk (raw listing, never the GUI scan)."""
        d = DiskFacts()
        if not self.audio:
            return d
        folder = os.path.dirname(self.audio)
        exts = (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".wav")
        files = [f for f in os.listdir(folder)
                 if os.path.splitext(f)[1].lower() in exts]
        d.audio_files = files
        if len(files) == 1:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_chapters", "-of", "json",
                 os.path.join(folder, files[0])],
                capture_output=True, text=True)
            try:
                chs = json.loads(probe.stdout).get("chapters", [])
            except ValueError:
                chs = []
            d.embedded_chapters = len(chs)
            if chs:
                d.embedded_last_start = float(chs[-1].get("start_time", 0) or 0)
        emitted = (self.analysis or {}).get("emitted") or {}
        if emitted.get("count"):
            ch_file = os.path.splitext(self.audio)[0] + ".chapters" \
                + os.path.splitext(self.audio)[1]
            if os.path.isfile(ch_file):
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_chapters", "-of", "json",
                     ch_file], capture_output=True, text=True)
                try:
                    chs = json.loads(probe.stdout).get("chapters", [])
                except ValueError:
                    chs = []
                d.chapters_file_match = (
                    len(chs) == emitted.get("count")
                    and chs and abs(float(chs[-1].get("start_time", 0) or 0)
                                    - emitted.get("last", -1)) < 0.1)
        return d

    def _compute_states(self) -> dict:
        states = stage_states(self.analysis,
                              self.min_conf.get() if hasattr(self, "min_conf")
                              else 0.5,
                              self._disk or DiskFacts())
        return states

    def _artifact_mtime(self) -> float | None:
        if not self.audio:
            return None
        try:
            return os.path.getmtime(analysis_path(self.audio, self.work_dir))
        except OSError:
            return None

    @property
    def _finalize_dry_ok(self) -> bool:
        """A dry run succeeded for this book and nothing changed since."""
        return bool(self.audio) and self.audio in self._dry_ok \
            and self._dry_ok[self.audio] == self._artifact_mtime()

    def set_book(self, audio: str):
        if self._cf is None:
            messagebox.showerror(
                "chapterfit", "Cannot find chapterfit.py or an installed "
                "'chapterfit' command.")
            return
        self.audio = os.path.abspath(audio)
        self.work_dir = work_dir_for(self.audio)
        self.book_var.set(self.audio)
        # per-book field provenance: the next row selection fills them again
        self.pin_ch.set("")
        self.pin_time.set("0:00:00.000")
        self.sweep_start.set("")
        self.sweep_end.set("")
        self._pin_autofilled = False
        self._sweep_autofilled = False
        self._reharvest()
        self._disk = self._disk_facts()
        self._refresh()
        self._prefill_identify()
        self.set_stage(next_stage(self._compute_states()))

    def _prefill_identify(self):
        """Show which source the last identify used - without clobbering
        anything the user is typing."""
        label = (self.analysis.get("identification") or {}).get("label") \
            or ""
        current = self.identify_value.get().strip()
        if current and not getattr(self, "_identify_autofilled", False):
            return
        if label.startswith("Audnexus "):
            self.identify_mode.set("asin")
            self.identify_value.set(label.split(" ", 1)[1].strip())
            self._identify_autofilled = True
        elif label:
            if os.path.basename(label) == "chapters.embedded.json":
                self.identify_mode.set("emb")
            elif os.path.isfile(label):
                self.identify_mode.set("json")
            else:
                return
            self.identify_value.set(label)
            self._identify_autofilled = True
        else:
            return
        self._identify_mode_changed()

    def _reharvest(self):
        """Read what the folder has and prefill the Book info fields."""
        if not self.audio:
            return
        self.harvest = harvest_book(self.audio)
        self.found_var.set(found_summary(self.harvest))
        for key, var in self.meta_vars.items():
            var.set(self.harvest["suggested"].get(key, ""))
        self._update_header()

    def _update_header(self):
        """One line: Title — Author · duration · ASIN · elided file name."""
        s = self.harvest.get("suggested") or {}
        title = s.get("title") or "(nothing selected)"
        author = s.get("author") or ""
        dur = ((self.analysis.get("envelope") or {}).get("duration"))
        ident = self.analysis.get("identification") or {}
        asin = ident.get("label") or ident.get("candidate") or ""
        name = os.path.basename(self.audio) if self.audio else ""
        bits = [f"{title} — {author}" if author else title]
        if dur:
            bits.append(hms(dur))
        if asin and asin.lower() != "none":
            bits.append(asin)
        self._header_bits = (bits, name)
        self._fit_header(self._book_label.master.winfo_width())

    def _fit_header(self, width: int):
        """The header stays one line: the file name is elided from the
        middle first, then the title (the full path is in the tooltip)."""
        bits, name = getattr(self, "_header_bits", (["(nothing selected)"],
                                                    ""))
        font = tkfont.nametofont("TkHeadingFont", root=self)
        room = max(width - 8, 200) if width > 1 else 10 ** 6

        def text(bits, name):
            return "  ·  ".join([*bits, name] if name else bits)

        if font.measure(text(bits, name)) > room and name:
            rest = font.measure(text(bits, "")) + font.measure("  ·  ")
            name = _elide(name, font, max(room - rest, font.measure("ab…yz")))
        if font.measure(text(bits, name)) > room:
            rest = font.measure(text(["", *bits[1:]], name))
            bits = [_elide(bits[0], font, max(room - rest, 80)), *bits[1:]]
        self.book_var.set(text(bits, name))

    def _show_path_tip(self, _event=None):
        self._hide_tip()
        if not self.audio:
            return
        x = self.winfo_rootx() + 8
        y = self.winfo_rooty() + self.winfo_height() + 2
        self.head_tooltip = tk.Toplevel(self)
        self.head_tooltip.wm_overrideredirect(True)
        self.head_tooltip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.head_tooltip, text=self.audio, background=palette.TIP,
                 foreground=palette.TEXT, font="TkDefaultFont",
                 relief="solid", borderwidth=1, padx=4, pady=1).pack()

    def _hide_tip(self, _event=None):
        if getattr(self, "head_tooltip", None) is not None:
            self.head_tooltip.destroy()
            self.head_tooltip = None

    def _meta_overrides(self) -> dict:
        """Book info fields -> emit flag values (empty = keep original)."""
        return {"title": self.meta_vars["title"].get().strip(),
                "artist": self.meta_vars["author"].get().strip(),
                "album": self.meta_vars["album"].get().strip(),
                "date": self.meta_vars["year"].get().strip(),
                "genre": self.meta_vars["genre"].get().strip(),
                "comment": self.meta_vars["comment"].get().strip()}

    def _refresh(self):
        """Read the analysis artifact and update every view."""
        self.analysis = {}
        if self.audio:
            self.analysis = load_analysis(self.audio, self.work_dir)

        sel = self.tree.selection()
        scroll = self.tree.yview()
        for i in self.tree.get_children():
            self.tree.delete(i)
        alignment = self.analysis.get("alignment") or {}
        results = list(alignment.get("results", []))
        if not results:
            # before Align: show the identified list, honoring any pins
            # (sweep/listen pins drop alignment until the next align runs)
            pins_now = self.analysis.get("pins") or {}
            results = [
                {"index": i, "title": c.get("title", ""),
                 "published": c.get("start"),
                 "time": pins_now.get(str(i)),
                 "confidence": 1.0 if str(i) in pins_now else None,
                 "pinned": str(i) in pins_now}
                for i, c in enumerate(
                    (self.analysis.get("identification") or {})
                    .get("chapters") or [])]
        self._conflicts_unknown = False
        conflicts = set(alignment.get("conflicts") or [])
        aligned = bool(self.analysis.get("alignment"))
        withheld = select_chapters(results, self.min_conf.get())[1] \
            if aligned else set()
        # artifacts from before conflicts were stored cannot say which pins
        # conflict; say so instead of guessing from the posterior spread
        self._conflicts_unknown = aligned and "conflicts" not in alignment \
            and any(r.get("pinned") for r in results)
        self._row_data = {}
        gap_at = {round(g["mid"], 3): g for g in
                  (self.analysis.get("gaps") or {}).get("gaps") or []
                  if g.get("mid") is not None}
        for r in results:
            n = r["index"] + 1
            mark = f"?{n}" if aligned and n in withheld else n
            tags = []
            pin = "pin" if r.get("pinned") else ""
            if aligned and r.get("pinned") and r["index"] in conflicts:
                tags.append("conflict")
                pin = "! pin"
            elif r.get("time") is None:
                tags.append("unplaced")
            elif aligned and n in withheld:
                tags.append("lowconf")
            g = gap_at.get(round(r["time"], 3)) \
                if r.get("time") is not None else None
            iid = self.tree.insert("", "end", tags=tuple(tags), values=(
                mark, r.get("title", ""), hms(r.get("published")),
                _t(r.get("time")),
                f"{g['dur']:.2f} s" if g and g.get("dur") is not None
                else "",
                "" if r.get("confidence") is None
                else f"{r['confidence']:.2f}",
                pin,
            ))
            self._row_data[iid] = r
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])
        self.tree.yview_moveto(scroll[0] if scroll else 0)

        self.conflict_note.set(
            "pins from an older Align: re-run Align to detect conflicting "
            "pins" if self._conflicts_unknown else "")
        if self._conflicts_unknown:
            self.conflict_label.grid(row=2, column=0, columnspan=2,
                                     sticky="w")
        else:
            self.conflict_label.grid_remove()
        total = len(results)
        weak = sum(1 for r in results if r.get("time") is None
                   or (r.get("confidence") or 0) < self.min_conf.get())
        conf = total - weak
        self.notebook.tab(self._chapters_tab,
                          text=f"Chapters {conf}/{total}")

        self._disk = self._disk_facts()
        states = self._compute_states()
        self.stage_bar.update(states, self._stage)
        self._update_banner(states)
        self._update_panel_results(states)
        self._update_resolve()
        self._update_header()
        self.gap_canvas.after_idle(self._draw_gap_graph)
        self._states = states
        self._update_controls()

    def set_busy(self, working: bool):
        """Called when a job starts or ends: lock conflicting controls."""
        if working != self._busy:
            self._busy = working
            self._update_controls()

    def _job_controls(self) -> list:
        """Everything that starts a job or edits what the next job reads."""
        return [self.measure_btn, self.identify_btn, self.identify_entry,
                self.identify_browse, *self.identify_radios, self.align_btn,
                self.near_entry, self.min_conf_spin, self.hint_entry,
                self.hint_btn, self.listen_btn, self.sweep_start_entry,
                self.sweep_end_entry, self.range_btn, self.probe_btn,
                self.full_btn, self.pin_btn, self.unpin_btn, self.reread_btn,
                self.emit_btn, self.finalize_btn, self.apply_btn]

    def _update_controls(self):
        """One place decides what is enabled, and tooltips say why not."""
        states = getattr(self, "_states", None) or self._compute_states()
        reasons: dict = {}

        def off(widgets, why):
            for w in widgets:
                reasons.setdefault(w, why)

        if self._busy:
            off(self._job_controls(), "a job is running - wait or Cancel job")
        if not self.audio:
            off(self._job_controls() + [self.playfrom_btn],
                "select a book in the library first")
        stage_btns = {"align": [self.align_btn],
                      "resolve": [self.hint_btn, self.listen_btn,
                                  self.range_btn, self.probe_btn,
                                  self.full_btn, self.pin_btn, self.unpin_btn],
                      "emit": [self.emit_btn],
                      "finalize": [self.finalize_btn, self.apply_btn]}
        for key, btns in stage_btns.items():
            if states[key]["state"] == BLOCKED:
                off(btns, f"{STAGE_LABELS[key]} is blocked: "
                          f"{states[key]['why']}")
        if whisper_python() is None:
            off([self.listen_btn, self.range_btn, self.probe_btn,
                 self.full_btn], "no Whisper interpreter found "
                                 "(set WHISPER_PYTHON)")
        if not self._finalize_dry_ok:
            off([self.apply_btn], "run a successful Dry run first")
        mode = self.identify_mode.get()
        if mode == "emb":
            off([self.identify_entry],
                "Embedded uses the chapters already in the file")
        if mode != "json":
            off([self.identify_browse], "Browse picks a chapters JSON file")
        if not self._selected_row() and not self.pin_ch.get().strip():
            off([self.pin_btn, self.unpin_btn],
                "select a chapter in the table")

        for w in {*self._job_controls(), self.playfrom_btn}:
            why = reasons.get(w)
            w.state(["disabled" if why else "!disabled"])
            tip = self._tips.get(w)
            if tip is not None:
                tip.reason = why

    def _update_panel_results(self, states: dict):
        self.measure_result.set(states["measure"]["detail"])
        ident = self.analysis.get("identification") or {}
        score = ((ident.get("best_trial") or {}).get("score")) if ident else None
        if ident:
            self.identify_result.set(
                f"{len(ident.get('chapters') or [])} chapters from "
                f"{ident.get('label') or '?'}"
                + (f" - fit score {score:.1f}" if score is not None else ""))
        else:
            self.identify_result.set("")
        al = self.analysis.get("alignment") or {}
        if al:
            rate = al.get("rate")
            drift = f"{(rate - 1) * 3600:+.1f}s/h" if rate else "?"
            self.align_result.set(
                f"placed {sum(1 for r in al['results'] if r['time'] is not None)}"
                f"/{len(al['results'])} - rate {rate} ({drift})")
        emitted = self.analysis.get("emitted") or {}
        if emitted:
            total = len((self.analysis.get("alignment") or {}).get("results",
                                                                   []))
            self.emit_result.set(
                f"{emitted.get('count')} embedded, "
                f"{total - emitted.get('count', 0)} withheld")

    def _update_resolve(self):
        """Summary line, per-option statuses, and the recommendation."""
        alignment = self.analysis.get("alignment") or {}
        results = alignment.get("results") or []
        thr = self.min_conf.get()
        dur = (self.analysis.get("envelope") or {}).get("duration") or 0.0
        pins = [r["time"] for r in results
                if r.get("pinned") and r.get("time") is not None]

        def listening_value(r):
            # mirrors suggest_measurements: uncertainty x sqrt(leverage)
            if r.get("time") is None:
                lev = dur / 2
            else:
                lev = min((abs(r["time"] - p) for p in pins), default=dur / 2)
            unc = (1 - (r.get("confidence") or 0)) + (r.get("spread")
                                                      or 0) / 60.0
            return unc * math.sqrt(max(lev, 0.0))

        weak = sorted((r for r in results if r["index"] != 0
                       and not r.get("pinned")
                       and (r.get("time") is None
                            or (r.get("confidence") or 0) < thr)),
                      key=listening_value, reverse=True)

        # summary line with clickable chapter numbers
        for w in self.unresolved_frame.winfo_children():
            w.destroy()
        if weak:
            ttk.Label(self.unresolved_frame,
                      text=f"{len(weak)} chapters unresolved:").pack(
                side="left")
            for r in weak:
                n = r["index"] + 1
                lbl = tk.Label(self.unresolved_frame, text=str(n),
                               fg=palette.LINK, cursor="hand2",
                               font=fonts.derived(self, underline=True))
                lbl.pack(side="left", padx=2)
                lbl.bind("<Button-1>",
                         lambda e, r=r: self._select_unresolved(r))
        else:
            ttk.Label(self.unresolved_frame,
                      text="nothing unresolved - spot-check by ear").pack(
                side="left")
        accent = recommend(self.analysis, weak)
        self.next_var.set(f"next: {accent}")

        # statuses from the artifact
        ev = resolve_evidence(self.analysis)
        self.listen_status.set(
            f"heard {ev['listen_heard']}" if ev["listen_heard"]
            else "heard nothing" if ev["listen_ran"] else "not run")
        self.swept_status.set(
            f"{ev['swept_minutes']:.0f} min swept"
            + (f", heard {ev['sweep_heard']}" if ev["sweep_heard"] else "")
            if ev["sweep_spans"] else "not run")
        self.probe_status.set("run" if ev["sweep_spans"] else "not run")
        self.hint_status.set(f"hint: {self.near_var.get()}"
                             if self.near_var.get() else "")

        try:
            s = parse_time(self.sweep_start.get())
            e = parse_time(self.sweep_end.get())
            self.range_cost.set(f"~{(e - s) / 60:.0f} min" if e > s else "")
        except ValueError:
            self.range_cost.set("")
        self.full_cost.set(f"~{dur / 600:.0f} min" if dur else "")

        # exactly one accent button
        chosen = {"Listen": self.listen_btn, "Sweep probe": self.probe_btn,
                  "Sweep range": self.range_btn, "Full sweep": self.full_btn,
                  "By ear": self.pin_btn}
        for name, btn in chosen.items():
            btn.configure(style="Accent.TButton" if name == accent else "TButton")

    def _select_unresolved(self, r):
        for iid, data in self._row_data.items():
            if data.get("index") == r["index"]:
                self.tree.see(iid)
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                break

    # ------------------------------------------------------- command plumbing
    def _argv(self, *extra: str, prefix: list[str] | None = None) -> list[str]:
        assert self._cf is not None and self.audio is not None
        base = self._cf if prefix is None else prefix
        return [*base, "--work-dir", self.work_dir, *extra, self.audio]

    def _run(self, name: str, argv: list[str], then=None, stage: str | None = None,
             on_line=None):
        assert self._cf is not None
        if self.app.runner.busy:
            self.app.log(f"! {name} not started: another job is still running")
            return
        self.app.log(f"$ {' '.join(argv)}")
        self.set_busy(True)
        self.app.runner.run(Job(
            name, argv, on_line=on_line,
            on_done=lambda j, code: self._on_done(
                code, then=then, stage=stage, name=j.name,
                cancelled=getattr(j, "cancelled", False))))

    def _on_done(self, code, then=None, stage: str | None = None,
                 name: str = "the last step", cancelled: bool = False):
        self.set_busy(False)
        if code is None:
            self.app.log(f"! {name} could not start (see the line above)")
        else:
            self.app.log(f"exit {code}")
        self._refresh()
        if cancelled:
            self.app.log(f"! {name} cancelled")
            return
        if code != 0:
            why = "could not start" if code is None else f"exited with {code}"
            messagebox.showwarning(
                "chapterfit", f"{name} {why} - see the Log tab for details",
                parent=self)
            return
        if then:
            then()
            return
        if stage == "finalize-dry":
            self._dry_ok[self.audio] = self._artifact_mtime()
            self._update_controls()
            return
        if stage:
            target = next_stage(self._compute_states())
            if target != self._stage:
                self.set_stage(target)

    # ------------------------------------------------------------- actions --
    def cmd_measure(self):
        self._run("measure", self._argv("measure"), stage="measure")

    # identify ----------------------------------------------------------
    def _identify_mode_changed(self):
        mode = self.identify_mode.get()
        if hasattr(self, "apply_btn"):  # built: enablement is decided centrally
            self._update_controls()
        if mode == "json" and not self.identify_value.get():
            self.identify_value.set(os.path.join(
                os.path.dirname(self.audio or ".") or ".", "chapters.json"))

    def _identify_browse(self):
        path = filedialog.askopenfilename(
            parent=self, title="Chapters JSON",
            filetypes=[("JSON", "*.json"), ("All files", "*")])
        if path:
            self.identify_value.set(path)

    def cmd_identify_primary(self):
        mode = self.identify_mode.get()
        if mode == "asin":
            asin = self.identify_value.get().strip()
            if asin:
                self.cmd_identify_asin(asin)
        elif mode == "json":
            path = self.identify_value.get().strip()
            if path and os.path.isfile(path):
                self.cmd_identify_json(path)
            else:
                messagebox.showinfo("Identify", "choose an existing JSON file",
                                    parent=self)
        else:
            self.cmd_identify_embedded()

    def cmd_identify_asin(self, asin=None):
        asin = asin or simpledialog.askstring("Identify", "Audible ASIN:",
                                              parent=self)
        if asin:
            self._run("identify", [*self._argv("identify"), "--asin",
                                   asin.strip()], stage="identify")

    def cmd_identify_json(self, path=None):
        path = path or filedialog.askopenfilename(
            parent=self, title="Chapters JSON",
            filetypes=[("JSON", "*.json"), ("All files", "*")])
        if path:
            self._run("identify", [*self._argv("identify"),
                                   "--chapters-json", path],
                      stage="identify")

    def cmd_identify_embedded(self):
        """Use the chapters already embedded in the file as the source."""
        chapters = self.harvest.get("embedded_chapters") or []
        if len(chapters) < 2:
            messagebox.showinfo("Identify (embedded)",
                                "the file has fewer than 2 embedded chapters",
                                parent=self)
            return
        assert self.work_dir is not None
        dump = os.path.join(self.work_dir, "chapters.embedded.json")
        os.makedirs(self.work_dir, exist_ok=True)
        with open(dump, "w") as f:
            json.dump({"chapters": [
                {"startOffsetMs": int(round(c["start"] * 1000)),
                 "title": c["title"]} for c in chapters]}, f, indent=1)
        self.app.log(f"  dumped {len(chapters)} embedded chapters -> {dump}")
        self._run("identify", [*self._argv("identify"), "--chapters-json",
                               dump], stage="identify")

    # align -------------------------------------------------------------
    def _near_spec(self, raw: str) -> str | None:
        """Normalize the position hint: `CH=TIME` passes through (time
        normalized); a bare TIME maps to the chapter whose placed time
        (or published, pre-align) is nearest."""
        raw = raw.strip()
        if not raw:
            return None
        if "=" in raw:
            ch, _, t = raw.partition("=")
            try:
                n = int(ch)
                seconds = parse_time(t)
            except ValueError:
                raise ValueError(
                    f"hint wants CH=TIME (e.g. 3=43:47) or just a time, "
                    f"got {raw!r}") from None
            return f"{n}={clock(seconds)}"
        seconds = parse_time(raw)  # raises ValueError for garbage
        rows = list((self.analysis.get("alignment") or {}).get("results")
                    or [])
        by_placed = [r for r in rows if r.get("time") is not None]
        if by_placed:
            best = min(by_placed, key=lambda r: abs(r["time"] - seconds))
            return f"{best['index'] + 1}={clock(seconds)}"
        ident = (self.analysis.get("identification") or {}).get("chapters") \
            or []
        if not ident:
            raise ValueError("run Identify first - no chapter list to map "
                             "the time to")
        best = min(ident, key=lambda c: abs(c["start"] - seconds))
        idx = ident.index(best) if ident else 0
        return f"{idx + 1}={clock(seconds)}"

    def cmd_align(self):
        argv = self._argv("align", "--min-confidence",
                          str(self.min_conf.get()))
        near = self.near_var.get().strip()
        if near:
            try:
                spec = self._near_spec(near)
            except ValueError as e:
                messagebox.showerror("Align", str(e), parent=self)
                return
            if spec:
                argv += ["--near", spec]
                self.hint_status.set(f"hint: {spec}")
        self._run("align", argv, stage="align")

    # resolve -----------------------------------------------------------
    def _resolve_hint(self):
        near = self.hint_var.get().strip()
        if not near:
            near = simpledialog.askstring(
                "Position hint",
                "roughly where a chapter starts: CH=TIME, or just a time",
                parent=self)
        if not near:
            return
        try:
            spec = self._near_spec(near)
        except ValueError as e:
            messagebox.showerror("Position hint", str(e), parent=self)
            return
        if spec:
            self.near_var.set(spec)
            self.cmd_align()

    def _sweep_probe(self):
        dur = ((self.analysis.get("envelope") or {}).get("duration"))
        if not dur:
            messagebox.showinfo("Sweep", "run Measure first", parent=self)
            return
        start = dur * 0.25
        self.cmd_sweep(start=start, end=start + 600)

    def _sweep_range(self):
        try:
            s = parse_time(self.sweep_start.get().strip() or "0")
            e = parse_time(self.sweep_end.get().strip() or "0")
        except ValueError:
            messagebox.showinfo("Sweep range",
                                "range needs S–E times like 0:44:54",
                                parent=self)
            return
        if e <= s:
            messagebox.showinfo("Sweep range", "end must be after start",
                                parent=self)
            return
        self.cmd_sweep(start=s, end=e)

    def _sweep_full(self):
        self.cmd_sweep()

    def cmd_sweep(self, start=None, end=None):
        wpy = whisper_python()
        if wpy is None:
            messagebox.showinfo("Sweep", "no whisper venv found (set "
                                "WHISPER_PYTHON)", parent=self)
            return
        script = self._whisper_script()
        if script is None:
            return
        argv = [*self._argv("sweep", prefix=[wpy, "-u", script]),
                "--model", os.environ.get("WHISPER_MODEL", "base"),
                "--pin", "-y"]
        if start is not None:
            argv += ["--start", clock(start)]
        if end is not None:
            argv += ["--end", clock(end)]
        self._run("sweep", argv, stage="resolve", then=self.cmd_align)

    def cmd_listen(self):
        wpy = whisper_python()
        if wpy is None:
            messagebox.showinfo("Listen", "no whisper venv found (set "
                                "WHISPER_PYTHON)", parent=self)
            return
        script = self._whisper_script()
        if script is None:
            return
        self._run("listen", [*self._argv("listen", prefix=[wpy, "-u", script]),
                             "--model", os.environ.get("WHISPER_MODEL", "base"),
                             "--pin", "-y"], stage="resolve",
                  then=self.cmd_align)

    def _whisper_script(self):
        # the venv python must run the chapterfit *script* directly
        # (chapterbook.sh: WCF=("$WPY" "$CHAPTERFIT"))
        script = next((a for a in (self._cf or [])[1:] if a.endswith(".py")),
                      None)
        if script is None:
            messagebox.showerror(
                "whisper", "whisper actions need the chapterfit.py script "
                "(an installed console script cannot be re-run under the "
                "whisper venv)", parent=self)
        return script

    def cmd_transcripts(self):
        argv = self._argv("transcripts")
        grep = self.grep_var.get().strip()
        if grep:
            argv += ["--grep", grep]
        self.transcripts_text.delete("1.0", "end")
        self._run("transcripts", argv, on_line=self._transcript_line)

    def _transcript_line(self, line: str):
        self.transcripts_text.insert("end", line + "\n")
        self.transcripts_text.see("end")

    # pin by ear ----------------------------------------------------------
    def _selected_row(self):
        sel = self.tree.selection()
        if not sel:
            return None
        r = self._row_data.get(sel[0])
        if r is not None:
            return dict(r, _iid=sel[0])
        vals = self.tree.item(sel[0], "values")
        return {"index": int(str(vals[0]).lstrip("?")) - 1, "title": vals[1],
                "published": vals[2], "time": vals[3],
                "confidence": vals[5], "pinned": vals[6], "_iid": None}

    def preview_row(self):
        row = self._selected_row()
        if not row or not self.audio:
            return
        start = None
        for key in ("time", "published"):
            v = row.get(key)
            if v is not None and str(v) not in ("", "-"):
                start = max(0.0, parse_time(str(v)) - 3.0)
                break
        self.app.play(self.audio, start=start, length=0)  # never self-stops

    def play_from_time(self):
        """Start uncapped playback at the time in the field."""
        if not self.audio:
            return
        try:
            seconds = parse_time(self.pin_time.get().strip())
        except ValueError:
            messagebox.showerror("Play from time",
                                 f"cannot read time: {self.pin_time.get()}",
                                 parent=self)
            return
        self.app.play(self.audio, start=max(0.0, seconds), length=0)
        self._sync_play_btn()

    def _key_playpause(self, _event=None):
        if self.app.player.alive:
            self.toggle_pause()
        else:
            self.preview_row()
        self._sync_play_btn()
        return "break"

    def _key_seek(self, delta):
        if self.app.player.alive:
            self.app.player.seek(delta)
        return "break"

    def _sync_play_btn(self):
        self.play_btn.configure(
            text="⏸ Pause" if self.app.player.alive
            and not self.app.player.paused else "▶ Play")

    def _fill_from_selection(self, _event=None):
        """Selecting a row fills chapter/time/sweep fields; manual edits in
        the chapter and time fields are never clobbered."""
        row = self._selected_row()
        if not row:
            return
        self._update_byear(row)
        if not self.pin_ch.get().strip() or self._pin_autofilled:
            self.pin_ch.set(str(row["index"] + 1))
            self.pin_time.set(_t(row.get("time") or row.get("published")))
            self._pin_autofilled = True
        self.play_btn.state(["!disabled"])
        if not self.sweep_start.get().strip() or self._sweep_autofilled:
            self._prefill_sweep(row)
        self._update_controls()

    def _sweep_edited(self, event):
        if event.char and event.char.isprintable() \
                or event.keysym in ("BackSpace", "Delete"):
            self._sweep_autofilled = False

    def _update_byear(self, row):
        name = row.get("title") or f"chapter {row['index'] + 1}"
        self.byear_title.set(f"{name}  (#{row['index'] + 1})")
        conf = row.get("confidence")
        conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) \
            else (conf or "–")
        self.byear_info.set(f"placed {_t(row.get('time'))}   "
                            f"conf {conf_s}")

    def _prefill_sweep(self, row):
        """Estimate listening spots: ±5 min around the chapter's time."""
        v = row.get("time") or row.get("published")
        if v is None or str(v) in ("", "-"):
            return
        try:
            seconds = parse_time(str(v))
        except ValueError:
            return
        dur = (self.analysis.get("envelope") or {}).get("duration") or 0.0
        self.sweep_start.set(clock(max(0.0, seconds - 300)))
        self.sweep_end.set(clock(min(dur, seconds + 300) or seconds + 300))
        self._sweep_autofilled = True
        span = (min(dur, seconds + 300) if dur else seconds + 300) \
            - max(0.0, seconds - 300)
        self.range_cost.set(f"~{span / 60:.0f} min")

    def toggle_pause(self):
        self.app.player.toggle_pause()
        self._sync_play_btn()

    def set_from_playback(self):
        t = self.app.current_playback()
        if t is None:
            return
        self.pin_time.set(_live(t))
        if not self.pin_ch.get().strip():
            row = self._selected_row()
            if row:
                self.pin_ch.set(str(row["index"] + 1))
            self._pin_autofilled = True

    def update_playback(self, t):
        """Called ~10x/s from the app pump with the live playback time."""
        self.playback_var.set(_t(t) if t is not None else "0:00:00.000")
        active = t is not None
        for b in (*self.seek_btns, self._set_btn, self.stop_btn):
            b.state(["!disabled" if active else "disabled"])
        self._sync_play_btn()

    # pin/unpin -------------------------------------------------------------
    def cmd_pin(self):
        ch = self.pin_ch.get().strip()
        t = self.pin_time.get().strip()
        if not ch or not t:
            messagebox.showinfo("Pin", "enter a chapter and a time", parent=self)
            return
        try:
            seconds = parse_time(t)
        except ValueError:
            messagebox.showerror("Pin", f"cannot read time: {t}", parent=self)
            return
        self._run("pin", [*self._argv("pin"),
                          "--at", f"{ch}={clock(seconds)}"],
                  then=self.cmd_align, stage="resolve")

    def cmd_unpin(self):
        ch = self.pin_ch.get().strip()
        if not ch:
            row = self._selected_row()
            ch = str(row["index"] + 1) if row else ""
        if not ch:
            messagebox.showinfo(
                "Remove pin", "select a chapter (or type its number) first",
                parent=self)
            return
        try:
            idx = int(ch) - 1
        except ValueError:
            messagebox.showerror("Remove pin",
                                 f"{ch!r} is not a chapter number",
                                 parent=self)
            return
        if self.audio and str(idx) not in (self.analysis.get("pins") or {}):
            messagebox.showinfo("Remove pin",
                                f"chapter {ch} has no pin", parent=self)
            return
        self._run("pin", [*self._argv("pin"), "--remove", str(idx + 1)],
                  then=self.cmd_align, stage="resolve")

    # emit / finalize -----------------------------------------------------
    def cmd_emit(self):
        assert self.work_dir is not None
        argv = self._argv("emit",
                          "--min-confidence", str(self.min_conf.get()),
                          "--chapters-txt", os.path.join(self.work_dir, "chapters.txt"),
                          "--abs-json", os.path.join(self.work_dir, "chapters.json"))
        flags = {"--title": "title", "--author": "artist", "--album": "album",
                 "--year": "date", "--genre": "genre", "--comment": "comment"}
        for flag, key in flags.items():
            v = self._meta_overrides().get(key)
            if v:
                argv += [flag, v]
        if self.embed_var.get():
            argv.append("--embed")
        self._run("emit", argv, stage="emit")

    def cmd_finalize_dry(self):
        self._run("finalize", self._argv("finalize"), stage="finalize-dry")

    def cmd_finalize_apply(self):
        if not messagebox.askyesno(
                "Finalize", "Move the original aside and reload the book in "
                "Audiobookshelf?", parent=self):
            return
        argv = self._argv("finalize", "--apply", "-y")
        if self.allow_delete_var.get():
            argv.append("--allow-delete")
        self._run("finalize", argv, stage="finalize")
