"""Chapter table and similar-gap ranking. The timeline draws the marks."""
from __future__ import annotations

import statistics
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from . import fonts, palette

CADENCE_WINDOW = 300.0

CHAPTER_COLUMNS = (
    ("index", "#", "?999", "e"),
    ("title", "Title", None, "w"),
    ("published", "Published", "12:34:56.789", "e"),
    ("time", "Placed", "12:34:56.789", "e"),
    ("gap", "Gap", "12.34 s", "e"),
    ("confidence", "Conf", "0.00", "e"),
    ("pinned", "Pin", "! pin", "center"),
)
CELL_PAD = 24
SHORTCUTS = ("Space play/pause · ←/→ ±5 s · Shift+←/→ ±10 s · "
             "Enter pin · Delete remove pin")
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

    def _build_chapters_tab(self, parent):
        tab = ttk.Frame(parent)
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
        tab.grid(row=0, column=0, sticky="nsew")
        self._chapters_tab = tab

    def _layout_foot(self, event=None):
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
