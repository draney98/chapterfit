"""Preset row and one chain row per stage, in processing order."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from chaptertools.gui import palette

from .summary import stage_summary


class ChainPanel(ttk.Frame):
    """The left column. Arrow keys move the selection; Space toggles it."""

    def __init__(self, master, chain, enabled_vars, on_select,
                 on_preset, on_save, on_restore, on_rename, on_delete):
        super().__init__(master, style="Panel.TFrame", padding=4)
        self.chain = chain
        self.on_select = on_select
        self.on_preset = on_preset
        self._selected = 0
        self.columnconfigure(0, weight=1)

        presets = ttk.Frame(self, style="Panel.TFrame")
        presets.grid(row=0, column=0, sticky="ew")
        presets.columnconfigure(0, weight=1)
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(
            presets, textvariable=self.preset_var, width=18, state="readonly")
        self.preset_box.grid(row=0, column=0, sticky="ew")
        self.preset_box.bind("<<ComboboxSelected>>", lambda _e: on_preset(self.preset_var.get()))
        ttk.Button(presets, text="Save as\u2026", command=on_save).grid(
            row=0, column=1, padx=4)
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Restore defaults", command=on_restore)
        self.menu.add_command(label="Rename", command=on_rename)
        self.menu.add_command(label="Delete", command=on_delete)
        self.menu.configure(postcommand=self._menu_state)
        ttk.Menubutton(presets, text="\u22ef", menu=self.menu, width=3).grid(
            row=0, column=2)
        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel",
                  wraplength=300, justify="left").grid(row=1, column=0, sticky="w", pady=(2, 4))
        ttk.Label(self, text="Signal chain", style="Muted.TLabel").grid(
            row=2, column=0, sticky="w")

        host = ttk.Frame(self, style="Panel.TFrame")
        host.grid(row=3, column=0, sticky="nsew")
        self.rowconfigure(3, weight=1)
        self.canvas = tk.Canvas(
            host, height=80, highlightthickness=0, background=palette.Q_PANEL)
        scroll = ttk.Scrollbar(host, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        host.rowconfigure(0, weight=1)
        host.columnconfigure(0, weight=1)
        self.inner = ttk.Frame(self.canvas, style="Panel.TFrame")
        self.inner.columnconfigure(0, weight=1)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._fit_scroll)
        self.canvas.bind("<Configure>", self._fit_width)

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

    def set_preset_names(self, names, current: str) -> None:
        self.preset_box.configure(values=names)
        self.preset_var.set(current)

    def mark(self, name: str, modified: bool) -> None:
        for row in self.rows:
            if row.stage.name == name:
                row.set_modified(modified)

    def select(self, index: int) -> None:
        self._selected = max(0, min(index, len(self.rows) - 1))
        self._paint_selection()
        frame = self.rows[self._selected].frame
        frame.focus_set()
        self.canvas.update_idletasks()
        height = max(self.inner.winfo_reqheight(), 1)
        self.canvas.yview_moveto(frame.winfo_y() / height)

    def _fit_scroll(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _fit_width(self, event) -> None:
        need = event.width
        for row in self.rows:
            need = max(need, row.frame.winfo_reqwidth())
        self.canvas.itemconfigure(self._window, width=need)
        if need > self.canvas.winfo_width():
            self.canvas.configure(width=need)

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
        self.frame = ttk.Frame(master, style="Panel.TFrame", padding=(2, 1))
        self.frame.columnconfigure(2, weight=1)
        self.frame.configure(takefocus=True)
        self.index_label = ttk.Label(self.frame, text=str(index + 1), width=3,
                                     style="ChainMuted.TLabel")
        self.index_label.grid(row=0, column=0, sticky="w")
        self.name_label = ttk.Label(self.frame, text=stage.title, style="Chain.TLabel")
        self.name_label.grid(row=0, column=1, sticky="w")
        self.summary_var = tk.StringVar(value="")
        self.summary = ttk.Label(self.frame, textvariable=self.summary_var,
                                 style="ChainMono.TLabel", anchor="e")
        self.summary.grid(row=0, column=2, sticky="ew", padx=4)
        self.dot = tk.Canvas(self.frame, width=10, height=16, highlightthickness=0,
                             background=palette.Q_PANEL)
        self.dot.grid(row=0, column=3)
        ok, _reason = stage.availability()
        self.check = ttk.Checkbutton(
            self.frame, variable=enabled_var, style="Chain.TCheckbutton",
            command=lambda: None,
        )
        self.check.grid(row=0, column=4, sticky="e")
        if not ok:
            self.check.configure(state="disabled")
        for widget in (self.frame, self.index_label, self.name_label, self.summary):
            widget.bind("<Button-1>", self._click)
            widget.bind("<Up>", keys._keys)
            widget.bind("<Down>", keys._keys)
            widget.bind("<space>", keys._keys)
        self.check.bind("<Up>", keys._keys)
        self.check.bind("<Down>", keys._keys)

    def refresh(self, summary: str) -> None:
        self.summary_var.set(summary)
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
        face = "ChainSelected.TFrame" if selected else "Panel.TFrame"
        name = "ChainSelected.TLabel" if selected else "Chain.TLabel"
        muted = "ChainSelectedMuted.TLabel" if selected else "ChainMuted.TLabel"
        mono = "ChainSelectedMono.TLabel" if selected else "ChainMono.TLabel"
        check = "ChainSelected.TCheckbutton" if selected else "Chain.TCheckbutton"
        ground = palette.Q_SELECT if selected else palette.Q_PANEL
        if self.stage.bypass or not self.stage.availability()[0]:
            name = muted
        self.frame.configure(style=face)
        self.index_label.configure(style=muted)
        self.name_label.configure(style=name)
        self.summary.configure(style=mono)
        self.check.configure(style=check)
        self.dot.configure(background=ground)
        self.dot.delete("all")
        if self._modified:
            self.dot.create_oval(1, 5, 8, 12, fill=palette.Q_ACCENT, outline="")
