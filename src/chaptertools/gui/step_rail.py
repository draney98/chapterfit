"""The only step selector: Scan, Match, Place, Finish, and Audio cleanup."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .stages import ATTENTION, BLOCKED, DONE, GLYPH
from .tips import tooltip

_FIX = {
    "identify first": "On Match, Search for the book, then choose an edition.",
    "align first": "On Match, choose Use this edition so the chapters can be placed.",
    "identify and align first": "Match an edition, then place the chapters.",
    "emit first": "Dry-run on Finish, then Apply, to write the chapters.",
}

STEPS = ("scan", "match", "place", "finish", "cleanup")
STEP_LABELS = {
    "scan": "Scan",
    "match": "Match",
    "place": "Place",
    "finish": "Finish",
    "cleanup": "Audio cleanup",
}
STAGE_STEP = {
    "measure": "scan",
    "identify": "match",
    "align": "place",
    "resolve": "place",
    "emit": "finish",
    "finalize": "finish",
}


class StepRail(ttk.Frame):
    def __init__(self, master, on_pick):
        super().__init__(master)
        self.on_pick = on_pick
        self._buttons: dict[str, ttk.Button] = {}
        self._subs: dict[str, tk.StringVar] = {}
        self._tips: dict[str, list] = {}
        for index, key in enumerate(STEPS):
            if key == "cleanup":
                ttk.Separator(self, orient="vertical").grid(
                    row=0, column=index + 1, rowspan=2, sticky="ns", padx=8)
            col = index if key != "cleanup" else index + 2
            var = tk.StringVar(value="")
            self._subs[key] = var
            button = ttk.Button(
                self, text=STEP_LABELS[key],
                command=lambda k=key: self.on_pick(k))
            button.grid(row=0, column=col, sticky="ew", padx=2)
            label = ttk.Label(self, textvariable=var, foreground="#3d3d3d")
            label.grid(row=1, column=col, sticky="w", padx=4)
            self._buttons[key] = button
            self._tips[key] = [tooltip(button, ""), tooltip(label, "")]

    def update(self, states: dict, step: str, cleanup: str) -> None:
        mapping = {
            "scan": "measure",
            "match": "identify",
            "place": "resolve",
            "finish": "finalize",
        }
        for index, key in enumerate(STEPS, start=1):
            if key == "cleanup":
                mark = "▪" if step == key else "○"
                sub = cleanup
            else:
                info = states.get(mapping[key]) or {}
                state = info.get("state") or "pending"
                mark = str(index) if step == key else GLYPH.get(state, "○")
                sub = info.get("detail") or _fallback(state)
            self._buttons[key].configure(text=f"{mark}  {STEP_LABELS[key]}")
            self._subs[key].set(sub)
            hint = blocked_hint(key, states)
            for tip in self._tips[key]:
                tip.text = hint


def blocked_hint(key: str, states: dict) -> str:
    """What to do when this step cannot run yet. Empty when it is not blocked."""
    order = {"place": ("align", "resolve"), "finish": ("emit", "finalize")}
    for stage in order.get(key, ()):
        info = states.get(stage) or {}
        if info.get("state") == BLOCKED and info.get("why"):
            return _FIX.get(info["why"], "")
    return ""


def _fallback(state: str) -> str:
    if state == DONE:
        return "done"
    if state == ATTENTION:
        return "needs attention"
    if state == BLOCKED:
        return "blocked"
    return "not run"


class Workspace(ttk.Frame):
    """Main and inspector. Side by side from 1500 px, stacked below that."""

    def __init__(self, master):
        super().__init__(master)
        self.main = ttk.Frame(self)
        self.inspector = ttk.Frame(self)
        self.main.grid(row=0, column=0, sticky="nsew")
        self.inspector.grid(row=0, column=1, sticky="nsew")
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(0, weight=1)
        self._wide = True
        self._beside = True
        self._table_step = False
        self._show_inspector = True
        self._mode: tuple | None = None
        self.bind("<Configure>", self._layout)

    def set_inspector(self, visible: bool, beside: bool = True,
                      table: bool = False) -> None:
        self._show_inspector = visible
        self._beside = beside
        self._table_step = table
        self._apply()

    def _layout(self, _event) -> None:
        self._wide = self.winfo_toplevel().winfo_width() >= 1500
        mode = (self._show_inspector, self._wants_beside())
        if mode == self._mode:
            return
        self._apply()

    def _wants_beside(self) -> bool:
        # The chapter table needs the full width until the window is wide
        # enough for that table and the inspector together.
        table_fits = (not self._table_step
                      or self.winfo_toplevel().winfo_width() >= 1800)
        return self._wide and self._beside and table_fits

    def _apply(self) -> None:
        self._mode = (self._show_inspector, self._wants_beside())
        self.main.grid_forget()
        self.inspector.grid_forget()
        if not self._show_inspector:
            self.columnconfigure(0, weight=1)
            self.columnconfigure(1, weight=0)
            self.rowconfigure(1, weight=0)
            self.main.grid(row=0, column=0, columnspan=2, sticky="nsew")
            return
        if self._wants_beside():
            self.columnconfigure(0, weight=3)
            self.columnconfigure(1, weight=2)
            self.rowconfigure(0, weight=1)
            self.rowconfigure(1, weight=0)
            self.main.grid(row=0, column=0, sticky="nsew")
            self.inspector.grid(row=0, column=1, sticky="nsew")
        else:
            self.columnconfigure(0, weight=1)
            self.columnconfigure(1, weight=0)
            self.rowconfigure(0, weight=3)
            self.rowconfigure(1, weight=1)
            self.main.grid(row=0, column=0, columnspan=2, sticky="nsew")
            self.inspector.grid(row=1, column=0, columnspan=2, sticky="nsew")
