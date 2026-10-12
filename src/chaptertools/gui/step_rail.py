"""The only step selector: Scan, Match, Place, Finish, and Audio cleanup."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import palette
from .stages import ATTENTION, BLOCKED, DONE, PENDING
from .theme import ctk_frame, require_customtkinter
from .tips import tooltip

ctk = require_customtkinter()

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

_ACCENT = palette.RAIL_ACCENT
_IDLE_BG = palette.RAIL_IDLE
_CURRENT_BG = palette.RAIL_CURRENT
_DONE_FG = palette.RAIL_DONE


class StepRail(ctk.CTkFrame):
    def __init__(self, master, on_pick):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.on_pick = on_pick
        self._buttons: dict[str, ctk.CTkFrame] = {}
        self._title: dict[str, ctk.CTkLabel] = {}
        self._subs: dict[str, tk.StringVar] = {}
        self._tips: dict[str, list] = {}
        self._current = ""
        style = ttk.Style(self)
        style.configure("RailGap.TSeparator", background=palette.BORDER)
        for index, key in enumerate(STEPS):
            if key == "cleanup":
                ttk.Separator(self, orient="vertical", style="RailGap.TSeparator").grid(
                    row=0, column=index + 1, sticky="ns", padx=8, pady=2)
            col = index if key != "cleanup" else index + 2
            # Fixed shell; compact label heights so title+sub fit (CTkLabel
            # defaults to height=28 and would clip inside 52 px).
            cell = ctk.CTkFrame(
                self, width=140, height=52, corner_radius=8,
                fg_color=_IDLE_BG, cursor="hand2")
            cell.grid(row=0, column=col, sticky="ew", padx=2, pady=2)
            cell.grid_propagate(False)
            cell.columnconfigure(0, weight=1)
            cell.rowconfigure(2, weight=1)
            title = ctk.CTkLabel(
                cell, text=STEP_LABELS[key], anchor="w", height=18,
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=palette.TEXT, fg_color="transparent")
            title.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 0))
            var = tk.StringVar(value="")
            self._subs[key] = var
            sub = ctk.CTkLabel(
                cell, textvariable=var, anchor="w", height=16,
                font=ctk.CTkFont(size=11),
                text_color=palette.MUTED, fg_color="transparent")
            sub.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 2))
            bar = ctk.CTkFrame(
                cell, width=1, height=2, corner_radius=0, fg_color=palette.BORDER)
            bar.grid(row=3, column=0, sticky="sew")
            cell._bar = bar  # noqa: SLF001
            for widget in (cell, title, sub):
                widget.bind("<Button-1>", lambda _e, k=key: self.on_pick(k))
            self._buttons[key] = cell
            self._title[key] = title
            self._tips[key] = [tooltip(cell, ""), tooltip(title, ""), tooltip(sub, "")]
            self.columnconfigure(col, weight=1, minsize=120)

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
                    elif (finish_detail.startswith("needs a dry run")
                          or finish_detail.startswith("ready")):
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
                self._title[key].configure(text_color=_DONE_FG)
            else:
                self._title[key].configure(text_color=palette.TEXT)
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
        bar_bg = _ACCENT if current else palette.BORDER
        cell.configure(fg_color=bg)
        self._title[key].configure(fg_color="transparent")
        cell._bar.configure(height=bar_h, fg_color=bar_bg)  # noqa: SLF001


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


class Workspace(ctk.CTkFrame):
    """Main and inspector. Side by side from 1500 px, stacked below that."""

    def __init__(self, master):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.main = ctk_frame(self, fg_color="transparent", corner_radius=0)
        self.inspector = ctk_frame(self, fg_color="transparent", corner_radius=0)
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
