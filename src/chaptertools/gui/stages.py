"""Stage state machine and the clickable stage bar.

The state machine is a pure function over the analysis artifact plus a
description of the folder on disk, so it can be tested without Tk or
subprocesses.
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk

DONE, CURRENT, ATTENTION, PENDING, BLOCKED = \
    "done", "current", "attention", "pending", "blocked"

GLYPH = {DONE: "✓", CURRENT: "●", ATTENTION: "!", PENDING: "○", BLOCKED: "✕"}
STAGE_KEYS = ("measure", "identify", "align", "resolve", "emit", "finalize")
STAGE_LABELS = {
    "measure": "Measure", "identify": "Identify", "align": "Align",
    "resolve": "Resolve", "emit": "Emit", "finalize": "Finalize",
}


@dataclass
class DiskFacts:
    """What the book folder looks like on disk (never the GUI's filtered
    view): raw audio files by extension, embedded chapter count of the
    single file when there is exactly one, and whether the emitted
    chapter file / final file matches what the artifact says it wrote."""
    audio_files: list[str] = field(default_factory=list)
    embedded_chapters: int = 0
    embedded_last_start: float | None = None
    chapters_file_match: bool = False


def _weak_count(results: list[dict], min_conf: float) -> int:
    return sum(1 for r in results
               if r.get("time") is None
               or (r.get("confidence") or 0) < min_conf)


def stage_states(analysis: dict, min_conf: float, disk: DiskFacts,
                 emit_stale: bool = False) -> dict[str, dict]:
    """State of every stage: {key: {state, detail, blocked, why}}.

    emit_stale is True when a pin/align job ran since the last successful
    emit in this session - the artifact cannot show that by itself.
    """
    out = {k: {"state": PENDING, "detail": "", "blocked": False, "why": ""}
           for k in STAGE_KEYS}

    measured = all(analysis.get(k) for k in
                   ("envelope", "calibration", "gaps", "fingerprint"))
    src = analysis.get("source") or {}
    out["measure"] = {
        "state": DONE if measured else PENDING,
        "detail": (f"{len((analysis.get('gaps') or {}).get('gaps', []))} gaps"
                   if measured else ""),
        "blocked": False, "why": "",
    }
    if measured and src and not src.get("timeline_trustworthy", True):
        out["measure"]["state"] = ATTENTION
        out["measure"]["detail"] += " (header wrong - rewrite the Xing header)"

    ident = analysis.get("identification") or {}
    # the user picked the source (ASIN/JSON/embedded) - the identification
    # stage is done the moment it runs; fit quality is Align's warning
    out["identify"] = {
        "state": DONE if ident else PENDING,
        "detail": f"{len(ident.get('chapters') or [])} chapters" if ident
        else "",
        "blocked": False, "why": "",
    }

    al = analysis.get("alignment") or {}
    results = al.get("results") or []
    weak = _weak_count(results, min_conf) if results else 0
    if not ident or not al:
        out["align"] = {"state": PENDING, "detail": "", "blocked": False,
                        "why": ""}
    else:
        score = (ident.get("best_trial") or {}).get("score")
        notes = [f"weak: {weak}"]
        if score is not None and score < 5:
            notes.append(f"edition fit {'rejected' if score < 2 else 'weak'}"
                         f" - score {score:.1f}")
        out["align"] = {"state": ATTENTION if weak or (score or 5) < 5
                        else DONE,
                        "detail": ", ".join(notes), "blocked": False,
                        "why": ""}

    if ident:
        out["resolve"] = {
            "state": DONE if (al and weak == 0) else ATTENTION if al
            else PENDING,
            "detail": f"weak: {weak}" if al else "",
            "blocked": False, "why": ""}

    emitted = analysis.get("emitted") or {}
    one_file = len(disk.audio_files) == 1
    final_match = (one_file
                   and disk.embedded_chapters == emitted.get("count")
                   and emitted.get("last") is not None
                   and disk.embedded_last_start is not None
                   and abs(disk.embedded_last_start - emitted["last"]) < 0.1)
    emit_done = bool(emitted) and (disk.chapters_file_match or final_match)
    # dependency rule: nothing is done while a stage it depends on is pending
    align_ok = ident and bool(al)
    emit_done = emit_done and align_ok
    out["emit"] = {
        "state": PENDING if (emit_stale or not emit_done) else DONE,
        "detail": f"{emitted.get('count', 0)} written"
                  if emitted and align_ok else "",
        "blocked": not align_ok,
        "why": "" if align_ok else "identify and align first",
    }

    fin_ok = (emit_done and one_file and disk.embedded_chapters >= 2)
    out["finalize"] = {
        "state": DONE if fin_ok else PENDING,
        "detail": "folder ready" if fin_ok else "",
        "blocked": not emit_done,
        "why": "" if emit_done else "emit first",
    }
    return out


def first_pending(states: dict[str, dict]) -> str:
    for k in STAGE_KEYS:
        if states[k]["state"] != DONE:
            return k
    return "finalize"


class StageBar(ttk.Frame):
    """Clickable row of six stages with state glyphs."""

    def __init__(self, master, on_pick):
        super().__init__(master)
        self.on_pick = on_pick
        self._labels: dict[str, tk.Label] = {}
        self._states: dict[str, dict] = {}
        self._selected: str | None = None
        self._tip = None
        for n, key in enumerate(STAGE_KEYS):
            lbl = tk.Label(self, text=STAGE_LABELS[key], cursor="hand2",
                           padx=6, pady=2)
            lbl.grid(row=0, column=n * 2, sticky="w")
            lbl.bind("<Button-1>", lambda e, k=key: self.on_pick(k))
            lbl.bind("<Enter>", lambda e, k=key: self._show_tip(k))
            lbl.bind("<Leave>", lambda e: self._hide_tip())
            self._labels[key] = lbl
            if n:
                ttk.Separator(self, orient="vertical").grid(
                    row=0, column=n * 2 - 1, padx=2)

    def update(self, states: dict[str, dict], selected: str):
        self._states = states
        self._selected = selected
        bold = _font(self, weight="bold")
        normal = _font(self)
        for key in STAGE_KEYS:
            st = states[key]["state"]
            lbl = self._labels[key]
            glyph = GLYPH.get(st, "○")
            text = f"● {STAGE_LABELS[key]}" if key == selected \
                else f"{glyph} {STAGE_LABELS[key]}"
            lbl.configure(text=text,
                          font=bold if key == selected else normal)
            lbl.configure(fg={DONE: "#2e7d32", ATTENTION: "#b26a00",
                              BLOCKED: "#9e9e9e"}.get(st, "#1a1a1a"))

    # tooltips for blocked stages
    def _show_tip(self, key):
        self._hide_tip()
        st = self._states.get(key) or {}
        if st.get("state") != BLOCKED and not st.get("why"):
            return
        x = self.winfo_rootx() + self._labels[key].winfo_x()
        y = self.winfo_rooty() + self.winfo_height() + 2
        self._tip = tk.Toplevel(self)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self._tip, text=st.get("why") or "click to open",
                 background="#ffffe0", relief="solid", borderwidth=1,
                 padx=4, pady=1).pack()

    def _hide_tip(self):
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def _font(widget, **kw):
    from .fonts import derived
    return derived(widget, **kw)
