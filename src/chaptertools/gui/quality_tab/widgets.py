"""Shared controls: a parameter row, a metric tile, and a vertical scroller."""

from __future__ import annotations

import tkinter as tk

from chaptertools.gui import palette
from chaptertools.gui.theme import require_customtkinter
from chaptertools.gui.ui_kit import BODY, font, secondary

from .summary import close, decimals_for, format_number, parse_number, snap

ctk = require_customtkinter()


class ScrollableFrame(ctk.CTkScrollableFrame):
    """A vertical scroller. Children pack into this frame."""

    def __init__(self, master):
        super().__init__(master, fg_color=palette.Q_PANEL, corner_radius=0)
        from chaptertools.gui.ui_kit import watch_scroll
        watch_scroll(self)
        self.body = self


class MetricTile(ctk.CTkFrame):
    """One analysis figure, with an optional warning face."""

    def __init__(self, master, title: str):
        super().__init__(master, fg_color=palette.Q_PANEL, corner_radius=8,
                         border_width=1, border_color=palette.Q_BORDER)
        self.warning = False
        self.title = ctk.CTkLabel(
            self, text=title, text_color=palette.Q_MUTED, anchor="w")
        self.title.pack(anchor="w", padx=4, pady=(4, 0))
        self.value_var = tk.StringVar(value="\u2014")
        self.value = ctk.CTkLabel(
            self, textvariable=self.value_var, text_color=palette.Q_TEXT,
            font=font(BODY, mono=True), anchor="w")
        self.value.pack(anchor="w", padx=4)
        self.sub_var = tk.StringVar(value="")
        self.sub = ctk.CTkLabel(
            self, textvariable=self.sub_var, text_color=palette.Q_MUTED,
            anchor="w", text="")
        self.sub.pack(anchor="w", padx=4, pady=(0, 4))

    def set(self, value: str, sub: str = "", *, warn: bool = False) -> None:
        self.warning = warn
        self.value_var.set(value)
        self.sub_var.set(sub)
        face = palette.Q_WARN_BG if warn else palette.Q_PANEL
        text = palette.Q_WARN_TEXT if warn else palette.Q_TEXT
        muted = palette.Q_WARN_TEXT if warn else palette.Q_MUTED
        self.configure(fg_color=face)
        self.title.configure(text_color=muted)
        self.value.configure(text_color=text)
        self.sub.configure(text_color=muted)


class ParamRow(ctk.CTkFrame):
    """Label, slider, and an entry that share one value.

    The reset control keeps its column even when the value matches the
    preset, so the row does not jump. EQ uses the compact layout: the
    number sits on the label line and the scale is full width below.
    """

    def __init__(self, master, stage, spec, baseline, on_edit, *,
                 compact: bool = False, enabled: bool = True):
        super().__init__(master, fg_color=palette.Q_PANEL, corner_radius=0)
        self.stage = stage
        self.spec = spec
        self.baseline = baseline
        self.on_edit = on_edit
        self.compact = compact
        self._writing = False
        self.columnconfigure(1, weight=1)
        decimals = decimals_for(spec.step)
        ratio = spec.key == "ratio"
        current = stage.params[spec.key]
        self.scale_var = tk.DoubleVar(value=float(current))
        self.text_var = tk.StringVar(value=format_number(
            current, decimals, spec.unit, ratio=ratio))
        label_color = palette.Q_TEXT if enabled else palette.Q_MUTED
        self.label = ctk.CTkLabel(
            self, text=spec.label, text_color=label_color,
            width=100 if compact else 140, anchor="w")
        state = "normal" if enabled else "disabled"
        self.scale = ctk.CTkSlider(
            self, from_=spec.minimum, to=spec.maximum,
            variable=self.scale_var, command=self._from_scale,
            state=state,
        )
        self.scale.bind("<Double-Button-1>", self._reset_event)
        self.scale.bind("<MouseWheel>", self._wheel)
        self.scale.bind("<Button-4>", self._wheel)
        self.scale.bind("<Button-5>", self._wheel)
        self.reset = secondary(
            self, "", width=28, command=self.reset_to_baseline)
        from chaptertools.gui import tips
        tips.tooltip(self.reset, "Reset to the preset value")
        if compact:
            self.label.grid(row=0, column=0, sticky="w")
            self.value_label = ctk.CTkLabel(
                self, textvariable=self.text_var,
                font=font(BODY, mono=True), width=100, anchor="e")
            self.value_label.grid(row=0, column=1, sticky="e")
            self.reset.grid(row=0, column=2, sticky="e")
            self.scale.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 2))
            self.spin = None
        else:
            self.label.grid(row=0, column=0, sticky="w")
            self.scale.grid(row=0, column=1, sticky="ew", padx=4)
            self.spin = ctk.CTkEntry(
                self, textvariable=self.text_var, width=90, state=state,
                justify="right")
            self.spin.grid(row=0, column=2, sticky="e", padx=(0, 4))
            self.spin.bind("<FocusOut>", lambda _e: self._from_spin())
            self.spin.bind("<Return>", lambda _e: self._from_spin())
            self.reset.grid(row=0, column=3, sticky="e")
            self.value_label = None
        self._show_reset()

    def set_baseline(self, value) -> None:
        self.baseline = value
        self._show_reset()

    def set_dim(self, dim: bool) -> None:
        if str(self.scale.cget("state")) == "disabled":
            return
        self.label.configure(
            text_color=palette.Q_MUTED if dim else palette.Q_TEXT)

    def sync(self) -> None:
        value = self.stage.params[self.spec.key]
        self.scale_var.set(float(value))
        self.text_var.set(self._format(value))
        self._show_reset()

    def reset_to_baseline(self) -> None:
        self._commit(self.baseline)

    def _reset_event(self, _event):
        self.reset_to_baseline()
        return "break"

    def _wheel(self, event):
        if str(self.scale.cget("state")) == "disabled":
            return "break"
        step = float(self.spec.step or 1)
        if getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
            step = -step
        self._commit(float(self.scale_var.get()) + step)
        return "break"

    def _from_scale(self, _value):
        self._commit(float(self.scale_var.get()), rewrite_text=True)

    def _from_spin(self):
        try:
            number = parse_number(self.text_var.get())
        except (TypeError, ValueError, IndexError):
            self.text_var.set(self._format(self.stage.params[self.spec.key]))
            return
        self._commit(number)

    def _commit(self, value, *, rewrite_text: bool = True) -> None:
        if self._writing:
            return
        self._writing = True
        try:
            number = snap(value, self.spec)
            self.stage.params[self.spec.key] = number
            self.scale_var.set(number)
            if rewrite_text:
                self.text_var.set(self._format(number))
            self._show_reset()
            self.on_edit()
        finally:
            self._writing = False

    def _format(self, value) -> str:
        return format_number(
            value, decimals_for(self.spec.step), self.spec.unit,
            ratio=self.spec.key == "ratio")

    def _show_reset(self) -> None:
        changed = not close(self.stage.params[self.spec.key], self.baseline)
        if changed:
            self.reset.configure(text="\u21ba")
            self.reset.grid()
        else:
            self.reset.grid_remove()
        if self.spin is not None:
            self.spin.configure(
                border_color=palette.Q_ACCENT if changed else palette.Q_BORDER)


class ChoiceRow(ctk.CTkFrame):
    """A combobox with a field label and no second copy of the value."""

    def __init__(self, master, label, values, current, on_pick, *, enabled=True):
        from tkinter import ttk
        super().__init__(master, fg_color=palette.Q_PANEL, corner_radius=0)
        self.on_pick = on_pick
        self.values = list(values)
        ctk.CTkLabel(self, text=label, text_color=palette.Q_TEXT,
                     width=140, anchor="w").pack(side="left")
        self.var = tk.StringVar(value=current)
        state = "readonly" if enabled else "disabled"
        self.box = ttk.Combobox(self, textvariable=self.var, values=self.values,
                                width=22, state=state)
        self.box.pack(side="left", fill="x", expand=True)
        self.box.bind("<<ComboboxSelected>>", self._picked)
        from chaptertools.gui.ui_kit import secondary
        from chaptertools.gui import tips
        self.reset = secondary(self, "", width=28, command=self._reset)
        tips.tooltip(self.reset, "Reset to the preset value")
        self.reset.pack(side="right")
        self.baseline = current
        self._show_reset()

    def set_baseline(self, value: str) -> None:
        self.baseline = value
        self._show_reset()

    def _picked(self, _event=None):
        shown = self.on_pick(self.var.get())
        if shown is not None:
            self.var.set(shown)
        self._show_reset()

    def _reset(self) -> None:
        self.var.set(self.baseline)
        self._picked()

    def _show_reset(self) -> None:
        changed = self.var.get() != self.baseline
        if changed:
            self.reset.configure(text="\u21ba")
            self.reset.pack(side="right")
        else:
            self.reset.pack_forget()
