"""The detail panel: one stage's controls, swapped when the chain selection changes."""

from __future__ import annotations

import tkinter as tk
from dataclasses import replace
from tkinter import messagebox

from chaptertools.gui import palette
from chaptertools.gui.theme import require_customtkinter

from .summary import close
from .widgets import ChoiceRow, ParamRow, ScrollableFrame

ctk = require_customtkinter()

_INSTALL = "./runme.sh"

DESCRIPTIONS = {
    "highpass": "Cuts rumble below the cutoff.",
    "denoise": "Reduces steady background noise.",
    "enhance": "Rebuilds muffled speech with a neural model.",
    "declick": "Softens short clicks and pops.",
    "eq": "Four-band tone shaping.",
    "deesser": "Turns down harsh sibilance.",
    "compressor": "Evens out loud and quiet passages.",
    "gate": "Ducks quiet gaps without removing time.",
    "loudness": "Sets one loudness for the whole file.",
    "limiter": "Stops peaks from passing the ceiling.",
}

_EQ_BANDS = (
    ("Low", "Peak", (("low_freq", "Frequency"), ("low_gain", "Gain"), ("low_q", "Q"))),
    ("Low-mid", "Peak", (("box_freq", "Frequency"), ("box_gain", "Gain"), ("box_q", "Q"))),
    ("Presence", "Peak", (
        ("presence_freq", "Frequency"), ("presence_gain", "Gain"), ("presence_q", "Q"),
    )),
    ("High", "Peak or shelf", (
        ("air_freq", "Frequency"), ("air_gain", "Gain"), ("air_q", "Q"),
    )),
)


class DetailPanel(ctk.CTkFrame):
    """Header plus a scroller that holds only the selected stage."""

    def __init__(self, master, enabled_vars, on_edit, on_reset_stage, on_learn,
                 on_choice=None):
        super().__init__(master, fg_color=palette.Q_PANEL, corner_radius=0)
        self.enabled_vars = enabled_vars
        self.on_edit = on_edit
        self.on_reset_stage = on_reset_stage
        self.on_learn = on_learn
        self.on_choice = on_choice
        self.stage = None
        self.baseline = None
        self.selection = None
        self.recommend = False
        self.enabled_var = None
        self.multifile = False
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.header = ctk.CTkFrame(self, fg_color=palette.Q_PANEL, corner_radius=0)
        self.header.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        self.header.columnconfigure(0, weight=1)
        self.scroller = ScrollableFrame(self)
        self.scroller.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self._rows = []

    def show(self, stage, baseline: dict, *, selection, recommend: bool) -> None:
        self.stage = stage
        self.baseline = baseline
        self.selection = selection
        self.recommend = recommend
        self.enabled_var = self.enabled_vars[stage.name]
        for child in self.header.winfo_children():
            child.destroy()
        ok, _reason = stage.availability()
        title = ctk.CTkLabel(self.header, text=stage.title, text_color=palette.Q_TEXT,
                             anchor="w")
        title.grid(row=0, column=0, sticky="w")
        self.enabled_check = ctk.CTkCheckBox(
            self.header, text="Enabled", variable=self.enabled_var,
        )
        self.enabled_check.grid(row=0, column=1, sticky="e")
        if not ok:
            self.enabled_check.configure(state="disabled")
        ctk.CTkButton(self.header, text="Reset to preset", height=28,
                      command=lambda: self.on_reset_stage(stage)).grid(
            row=0, column=2, sticky="e", padx=(8, 0))
        ctk.CTkLabel(self.header, text=DESCRIPTIONS.get(stage.name, ""),
                     text_color=palette.Q_MUTED, anchor="w").grid(
            row=1, column=0, columnspan=3, sticky="w")
        for child in self.scroller.body.winfo_children():
            child.destroy()
        self._rows = []
        self._build_editor(stage, ok)
        self.set_dim(stage.bypass and ok)

    def set_dim(self, dim: bool) -> None:
        for row in self._rows:
            if hasattr(row, "set_dim"):
                row.set_dim(dim)

    def _build_editor(self, stage, available: bool) -> None:
        body = self.scroller.body
        body.columnconfigure(0, weight=1)
        if stage.name == "enhance" and not available:
            _install_note(body).grid(row=0, column=0, sticky="ew", pady=(4, 8))
            self._generic(stage, enabled=False, start=1)
            return
        if stage.name == "denoise":
            self._denoise(stage)
            return
        if stage.name == "eq":
            self._eq(stage)
            return
        self._generic(stage, enabled=True, start=0)

    def _generic(self, stage, *, enabled: bool, start: int) -> None:
        body = self.scroller.body
        base = (self.baseline or {}).get("params", {})
        for offset, spec in enumerate(stage.spec):
            if spec.kind == "float":
                row = ParamRow(body, stage, spec, base.get(spec.key, spec.default),
                               self.on_edit, enabled=enabled)
                row.grid(row=start + offset, column=0, sticky="ew", pady=2)
                self._rows.append(row)
            elif spec.kind == "choice":
                self._choice(body, stage, spec, start + offset)
            elif spec.kind == "bool":
                self._bool(body, stage, spec, start + offset, recommend=False)

    def _denoise(self, stage) -> None:
        body = self.scroller.body
        base = (self.baseline or {}).get("params", {})
        mode_spec = _find(stage, "mode")
        strength = _find(stage, "strength")
        per_seg = _find(stage, "per_segment")
        labels = _mode_labels(stage)
        current = _mode_label(stage.params["mode"])
        row = ChoiceRow(
            body, mode_spec.label, labels, current,
            lambda label: self._pick_mode(stage, label),
        )
        row.set_baseline(_mode_label(base.get("mode", mode_spec.default)))
        row.grid(row=0, column=0, sticky="ew", pady=2)
        self._rows.append(row)
        if self.multifile:
            ctk.CTkLabel(
                body,
                text="Denoise runs per file (noise is re-estimated for each).",
                text_color=palette.Q_MUTED,
            ).grid(row=1, column=0, sticky="w", pady=2)
        else:
            self._bool(body, stage, per_seg, 1, recommend=self.recommend,
                       baseline=base.get("per_segment", per_seg.default))
        note = "Select a quiet stretch on the timeline first"
        learn_state = "normal" if self.selection else "disabled"
        block = ctk.CTkFrame(body, fg_color=palette.Q_PANEL, corner_radius=0)
        block.grid(row=2, column=0, sticky="ew", pady=2)
        ctk.CTkButton(block, text="Learn from selection", command=self.on_learn,
                   state=learn_state).pack(side="left")
        if not self.selection:
            ctk.CTkLabel(block, text=note, text_color=palette.Q_MUTED).pack(side="left", padx=8)
        slider = ParamRow(body, stage, strength, base.get("strength", strength.default),
                          self.on_edit)
        slider.grid(row=3, column=0, sticky="ew", pady=2)
        self._rows.append(slider)

    def _eq(self, stage) -> None:
        body = self.scroller.body
        grid = ctk.CTkFrame(body, fg_color=palette.Q_PANEL, corner_radius=0)
        grid.grid(row=1, column=0, sticky="ew")
        for column in range(2):
            grid.columnconfigure(column, weight=1)
        specs = {item.key: item for item in stage.spec}
        base = (self.baseline or {}).get("params", {})
        trim = specs["trim_db"]
        trim_row = ParamRow(
            body, stage, trim, base.get("trim_db", trim.default), self.on_edit)
        trim_row.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self._rows.append(trim_row)
        for index, (title, hint, fields) in enumerate(_EQ_BANDS):
            card = ctk.CTkFrame(grid, fg_color=palette.Q_PANEL, corner_radius=8, border_width=1, border_color=palette.Q_BORDER)
            card.grid(row=index // 2, column=index % 2, sticky="nsew", padx=2, pady=2)
            ctk.CTkLabel(card, text=title, text_color=palette.Q_TEXT).pack(anchor="w")
            ctk.CTkLabel(card, text=hint, text_color=palette.Q_MUTED).pack(anchor="w")
            for key, label in fields:
                spec = replace(specs[key], label=label)
                row = ParamRow(card, stage, spec, base.get(key, spec.default),
                               self.on_edit, compact=True)
                row.pack(fill="x", pady=2)
                self._rows.append(row)
        shape = specs["air_shape"]
        current = _shape_label(stage.params["air_shape"])
        choice = ChoiceRow(
            body, "Shape", ("Peak", "Shelf"), current,
            lambda label: self._pick_shape(stage, label),
        )
        choice.set_baseline(_shape_label(base.get("air_shape", shape.default)))
        choice.grid(row=2, column=0, sticky="ew", pady=(6, 2))
        self._rows.append(choice)
        self.flat_note = ctk.CTkLabel(
            body, text="All gains at 0 dB \u2014 the EQ is currently flat and has no effect.",
            text_color=palette.Q_MUTED,
        )
        self.flat_note.grid(row=3, column=0, sticky="w", pady=(4, 0))
        self._show_flat()
        previous = self.on_edit
        def _edit():
            self._show_flat()
            previous()
        for row in self._rows:
            if isinstance(row, ParamRow):
                row.on_edit = _edit

    def _show_flat(self) -> None:
        if self.stage is None or self.stage.name != "eq" or not hasattr(self, "flat_note"):
            return
        gains = [self.stage.params[key] for key in
                 ("low_gain", "box_gain", "presence_gain", "air_gain")]
        if all(abs(float(gain)) < 0.05 for gain in gains):
            self.flat_note.grid()
        else:
            self.flat_note.grid_remove()

    def _bool(self, body, stage, spec, row_index, *, recommend: bool, baseline=None) -> None:
        if baseline is None:
            baseline = (self.baseline or {}).get("params", {}).get(spec.key, spec.default)
        row = ctk.CTkFrame(body, fg_color=palette.Q_PANEL, corner_radius=0)
        row.grid(row=row_index, column=0, sticky="w", pady=2)
        var = tk.BooleanVar(value=bool(stage.params[spec.key]))

        def _set():
            stage.params[spec.key] = bool(var.get())
            reset.configure(
                text="\u21ba" if not close(var.get(), baseline) else "",
                state="normal" if not close(var.get(), baseline) else "disabled",
            )
            self.on_edit()

        ctk.CTkCheckBox(row, text=spec.label, variable=var, command=_set,
                        ).pack(side="left")
        if recommend and not stage.params[spec.key]:
            ctk.CTkLabel(row, text="recommended", text_color=palette.Q_MUTED).pack(side="left", padx=6)
        reset = ctk.CTkButton(row, text="", width=28, height=24,
                              command=lambda: _reset())
        reset.pack(side="left", padx=4)

        def _reset():
            var.set(bool(baseline))
            _set()

        reset.configure(
            text="\u21ba" if not close(var.get(), baseline) else "",
            state="normal" if not close(var.get(), baseline) else "disabled",
        )

    def _choice(self, body, stage, spec, row_index) -> None:
        base = (self.baseline or {}).get("params", {}).get(spec.key, spec.default)
        labels = tuple(spec.choices)
        row = ChoiceRow(
            body, spec.label, labels, str(stage.params[spec.key]),
            lambda label, spec=spec: self._pick_plain(stage, spec, label),
        )
        row.set_baseline(str(base))
        row.grid(row=row_index, column=0, sticky="ew", pady=2)
        self._rows.append(row)

    def _choice_changed(self) -> None:
        if self.on_choice is not None:
            self.on_choice()

    def _pick_plain(self, stage, spec, label: str) -> str:
        stage.params[spec.key] = label
        self._choice_changed()
        self.on_edit()
        return label

    def _pick_mode(self, stage, label: str) -> str:
        if label.startswith("Neural (install"):
            return _mode_label(stage.params["mode"])
        stage.params["mode"] = "deepfilter" if label == "Neural" else "spectral"
        self._choice_changed()
        self.on_edit()
        return _mode_label(stage.params["mode"])

    def _pick_shape(self, stage, label: str) -> str:
        stage.params["air_shape"] = "shelf" if label == "Shelf" else "peak"
        self._choice_changed()
        self.on_edit()
        return _shape_label(stage.params["air_shape"])


def _find(stage, key: str):
    for item in stage.spec:
        if item.key == key:
            return item
    raise KeyError(key)


def _mode_labels(stage) -> list[str]:
    allowed = stage.enabled_choices("mode")
    if allowed is None or "deepfilter" in allowed:
        return ["Spectral", "Neural"]
    return ["Spectral", "Neural (install extra)"]


def _mode_label(value) -> str:
    return "Neural" if value == "deepfilter" else "Spectral"


def _shape_label(value) -> str:
    return "Shelf" if value == "shelf" else "Peak"


def _install_note(master) -> tk.Frame:
    box = tk.Frame(master, background=palette.Q_PANEL,
                   highlightbackground=palette.Q_BORDER, highlightthickness=1)
    ctk.CTkLabel(
        box,
        text="Neural enhance needs resemble-enhance. Its only release requires "
             "torch 2.1.1, which has no Python 3.12 wheel, so this stage "
             "cannot be installed here. ./runme.sh still installs DeepFilterNet "
             "for neural denoise.",
        text_color=palette.Q_MUTED, wraplength=420, justify="left",
    ).pack(anchor="w", padx=8, pady=(8, 4))
    ctk.CTkButton(box, text="Install neural extra\u2026", command=_show_install).pack(
        anchor="w", padx=8, pady=(0, 8))
    return box


def _show_install() -> None:
    messagebox.showinfo(
        "Neural extra",
        "From the chapterfit directory, run:\n\n" + _INSTALL,
    )
