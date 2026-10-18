"""The only step selector: Scan, Match, Place, Finish, and Audio cleanup."""

from __future__ import annotations

import tkinter as tk

import customtkinter as ctk  # noqa: E402 — after ui_kit font pick

from .stages import ATTENTION, BLOCKED, DONE, PENDING
from .tips import tooltip
from .ui_kit import PAD, BODY, C, body, font, small

_FIX = {
    "identify first": "On Match, Search for the book, then choose an edition.",
    "align first": "On Match, choose Use this edition so the chapters can be placed.",
    "identify and align first": "Match an edition, then place the chapters.",
    "emit first": "Dry-run on Finish, then Apply, to write the chapters.",
    "needs Place": "Place the chapters before Finish can run.",
    "waiting on Place": "Place the chapters before Finish can run.",
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


def _ellipsize(text: str, limit: int = 36) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


class StepRail(ctk.CTkFrame):
    def __init__(self, master, on_pick):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.on_pick = on_pick
        self._buttons: dict[str, ctk.CTkFrame] = {}
        self._title: dict[str, ctk.CTkLabel] = {}
        self._subs: dict[str, tk.StringVar] = {}
        self._sub_lbl: dict[str, ctk.CTkLabel] = {}
        self._tips: dict[str, list] = {}
        self._current = ""
        for index, key in enumerate(STEPS):
            col = index
            cell = ctk.CTkFrame(
                self, corner_radius=4, fg_color=C["sunken"], cursor="hand2",
                border_width=0, width=176)
            cell.grid(row=0, column=col, sticky="nsew", padx=PAD // 2, pady=2)
            cell.columnconfigure(0, weight=1)
            title = body(cell, text=STEP_LABELS[key])
            title.configure(anchor="w", font=font(BODY, "bold"))
            title.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))
            var = tk.StringVar(value="")
            self._subs[key] = var
            sub = small(cell, text="")
            sub.configure(textvariable=var, wraplength=160)
            sub.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
            bar = ctk.CTkFrame(
                cell, height=2, corner_radius=0, fg_color=C["border"])
            bar.grid(row=2, column=0, sticky="ew")

            def _fit(_event, label=sub, rule=bar, card=cell):
                label.configure(wraplength=max(40, card.winfo_width() - 2 * PAD))
                rule.configure(width=max(1, card.winfo_width()))
                self._equalize()

            cell.bind("<Configure>", _fit)
            cell._bar = bar  # noqa: SLF001
            for widget in (cell, title, sub):
                widget.bind("<Button-1>", lambda _e, k=key: self.on_pick(k))
            self._buttons[key] = cell
            self._title[key] = title
            self._sub_lbl[key] = sub
            self._tips[key] = [tooltip(cell, ""), tooltip(title, ""), tooltip(sub, "")]
            card_floor = 100
            self.columnconfigure(col, weight=1, uniform="steps", minsize=card_floor)

    def update(self, states: dict, step: str, cleanup: str,
               finish_detail: str | None = None,
               place_status: dict | None = None,
               finish_status: dict | None = None) -> None:
        mapping = {
            "scan": "measure",
            "match": "identify",
            "place": "resolve",
            "finish": "finalize",
        }
        self._current = step
        for index, key in enumerate(STEPS, start=1):
            if key == "cleanup":
                written = "written" in (cleanup or "")
                sub = cleanup if cleanup else "off · optional"
                state = DONE if written else PENDING
            elif key == "place" and place_status is not None:
                state = place_status.get("state") or PENDING
                sub = place_status.get("detail") or ""
            elif key == "finish" and finish_status is not None:
                state = finish_status.get("state") or PENDING
                sub = finish_status.get("detail") or finish_detail or ""
            else:
                info = states.get(mapping[key]) or {}
                state = info.get("state") or PENDING
                if key == "finish" and finish_detail is not None:
                    sub = finish_detail
                    if finish_detail.startswith("waiting on Place") or (
                            finish_detail.startswith("needs Place")):
                        state = BLOCKED
                    elif (finish_detail.startswith("needs a dry run")
                          or finish_detail.startswith("ready")):
                        state = PENDING
                    elif finish_detail.startswith("applied"):
                        state = DONE
                else:
                    sub = info.get("detail") or _fallback(key, state)
            self._title[key].configure(
                text=f"{index}  {STEP_LABELS[key]}", text_color=C["text"])
            self._subs[key].set(_ellipsize(sub))
            self._sub_lbl[key].configure(text_color=self._status_color(state))
            self._paint(key, key == step)
            hint = blocked_hint(key, states)
            if key == "finish" and (finish_detail or "").startswith(
                    ("needs Place", "waiting on Place")):
                hint = finish_detail
            for tip in self._tips[key]:
                tip.text = hint or ""
        if getattr(self, "_dim", False):
            self.set_dim(True)

    def set_dim(self, dim: bool) -> None:
        """No book yet: keep the steps visible, in the muted colour."""
        self._dim = dim
        if not dim:
            return
        for key in STEPS:
            self._title[key].configure(text_color=C["muted"])
            self._sub_lbl[key].configure(text_color=C["muted"])
            self._paint(key, False)

    def _status_color(self, state: str) -> str:
        if state == DONE:
            return C["ok"]
        if state == ATTENTION:
            return C["warn_fg"]
        return C["muted"]

    def _equalize(self) -> None:
        """Every card is as tall as the tallest subtitle."""
        if getattr(self, "_equalizing", False):
            return
        self._equalizing = True
        try:
            tall = max(cell.winfo_reqheight() for cell in self._buttons.values())
            for cell in self._buttons.values():
                if int(cell.cget("height")) != tall:
                    cell.configure(height=tall)
        finally:
            self._equalizing = False

    def _paint(self, key: str, current: bool) -> None:
        cell = self._buttons[key]
        bg = C["panel"] if current else C["sunken"]
        bar_bg = C["accent"] if current else C["sunken"]
        cell.configure(fg_color=bg)
        cell._bar.configure(height=2, fg_color=bar_bg)  # noqa: SLF001


def blocked_hint(key: str, states: dict) -> str:
    """What to do when this step cannot run yet. Empty when it is not blocked."""
    order = {"place": ("align", "resolve"), "finish": ("emit", "finalize")}
    for stage in order.get(key, ()):
        info = states.get(stage) or {}
        if info.get("state") == BLOCKED and info.get("why"):
            return _FIX.get(info["why"], "")
    return ""


def _fallback(key: str, state: str) -> str:
    if key == "match" and state == PENDING:
        return "pick an edition"
    if key == "finish" and state == BLOCKED:
        return "waiting on Place"
    if state == DONE:
        return "done"
    if state == ATTENTION:
        return "needs attention"
    if state == BLOCKED:
        return "waiting on Place" if key == "finish" else "waiting on Match"
    return "not run"


class Workspace(ctk.CTkFrame):
    """Main and inspector. Side by side from 1200 px, stacked below that.

    The inspector column is at least 340 px and at most 400.
    """

    INSPECTOR_MIN = 340
    INSPECTOR_WIDTH = 400

    def __init__(self, master):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        from .theme import ctk_frame
        from .ui_kit import PAD, C
        pad = PAD * 2
        self.main = ctk_frame(self, fg_color=C["bg"], corner_radius=0)
        self.inspector = ctk_frame(self, fg_color=C["bg"], corner_radius=0)
        self._main_pad = pad
        self._insp_pad = pad
        self.main.grid(row=0, column=0, sticky="nsew", padx=(pad, pad // 2),
                       pady=pad)
        self.inspector.grid(row=0, column=1, sticky="nsew",
                            padx=(pad // 2, pad), pady=pad)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, minsize=self.INSPECTOR_WIDTH, weight=0)
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
        self._wide = self.winfo_toplevel().winfo_width() >= 1200
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
        pad = self._main_pad
        collapsed = 0
        if not self._show_inspector:
            self.columnconfigure(0, weight=1)
            self.columnconfigure(1, minsize=collapsed, weight=0)
            self.rowconfigure(1, weight=0)
            self.main.grid(row=0, column=0, columnspan=2, sticky="nsew",
                           padx=pad, pady=pad)
            return
        if self._wants_beside():
            self.columnconfigure(0, weight=1)
            self.columnconfigure(1, minsize=self.INSPECTOR_WIDTH, weight=0)
            self.rowconfigure(0, weight=1)
            self.rowconfigure(1, weight=0)
            self.inspector.configure(width=self.INSPECTOR_WIDTH)
            self.main.grid(row=0, column=0, sticky="nsew",
                           padx=(pad, pad // 2), pady=pad)
            self.inspector.grid(row=0, column=1, sticky="nsew",
                                padx=(pad // 2, pad), pady=pad)
        else:
            self.columnconfigure(0, weight=1)
            self.columnconfigure(1, minsize=collapsed, weight=0)
            self.rowconfigure(0, weight=1)
            self.rowconfigure(1, weight=0)
            self.main.grid(row=0, column=0, columnspan=2, sticky="nsew",
                           padx=pad, pady=(pad, pad // 2))
            self.inspector.grid(row=1, column=0, columnspan=2, sticky="nsew",
                                padx=pad, pady=(pad // 2, pad))
