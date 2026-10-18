"""Chapter table and similar-gap ranking. The timeline draws the marks."""
from __future__ import annotations

import statistics
import tkinter as tk
from tkinter import ttk

from . import fonts, palette
from .theme import ctk_frame, follow_theme, require_customtkinter

ctk = require_customtkinter()

CADENCE_WINDOW = 300.0

CHAPTER_COLUMNS = (
    ("index", "#", "?999", "e"),
    ("title", "Title", None, "w"),
    ("published", "Published", "12:34:56.789", "e"),
    ("time", "Placed", "12:34:56.789", "e"),
    ("gap", "Gap", "12.34 s", "e"),
    ("confidence", "Conf", "0.00", "e"),
    ("pinned", "Pin", "! pin", "center"),
    ("heard", "Heard", None, "w"),
    ("status", "Status", "unplaced", "w"),
)
CELL_PAD = 24
SHORTCUTS = ("Space play/pause · ←/→ ±5 s · Shift+←/→ ±10 s · "
             "Enter opens a problem · Delete removes a pin")
TITLE_MIN = "An Average Chapter Title"


def similar_gaps(gaps: list[dict], pins: dict[int, float],
                 chapters: list[dict], mode: str,
                 limit: int = 10) -> list[dict]:
    """Gaps most similar to the pinned chapters (cadence or duration)."""
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


def _gap_at(gaps: list[dict], t: float, tol: float = 0.06):
    best, bd = None, 1e9
    for g in gaps:
        d = abs(g["mid"] - t)
        if d < bd:
            best, bd = g, d
    return best if bd <= tol else None


def gap_marks(gaps: list[dict], rows, mode: str = "cadence",
              limit: int = 10) -> tuple[list[dict], list[dict]]:
    """Placed-chapter bars, plus extra candidate gaps.

    With nothing placed, the extras are the longest silences. Otherwise
    they are the gaps `similar_gaps` ranks from those placed times, so a
    chapter that already sits on a silence is not listed again.
    """
    if not gaps:
        return [], []
    rows = list(rows)
    bars = []
    for r in rows:
        if r.placed is None:
            continue
        bars.append({"time": r.placed, "title": r.title,
                     "n": r.index + 1,
                     "gap": _gap_at(gaps, r.placed)})
    if not bars:
        ranked = sorted(gaps, key=lambda g: g.get("dur") or 0, reverse=True)
        extras = [{"gap": g, "pred": None, "chapter_title": "", "ref": None}
                  for g in ranked[:limit]]
        return [], extras
    n = max(r.index for r in rows) + 1
    chapters = [{"title": "", "start": 0.0} for _ in range(n)]
    pins: dict[int, float] = {}
    for r in rows:
        if r.published is not None:
            start = r.published
        elif r.placed is not None:
            start = r.placed
        else:
            start = 0.0
        chapters[r.index] = {"title": r.title, "start": start}
        if r.placed is not None:
            pins[r.index] = r.placed
    return bars, similar_gaps(gaps, pins, chapters, mode, limit=limit)


class TableViews:
    """Chapters tab. Mixed into WorkflowPanel."""

    def size_table(self):
        # The chapter table uses the same 14 px body as every other table.
        # The old "+2 pt" chapter-table size is cancelled.
        from .ui_kit import SMALL, tk_font
        row_font = tk_font(self)
        head_font = tk_font(self, SMALL, "bold")
        self.table_font = row_font
        self._chapter_widths = {}
        for col, heading, sample, _anchor in CHAPTER_COLUMNS:
            text = TITLE_MIN if col == "title" else (sample or heading)
            need = max(head_font.measure(heading),
                       row_font.measure(text)) + CELL_PAD
            self.tree.column(col, minwidth=need, width=need)
            self._chapter_widths[col] = need

    def _paint_row_tags(self) -> None:
        self.tree.tag_configure(
            "unplaced", background=palette.current("ROW_UNPLACED"))
        self.tree.tag_configure(
            "lowconf", background=palette.current("ROW_LOWCONF"))
        self.tree.tag_configure(
            "conflict", background=palette.current("ROW_CONFLICT"))

    def _build_chapters_tab(self, parent):
        tab = ctk_frame(parent, fg_color="transparent", corner_radius=0)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        cols = [c[0] for c in CHAPTER_COLUMNS]
        self.tree = ttk.Treeview(tab, columns=cols, show="headings",
                                 style="Chapter.Treeview")
        for col, heading, _sample, anchor in CHAPTER_COLUMNS:
            self.tree.heading(col, text=heading, anchor=anchor)
            self.tree.column(col, anchor=anchor, stretch=col == "title")
        self.size_table()
        self._paint_row_tags()
        follow_theme(self.tree, self._paint_row_tags)
        self.tree.grid(row=0, column=0, sticky="nsew")
        from .ui_kit import vscroll
        vscroll(tab, self.tree, row=0, column=1, sticky="ns")
        self.tree.bind("<Double-1>", lambda e: self.preview_row())
        self.tree.bind("<<TreeviewSelect>>", self._fill_from_selection)
        for seq, fn in (("space", self._key_playpause),
                        ("Left", lambda e: self._key_seek(-5)),
                        ("Right", lambda e: self._key_seek(5)),
                        ("Shift-Left", lambda e: self._key_seek(-10)),
                        ("Shift-Right", lambda e: self._key_seek(10)),
                        ("Return", self._toggle_open_row),
                        ("KP_Enter", self._toggle_open_row),
                        ("Delete", lambda e: self.cmd_unpin())):
            self.tree.bind(seq, fn)
        foot = ctk_frame(tab, fg_color="transparent")
        foot.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        foot.columnconfigure(0, weight=1)
        legend = ctk_frame(foot, fg_color="transparent")
        legend.grid(row=0, column=0, sticky="w")
        self.chapter_filter = tk.StringVar(value="all")
        self._filter_labels = {
            "all": "All chapters",
            "problems": "Problems",
            "lowconf": "Low confidence",
            "unplaced": "Unplaced",
        }
        show = ctk_frame(legend, fg_color="transparent")
        show.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(show, text="Show", text_color=palette.TEXT).pack(side="left")
        self.filter_box = ttk.Combobox(
            show, width=16, state="readonly",
            values=tuple(self._filter_labels.values()))
        self.filter_box.set(self._filter_labels["all"])
        self.filter_box.pack(side="left", padx=(6, 0))

        def _pick_filter(_e=None):
            label = self.filter_box.get()
            for key, text in self._filter_labels.items():
                if text == label:
                    self.chapter_filter.set(key)
                    break
            self._refresh()

        self.filter_box.bind("<<ComboboxSelected>>", _pick_filter)
        chips = ctk_frame(legend, fg_color="transparent")
        chips.pack(side="left")
        for color, text in ((palette.ROW_UNPLACED, "Unplaced"),
                            (palette.ROW_LOWCONF, "Withheld"),
                            (palette.ROW_CONFLICT, "Pin conflict")):
            ctk.CTkFrame(chips, width=16, height=14, fg_color=color,
                         corner_radius=2, border_width=1,
                         border_color=palette.BORDER).pack(
                side="left", padx=(0, 4))
            ctk.CTkLabel(chips, text=text, height=16,
                         text_color=palette.MUTED).pack(
                side="left", padx=(0, 12))
        ctk.CTkLabel(
            chips, text="? = withheld, not written to the file",
            height=16, text_color=palette.MUTED).pack(side="left")
        from .ui_kit import text_button
        from . import tips
        hint = text_button(foot, "?", width=28)
        hint.grid(row=0, column=1, sticky="e")
        tips.tooltip(hint, SHORTCUTS)
        self.shortcut_label = hint
        self._table_legend = legend
        self.conflict_note = tk.StringVar(value="")
        self.conflict_label = ctk.CTkLabel(
            tab, textvariable=self.conflict_note, text_color=palette.WARN,
            anchor="w", text="")
        self.problem_bar = ctk_frame(tab, fg_color="transparent")
        self.problem_bar.grid(row=2, column=0, columnspan=2, sticky="ew",
                              pady=(4, 0))
        self.problem_bar.grid_remove()
        tab.grid(row=0, column=0, sticky="nsew")
        self._chapters_tab = tab
