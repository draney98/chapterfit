"""The log, opened from the player bar instead of a notebook tab."""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import filedialog

from chaptertools import file_log

from . import tips
from .theme import ctk_frame, require_customtkinter

ctk = require_customtkinter()


class LogDrawer:
    def __init__(self, app):
        self.app = app
        self.win = ctk.CTkToplevel(app)
        self.win.title("Log")
        self.win.geometry("720x320")
        self.win.withdraw()
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        bar = ctk_frame(self.win, fg_color="transparent")
        bar.pack(side="top", fill="x", padx=4, pady=4)
        copy = ctk.CTkButton(bar, text="Copy all", width=90, height=28,
                             command=self.copy_all)
        copy.pack(side="right", padx=(0, 2))
        tips.tooltip(copy, "copy the whole log to the clipboard")
        clear = ctk.CTkButton(bar, text="Clear log", width=90, height=28,
                              command=self.clear)
        clear.pack(side="right", padx=(0, 8))
        tips.tooltip(clear, "empty the log")
        save = ctk.CTkButton(bar, text="Save log…", width=90, height=28,
                             command=self.save)
        save.pack(side="right", padx=(0, 8))
        tips.tooltip(save, "write the log to a file")
        from chaptertools.gui.ui_kit import font
        self.text = ctk.CTkTextbox(
            self.win, height=280, wrap="none",
            font=font(mono=True))
        self.text.pack(side="top", fill="both", expand=True, padx=4, pady=(0, 4))
        self.text.bind("<Key>", self._key)
        self.text.bind("<Button-2>", lambda e: "break")
        self.text.bind("<ButtonRelease-2>", lambda e: "break")
        menu = tk.Menu(self.text, tearoff=0)
        menu.add_command(label="Copy", command=self._copy_selection)
        menu.add_command(label="Select all", command=self._select_all)
        menu.add_command(label="Copy everything", command=self.copy_all)
        menu.add_command(label="Save log…", command=self.save)
        self._menu = menu
        self.text.bind("<Button-3>", self._popup_menu)
        self.visible = False

    def _popup_menu(self, event) -> None:
        """Right-click menu; always release the grab (tkinter safety)."""
        menu = self._menu
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def toggle(self) -> None:
        if self.visible:
            self.hide()
        else:
            self.show()

    def show(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self.visible = True

    def hide(self) -> None:
        self.win.withdraw()
        self.visible = False

    def _key(self, event):
        ctrl = event.state & 0x0004
        if ctrl and event.keysym in ("c", "C", "a", "A"):
            return None
        return "break"

    def append(self, line: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        text = f"{stamp} {line}\n"
        self.text.insert("end", text)
        file_log.write(text)
        end_index = self.text.index("end-1c")
        if int(str(end_index).split(".")[0]) > 5000:
            self.text.delete("1.0", "2.0")
        if not self.text.tag_ranges("sel"):
            self.text.see("end")

    def clear(self) -> None:
        self.text.delete("1.0", "end")
        self.app.log_errors = 0
        self.app.player_bar.set_log_errors(0)

    def copy_all(self) -> None:
        self.app.clipboard_clear()
        self.app.clipboard_append(self.text.get("1.0", "end-1c"))

    def _select_all(self) -> None:
        self.text.tag_add("sel", "1.0", "end")

    def _copy_selection(self) -> None:
        if self.text.tag_ranges("sel"):
            try:
                self.app.clipboard_clear()
                self.app.clipboard_append(self.text.get("sel.first", "sel.last"))
            except tk.TclError:
                pass

    def save(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.win, title="Save log",
            initialfile="chaptergui.log",
            filetypes=[("Log files", "*.log"), ("All files", "*")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.text.get("1.0", "end-1c"))
