"""Preset row and one chain row per stage, in processing order."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from chaptertools.gui import palette
from chaptertools.gui.theme import require_customtkinter
from chaptertools.gui.ui_kit import BODY, font

from .summary import stage_summary

ctk = require_customtkinter()


class ChainPanel(ctk.CTkFrame):
    """The left column. Arrow keys move the selection; Space toggles it."""

    def __init__(self, master, chain, enabled_vars, on_select,
                 on_preset, on_save, on_restore, on_rename, on_delete):
        super().__init__(master, fg_color=palette.Q_PANEL, corner_radius=0)
        self.chain = chain
        self.on_select = on_select
        self.on_preset = on_preset
        self._selected = 0
        self.columnconfigure(0, weight=1)

        presets = ctk.CTkFrame(self, fg_color=palette.Q_PANEL, corner_radius=0)
        presets.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))
        presets.columnconfigure(0, weight=1)
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(
            presets, textvariable=self.preset_var, width=18, state="readonly")
        self.preset_box.grid(row=0, column=0, sticky="ew")
        self.preset_box.bind("<<ComboboxSelected>>", lambda _e: on_preset(self.preset_var.get()))
        from chaptertools.gui.ui_kit import post_menu, secondary
        secondary(presets, "Save as…", command=on_save, width=90).grid(
            row=0, column=1, padx=4)
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Restore defaults", command=on_restore)
        self.menu.add_command(label="Rename", command=on_rename)
        self.menu.add_command(label="Delete", command=on_delete)
        self.menu.configure(postcommand=self._menu_state)
        more = secondary(presets, "More ▾", width=90)
        more.configure(command=lambda: post_menu(self.menu, more))
        more.grid(row=0, column=2)

        from chaptertools.gui.ui_kit import section, wrap_to_parent
        section(self, "Signal chain").grid(
            row=1, column=0, sticky="w", padx=4, pady=(8, 0))
        self.status_var = tk.StringVar(value="")
        self._status = ctk.CTkLabel(self, textvariable=self.status_var,
                     text_color=palette.Q_MUTED,
                     justify="left", anchor="w", text="")
        wrap_to_parent(self._status)

        self.inner = ctk.CTkFrame(self, fg_color=palette.Q_PANEL, corner_radius=0)
        self.inner.grid(row=3, column=0, sticky="ew", padx=4, pady=(0, 4))
        self.inner.columnconfigure(0, weight=1)

        self.rows = []
        for index, stage in enumerate(chain.stages):
            row = ChainRow(self.inner, index, stage, enabled_vars[stage.name],
                           self._click, keys=self)
            row.frame.grid(row=index, column=0, sticky="ew", pady=1)
            self.rows.append(row)
        self.refresh()
        self._paint_selection()

    def refresh(self) -> None:
        for row in self.rows:
            row.refresh(stage_summary(row.stage))

    def set_status(self, text: str) -> None:
        self.status_var.set(text)
        if text:
            self._status.grid(row=2, column=0, sticky="ew", padx=4, pady=(2, 0))
        else:
            self._status.grid_remove()

    def set_preset_names(self, names, current: str) -> None:
        self.preset_box.configure(values=names)
        if current not in names and names:
            current = names[0]
        self.preset_var.set(current or "")

    def mark(self, name: str, modified: bool) -> None:
        for row in self.rows:
            if row.stage.name == name:
                row.set_modified(modified)

    def select(self, index: int) -> None:
        self._selected = max(0, min(index, len(self.rows) - 1))
        self._paint_selection()
        frame = self.rows[self._selected].frame
        frame.focus_set()

    def move(self, delta: int) -> None:
        self.select(self._selected + delta)
        self.on_select(self._selected)

    def toggle_selected(self) -> None:
        row = self.rows[self._selected]
        if str(row.check.cget("state")) == "disabled":
            return
        row.enabled.set(not row.enabled.get())

    def _click(self, index: int) -> None:
        self.select(index)
        self.on_select(index)

    def _paint_selection(self) -> None:
        for row in self.rows:
            row.set_selected(row.index == self._selected)

    def _menu_state(self) -> None:
        from chaptertools.audio_quality.presets import load_user
        state = "normal" if self.preset_var.get() in load_user() else "disabled"
        self.menu.entryconfigure("Rename", state=state)
        self.menu.entryconfigure("Delete", state=state)

    def _keys(self, event):
        if event.keysym == "Up":
            self.move(-1)
        elif event.keysym == "Down":
            self.move(1)
        elif event.keysym == "space":
            self.toggle_selected()
        return "break"


class ChainRow:
    """Index, name, summary, modified dot, and the Enabled checkbox."""

    def __init__(self, master, index: int, stage, enabled_var, on_click, keys=None):
        self.index = index
        self.stage = stage
        self.enabled = enabled_var
        self.on_click = on_click
        keys = keys or master
        self._selected = False
        self._modified = False
        self.frame = ctk.CTkFrame(master, fg_color=palette.Q_PANEL, corner_radius=4)
        self.frame.columnconfigure(2, weight=1)
        self.index_label = ctk.CTkLabel(
            self.frame, text=str(index + 1), width=28,
            text_color=palette.Q_MUTED, anchor="w")
        self.index_label.grid(row=0, column=0, sticky="w", padx=(4, 0))
        from chaptertools.gui.ui_kit import BODY, C, font
        self.name_label = ctk.CTkLabel(
            self.frame, text=stage.title, text_color=palette.Q_TEXT, anchor="w",
            font=font(BODY))
        self.name_label.grid(row=0, column=1, sticky="w")
        self.summary_var = tk.StringVar(value="")
        self.summary = ctk.CTkLabel(
            self.frame, textvariable=self.summary_var,
            text_color=palette.Q_MUTED, font=font(BODY),
            anchor="e")
        self.summary.grid(row=0, column=2, sticky="ew", padx=4)
        self.dot = tk.Canvas(self.frame, width=10, height=16, highlightthickness=0,
                             background=palette.Q_PANEL)
        self.dot.grid(row=0, column=3)
        ok, _reason = stage.availability()
        self.check = ctk.CTkCheckBox(
            self.frame, text="", variable=enabled_var, width=24,
            command=lambda: None,
        )
        self.check.grid(row=0, column=4, sticky="e", padx=(0, 4))
        if not ok:
            self.check.configure(state="disabled")
            self.check.grid_remove()
            self.name_label.configure(text_color=C["disabled"])
            self.summary_var.set("Not installed")
        for widget in (self.frame, self.index_label, self.name_label, self.summary):
            widget.bind("<Button-1>", self._click)
            widget.bind("<Up>", keys._keys)
            widget.bind("<Down>", keys._keys)
            widget.bind("<space>", keys._keys)
        self.check.bind("<Up>", keys._keys)
        self.check.bind("<Down>", keys._keys)

    def refresh(self, summary: str) -> None:
        if not self.stage.availability()[0]:
            summary = "Not installed"
            self.check.grid_remove()
        self.summary_var.set(summary)
        from chaptertools.gui.ui_kit import BODY, font
        mono = any(ch.isdigit() for ch in summary)
        self.summary.configure(font=font(BODY, mono=mono))
        self._paint()

    def set_modified(self, modified: bool) -> None:
        self._modified = modified
        self._paint()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._paint()

    def _click(self, _event=None):
        self.frame.focus_set()
        self.on_click(self.index)

    def _paint(self) -> None:
        selected = self._selected
        ground = palette.Q_SELECT if selected else palette.Q_PANEL
        muted = palette.Q_MUTED
        text = palette.Q_TEXT
        if not self.stage.availability()[0]:
            from chaptertools.gui.ui_kit import C
            text = C["disabled"]
        self.frame.configure(fg_color=ground)
        self.index_label.configure(text_color=muted, fg_color="transparent")
        self.name_label.configure(text_color=text, fg_color="transparent")
        self.summary.configure(text_color=muted, fg_color="transparent")
        self.dot.configure(background=ground)
        self.dot.delete("all")
        if self._modified:
            self.dot.create_oval(1, 5, 8, 12, fill=palette.Q_ACCENT, outline="")
