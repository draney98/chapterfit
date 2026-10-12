"""Stage state machine and the clickable stage bar.

The state machine is a pure function over the analysis artifact plus a
description of the folder on disk, so it can be tested without Tk or
subprocesses.
"""
from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from tkinter import ttk

from chaptertools.envelope.emit import alignment_fingerprint

from . import palette

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


def _blocked(why: str) -> dict:
    return {"state": BLOCKED, "detail": "", "blocked": True, "why": why}


def emit_is_stale(analysis: dict) -> bool:
    """The alignment changed (re-align, pins) since the last emit."""
    emitted = analysis.get("emitted") or {}
    sig = emitted.get("alignment_sig")
    if not sig:
        return False  # emitted before signatures existed: cannot tell
    return sig != alignment_fingerprint(analysis.get("alignment"))


def identify_verdict(ident: dict) -> str | None:
    """The CLI's identify verdict: consistent / weak / rejected (None
    before Identify has run)."""
    if not ident:
        return None
    best = ident.get("best_trial")
    if not best:
        return "rejected"
    score = best.get("score") or 0.0
    spread = best.get("rate_spread") or 0.0
    if score >= 5 and spread < 0.02:
        return "consistent"
    return "weak" if score >= 2 else "rejected"


def stage_states(analysis: dict, min_conf: float, disk: DiskFacts,
                 emit_stale: bool = False) -> dict[str, dict]:
    """State of every stage: {key: {state, detail, blocked, why}}.

    Dependencies: align needs identify, resolve and emit need align,
    finalize needs emit. A stage whose dependency is not done is BLOCKED.
    emit_stale forces Emit back to pending; the artifact's own emit
    signature is checked as well.
    """
    out = {k: {"state": PENDING, "detail": "", "blocked": False, "why": ""}
           for k in STAGE_KEYS}

    measured = all(analysis.get(k) for k in
                   ("envelope", "calibration", "gaps", "fingerprint"))
    src = analysis.get("source") or {}
    out["measure"] = {
        "state": DONE if measured else PENDING,
        "detail": (f"{len((analysis.get('gaps') or {}).get('gaps', [])):,} gaps"
                   if measured else ""),
        "blocked": False, "why": "",
    }
    if measured and src and not src.get("timeline_trustworthy", True):
        out["measure"]["state"] = ATTENTION
        out["measure"]["detail"] = "header timing is wrong"

    ident = analysis.get("identification") or {}
    # the user picked the source (ASIN/JSON/embedded) - the identification
    # stage is done the moment it runs; fit quality is Align's warning
    out["identify"] = {
        "state": DONE if ident else PENDING,
        "detail": (
            f"{len(ident.get('chapters') or [])} chapters"
            + (f" · {str(ident.get('label') or '').removeprefix('Audnexus ').strip()}"
               if ident.get("label") else "")
            if ident else "pick an edition"),
        "blocked": False, "why": "",
    }

    al = analysis.get("alignment") or {}
    results = al.get("results") or []
    weak = _weak_count(results, min_conf) if results else 0
    placed = sum(1 for r in results if r.get("time") is not None)
    place_detail = f"{placed} placed · {weak} to check" if results else ""
    if not ident:
        out["align"] = _blocked("identify first")
    elif not al:
        out["align"] = {"state": PENDING, "detail": "", "blocked": False,
                        "why": ""}
    else:
        score = (ident.get("best_trial") or {}).get("score")
        notes = [place_detail]
        if score is not None and score < 5:
            notes.append(f"edition fit {'rejected' if score < 2 else 'weak'}"
                         f" - score {score:.1f}")
        out["align"] = {"state": ATTENTION if weak or (score or 5) < 5
                        else DONE,
                        "detail": ", ".join(n for n in notes if n),
                        "blocked": False, "why": ""}

    align_ok = bool(ident) and bool(al)
    if align_ok:
        out["resolve"] = {
            "state": DONE if weak == 0 else ATTENTION,
            "detail": place_detail,
            "blocked": False, "why": ""}
    else:
        out["resolve"] = _blocked("align first")

    emitted = analysis.get("emitted") or {}
    one_file = len(disk.audio_files) == 1
    final_match = (one_file
                   and disk.embedded_chapters == emitted.get("count")
                   and emitted.get("last") is not None
                   and disk.embedded_last_start is not None
                   and abs(disk.embedded_last_start - emitted["last"]) < 0.1)
    stale = emit_stale or emit_is_stale(analysis)
    emit_done = (align_ok and bool(emitted) and not stale
                 and (disk.chapters_file_match or final_match))
    if not align_ok:
        out["emit"] = _blocked("identify and align first")
    else:
        out["emit"] = {
            "state": DONE if emit_done else PENDING,
            "detail": (f"{emitted.get('count', 0)} written"
                       + (" - alignment changed since" if stale else ""))
                      if emitted else "",
            "blocked": False, "why": ""}

    if not align_ok:
        out["finalize"] = _blocked("needs Place")
    elif not emit_done:
        # Chapters are placed; a dry run / apply still needed. Not blocked.
        out["finalize"] = {
            "state": PENDING, "detail": "needs a dry run",
            "blocked": False, "why": ""}
    else:
        fin_ok = one_file and disk.embedded_chapters >= 2
        out["finalize"] = {
            "state": DONE if fin_ok else PENDING,
            "detail": "applied" if fin_ok else "ready to apply",
            "blocked": False, "why": ""}
    return out


def next_stage(states: dict[str, dict]) -> str:
    """The stage to open: the first one not done. Align is passed once
    alignment exists - its warning is shown, but the remedy (placing
    weak chapters) lives in Resolve."""
    for k in STAGE_KEYS:
        st = states[k]["state"]
        if st == DONE:
            continue
        if k == "align" and states["resolve"]["state"] != BLOCKED:
            continue
        return k
    return "finalize"


def _clip_spans(clips: dict, sweep: bool) -> list[tuple[float, float]]:
    """(start, end) of cached transcription clips, sweep or listen."""
    spans = []
    for key in clips:
        if ("sweep" in key) != sweep:
            continue
        try:
            start, length = (float(x) for x in key.split("|")[:2])
        except ValueError:
            continue
        spans.append((start, start + length))
    return spans


def resolve_evidence(analysis: dict) -> dict:
    """What escalation has already been tried, from the artifact."""
    heard = analysis.get("heard") or {}
    positions = heard.get("positions") or []
    clips = heard.get("clips") or {}
    sweeps = _clip_spans(clips, sweep=True)
    return {
        "listen_ran": bool(_clip_spans(clips, sweep=False))
        or any(p.get("source") != "sweep" for p in positions),
        "listen_heard": sum(1 for p in positions
                            if p.get("heading") and p.get("source") != "sweep"),
        "sweep_spans": sweeps,
        "swept_minutes": sum(e - s for s, e in sweeps) / 60.0,
        "sweep_heard": sum(1 for p in positions if p.get("source") == "sweep"),
    }


def _chapter_time(r: dict) -> float | None:
    t = r.get("time")
    return t if t is not None else r.get("published")


def recommend(analysis: dict, weak: list[dict]) -> str:
    """The escalation ladder: which Resolve action to try next.

    Listen first (cheap). If listen heard nothing, probe with a sweep; if
    it heard announcements, sweep a range around a stubborn chapter. Once
    sweeps cover every unresolved chapter - or a sweep found no
    announcements at all - only the ear is left. A sweep that found
    announcements but left chapters uncovered calls for the full sweep.
    """
    if not weak:
        return "By ear"
    ev = resolve_evidence(analysis)
    if not ev["listen_ran"]:
        return "Listen"
    spans = ev["sweep_spans"]
    times = [_chapter_time(r) for r in weak]
    if spans and all(t is not None and any(s <= t <= e for s, e in spans)
                     for t in times):
        return "By ear"
    if not spans:
        return "Sweep range" if ev["listen_heard"] else "Sweep probe"
    if not ev["sweep_heard"]:
        return "By ear"
    return "Full sweep"


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
        chosen = _font(self, weight="bold", underline=True)
        normal = _font(self)
        for key in STAGE_KEYS:
            st = states[key]["state"]
            lbl = self._labels[key]
            glyph = GLYPH.get(st, "○")
            lbl.configure(text=f"{glyph} {STAGE_LABELS[key]}",
                          font=chosen if key == selected else normal,
                          fg={DONE: palette.OK, ATTENTION: palette.WARN,
                              BLOCKED: palette.BLOCKED}.get(st, palette.TEXT))

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
                 background=palette.TIP, foreground=palette.TEXT,
                 font="TkDefaultFont", relief="solid", borderwidth=1,
                 padx=4, pady=1, takefocus=0).pack()

    def _hide_tip(self):
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def _font(widget, **kw):
    from .fonts import derived
    return derived(widget, **kw)
