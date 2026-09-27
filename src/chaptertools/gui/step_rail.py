"""The only step selector: Scan, Match, Place, Finish, and Audio cleanup."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .stages import ATTENTION, BLOCKED, DONE, GLYPH

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
            ttk.Label(self, textvariable=var, foreground="#3d3d3d").grid(
                row=1, column=col, sticky="w", padx=4)
            self._buttons[key] = button

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
        self._show_inspector = True
        self.bind("<Configure>", self._layout)

    def set_inspector(self, visible: bool) -> None:
        self._show_inspector = visible
        self._apply()

    def _layout(self, event) -> None:
        wide = event.width >= 1500
        if wide == self._wide:
            return
        self._wide = wide
        self._apply()

    def _apply(self) -> None:
        self.main.grid_forget()
        self.inspector.grid_forget()
        if not self._show_inspector:
            self.columnconfigure(0, weight=1)
            self.columnconfigure(1, weight=0)
            self.rowconfigure(1, weight=0)
            self.main.grid(row=0, column=0, columnspan=2, sticky="nsew")
            return
        if self._wide:
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
