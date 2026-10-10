"""The only step selector: Scan, Match, Place, Finish, and Audio cleanup."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .stages import ATTENTION, BLOCKED, DONE, PENDING
from .tips import tooltip

_FIX = {
    "identify first": "On Match, Search for the book, then choose an edition.",
    "align first": "On Match, choose Use this edition so the chapters can be placed.",
    "identify and align first": "Match an edition, then place the chapters.",
    "emit first": "Dry-run on Finish, then Apply, to write the chapters.",
    "needs Place": "Place the chapters before Finish can run.",
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

_ACCENT = "#4A6984"
_IDLE_BG = "#E9E7E2"
_CURRENT_BG = "#FFFFFF"
_DONE_FG = "#2E7D32"


class StepRail(ttk.Frame):
    def __init__(self, master, on_pick):
        super().__init__(master)
        self.on_pick = on_pick
        self._buttons: dict[str, tk.Frame] = {}
        self._title: dict[str, tk.Label] = {}
        self._subs: dict[str, tk.StringVar] = {}
        self._tips: dict[str, list] = {}
        self._current = ""
        style = ttk.Style(self)
        style.configure("RailGap.TSeparator", background="#C8C5BE")
        for index, key in enumerate(STEPS):
            if key == "cleanup":
                ttk.Separator(self, orient="vertical", style="RailGap.TSeparator").grid(
                    row=0, column=index + 1, sticky="ns", padx=8, pady=2)
            col = index if key != "cleanup" else index + 2
            cell = tk.Frame(self, width=200, height=52, bd=0, highlightthickness=0,
                            bg=_IDLE_BG, cursor="hand2")
            cell.grid(row=0, column=col, sticky="nsew", padx=0)
            cell.grid_propagate(False)
            cell.columnconfigure(0, weight=1)
            title = tk.Label(
                cell, text=STEP_LABELS[key], anchor="w",
                font=("", 11, "bold"), bg=_IDLE_BG, padx=10, pady=4)
            title.grid(row=0, column=0, sticky="ew")
            var = tk.StringVar(value="")
            self._subs[key] = var
            sub = tk.Label(
                cell, textvariable=var, anchor="w",
                font=("", 9), fg="#3d3d3d", bg=_IDLE_BG, padx=22)
            sub.grid(row=1, column=0, sticky="ew")
            bar = tk.Frame(cell, height=2, bg="#C8C5BE", bd=0)
            bar.grid(row=2, column=0, sticky="ew")
            cell._bar = bar  # noqa: SLF001
            for widget in (cell, title, sub):
                widget.bind("<Button-1>", lambda _e, k=key: self.on_pick(k))
            self._buttons[key] = cell
            self._title[key] = title
            self._tips[key] = [tooltip(cell, ""), tooltip(title, ""), tooltip(sub, "")]
            self.columnconfigure(col, weight=0)

    def update(self, states: dict, step: str, cleanup: str,
               finish_detail: str | None = None) -> None:
        mapping = {
            "scan": "measure",
            "match": "identify",
            "place": "resolve",
            "finish": "finalize",
        }
        self._current = step
        for index, key in enumerate(STEPS, start=1):
            if key == "cleanup":
                mark = "✓" if "written" in cleanup else str(index)
                sub = cleanup if cleanup else "off · optional"
                state = DONE if "written" in cleanup else PENDING
            else:
                info = states.get(mapping[key]) or {}
                state = info.get("state") or "pending"
                if key == "finish" and finish_detail is not None:
                    sub = finish_detail
                    if finish_detail.startswith("needs Place"):
                        state = BLOCKED
                    elif finish_detail.startswith("needs a dry run"):
                        state = "pending"
                    elif finish_detail.startswith("ready"):
                        state = "pending"
                    elif finish_detail.startswith("applied"):
                        state = DONE
                else:
                    sub = info.get("detail") or _fallback(key, state)
                mark = "✓" if state == DONE else (
                    "✕" if state == BLOCKED else (
                        "!" if state == ATTENTION else str(index)))
            title = f"{mark}  {STEP_LABELS[key]}"
            self._title[key].configure(text=title)
            if state == DONE:
                self._title[key].configure(fg=_DONE_FG)
            else:
                self._title[key].configure(fg="#1a1a1a")
            self._subs[key].set(sub)
            self._paint(key, key == step)
            hint = blocked_hint(key, states)
            if key == "finish" and (finish_detail or "").startswith("needs Place"):
                hint = finish_detail
            for tip in self._tips[key]:
                tip.text = hint or ""

    def _paint(self, key: str, current: bool) -> None:
        cell = self._buttons[key]
        bg = _CURRENT_BG if current else _IDLE_BG
        bar_h = 3 if current else 2
        bar_bg = _ACCENT if current else "#C8C5BE"
        cell.configure(bg=bg)
        self._title[key].configure(bg=bg)
        for child in cell.winfo_children():
            if isinstance(child, tk.Label):
                child.configure(bg=bg)
        cell._bar.configure(height=bar_h, bg=bar_bg)  # noqa: SLF001


def blocked_hint(key: str, states: dict) -> str:
    """What to do when this step cannot run yet. Empty when it is not blocked."""
    order = {"place": ("align", "resolve"), "finish": ("emit", "finalize")}
    for stage in order.get(key, ()):
        info = states.get(stage) or {}
        if info.get("state") == BLOCKED and info.get("why"):
            return _FIX.get(info["why"], "")
    return ""


def _fallback(key: str, state: str) -> str:
    if key == "match" and state == "pending":
        return "pick an edition"
    if key == "finish" and state == BLOCKED:
        return "needs Place"
    if state == DONE:
        return "done"
    if state == ATTENTION:
        return "needs attention"
    if state == BLOCKED:
        return "needs Place" if key == "finish" else "blocked"
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
