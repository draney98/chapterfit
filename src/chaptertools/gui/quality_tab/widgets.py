"""Shared controls: a parameter row, a metric tile, and a vertical scroller."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from chaptertools.gui import palette

from .summary import close, decimals_for, format_number, parse_number, snap


class ScrollableFrame(ttk.Frame):
    """A vertical scroller. The canvas is the only widget that grows."""

    def __init__(self, master):
        super().__init__(master, style="Panel.TFrame")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, highlightthickness=0, background=palette.Q_PANEL)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.body = ttk.Frame(self.canvas, style="Panel.TFrame")
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._fit_scroll)
        self.canvas.bind("<Configure>", self._fit_width)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", self._wheel)
        self.canvas.bind("<Button-5>", self._wheel)

    def _fit_scroll(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _fit_width(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)

    def _wheel(self, event):
        if event.num == 4:
            self.canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(1, "units")
        else:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


class MetricTile(ttk.Frame):
    """One analysis figure, with an optional warning face."""

    def __init__(self, master, title: str):
        super().__init__(master, style="Card.TFrame", padding=4)
        self.warning = False
        self.title = ttk.Label(self, text=title, style="MetricMuted.TLabel")
        self.title.pack(anchor="w")
        self.value_var = tk.StringVar(value="\u2014")
        self.value = ttk.Label(self, textvariable=self.value_var, style="Metric.TLabel")
        self.value.pack(anchor="w")
        self.sub_var = tk.StringVar(value="")
        self.sub = ttk.Label(self, textvariable=self.sub_var, style="MetricMuted.TLabel")
        self.sub.pack(anchor="w")

    def set(self, value: str, sub: str = "", *, warn: bool = False) -> None:
        self.warning = warn
        self.value_var.set(value)
        self.sub_var.set(sub)
        face = "Warn.TFrame" if warn else "Card.TFrame"
        label = "Warn.TLabel" if warn else "Metric.TLabel"
        muted = "WarnMuted.TLabel" if warn else "MetricMuted.TLabel"
        self.configure(style=face)
        self.title.configure(style=muted)
        self.value.configure(style=label)
        self.sub.configure(style=muted)


class ParamRow(ttk.Frame):
    """Label, scale, and a spinbox that share one value.

    The reset control keeps its column even when the value matches the
    preset, so the row does not jump. EQ uses the compact layout: the
    number sits on the label line and the scale is full width below.
    """

    def __init__(self, master, stage, spec, baseline, on_edit, *,
                 compact: bool = False, enabled: bool = True):
        super().__init__(master, style="Panel.TFrame")
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
        label_style = "Panel.TLabel" if enabled else "Dim.TLabel"
        self.label = ttk.Label(
            self, text=spec.label, style=label_style,
            width=12 if compact else 18, anchor="w")
        state = "normal" if enabled else "disabled"
        self.scale = ttk.Scale(
            self, from_=spec.minimum, to=spec.maximum, variable=self.scale_var,
            command=self._from_scale,
        )
        self.scale.configure(state=state)
        self.scale.bind("<Double-Button-1>", self._reset_event)
        self.scale.bind("<MouseWheel>", self._wheel)
        self.scale.bind("<Button-4>", self._wheel)
        self.scale.bind("<Button-5>", self._wheel)
        self.reset = ttk.Button(self, text="", width=2, style="Quality.Reset.TButton",
                                command=self.reset_to_baseline)
        if compact:
            self.label.grid(row=0, column=0, sticky="w")
            self.value_label = ttk.Label(self, textvariable=self.text_var,
                                         style="Mono.TLabel", width=12, anchor="e")
            self.value_label.grid(row=0, column=1, sticky="e")
            self.reset.grid(row=0, column=2, sticky="e")
            self.scale.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 2))
            self.spin = None
        else:
            self.label.grid(row=0, column=0, sticky="w")
            self.scale.grid(row=0, column=1, sticky="ew", padx=4)
            self.spin = ttk.Spinbox(
                self, textvariable=self.text_var, width=10,
                from_=spec.minimum, to=spec.maximum, increment=spec.step or 1,
                command=self._from_spin, state=state, justify="right",
            )
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
        self.label.configure(style="Dim.TLabel" if dim else "Panel.TLabel")

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
        self.reset.configure(text="\u21ba" if changed else "",
                             state="normal" if changed else "disabled")
        if self.spin is not None:
            self.spin.configure(style="Changed.TSpinbox" if changed else "TSpinbox")


class ChoiceRow(ttk.Frame):
    """A combobox with a field label and no second copy of the value."""

    def __init__(self, master, label, values, current, on_pick, *, enabled=True):
        super().__init__(master, style="Panel.TFrame")
        self.on_pick = on_pick
        self.values = list(values)
        ttk.Label(self, text=label, style="Panel.TLabel", width=18,
                  anchor="w").pack(side="left")
        self.var = tk.StringVar(value=current)
        state = "readonly" if enabled else "disabled"
        self.box = ttk.Combobox(self, textvariable=self.var, values=self.values,
                                width=22, state=state)
        self.box.pack(side="left", fill="x", expand=True)
        self.box.bind("<<ComboboxSelected>>", self._picked)
        self.reset = ttk.Button(self, text="", width=2, command=self._reset)
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
        self.reset.configure(text="\u21ba" if changed else "",
                             state="normal" if changed else "disabled")
