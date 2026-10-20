"""Stored speech, opened from Place instead of a workflow step."""

from __future__ import annotations

import tkinter as tk

from . import tips
from .projection import filter_transcript, transcript_line_time, transcript_text
from .theme import ctk_frame, require_customtkinter

ctk = require_customtkinter()

_EMPTY = "Nothing transcribed yet. Listen or Sweep writes them."


class TranscriptView:
    """Timestamped lines from heard.clips. A click plays that moment."""

    def __init__(self, workflow):
        self.workflow = workflow
        self.win = ctk.CTkToplevel(workflow.winfo_toplevel())
        self.win.title("Transcripts")
        self.win.geometry("860x480")
        self.win.withdraw()
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        self.pattern = tk.StringVar(value="")
        self.count = tk.StringVar(value=_EMPTY)
        bar = ctk_frame(self.win, fg_color="transparent")
        bar.pack(side="top", fill="x", padx=4, pady=4)
        copy = ctk.CTkButton(bar, text="Copy all", width=90, height=28,
                             command=self.copy_all)
        copy.pack(side="right", padx=(8, 2))
        tips.tooltip(copy, "copy the lines on screen to the clipboard")
        search = ctk.CTkEntry(
            bar, textvariable=self.pattern, width=180, height=28,
            placeholder_text="search")
        search.pack(side="left")
        tips.tooltip(search, "show only lines matching this regular expression")
        note = ctk.CTkLabel(bar, textvariable=self.count, anchor="w")
        note.pack(side="left", fill="x", expand=True, padx=8)
        from chaptertools.gui.theme import pair
        from chaptertools.gui.ui_kit import font
        self.text = ctk.CTkTextbox(
            self.win, height=420, wrap="none",
            fg_color=pair("field_bg"), text_color=pair("field_fg"),
            font=font(mono=True))
        self.text.pack(side="top", fill="both", expand=True, padx=4, pady=(0, 4))
        self.text.bind("<Key>", self._key)
        self.text.bind("<Button-1>", self._on_click)
        self.text.bind("<Button-2>", lambda e: "break")
        self.text.bind("<ButtonRelease-2>", lambda e: "break")
        menu = tk.Menu(self.text, tearoff=0)
        menu.add_command(label="Copy", command=self._copy_selection)
        menu.add_command(label="Select all", command=self._select_all)
        menu.add_command(label="Copy everything", command=self.copy_all)
        self._menu = menu
        self.text.bind("<Button-3>", self._popup_menu)
        self.pattern.trace_add("write", lambda *_: self.fill())
        self.visible = False
        self._filling = False

    def _popup_menu(self, event) -> None:
        """Right-click menu; always release the grab (tkinter safety)."""
        menu = self._menu
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def show(self) -> None:
        self.fill()
        self.win.deiconify()
        self.win.lift()
        self.visible = True

    def hide(self) -> None:
        self.win.withdraw()
        self.visible = False

    def fill(self) -> None:
        """Replace the body from the current analysis. Keeps the search."""
        if self._filling:
            return
        self._filling = True
        try:
            analysis = getattr(self.workflow, "analysis", None) or {}
            clips = (analysis.get("heard") or {}).get("clips") or {}
            n_clips = len(clips) if isinstance(clips, dict) else 0
            body = transcript_text(analysis)
            shown = filter_transcript(body, self.pattern.get())
            if shown.startswith("bad regex:"):
                self.count.set(shown)
                text = shown
            elif not body.strip():
                self.count.set(_EMPTY)
                text = ""
            else:
                lines = [ln for ln in shown.splitlines() if ln.strip()]
                total = [ln for ln in body.splitlines() if ln.strip()]
                if (self.pattern.get() or "").strip():
                    self.count.set(
                        f"{len(lines)} of {len(total)} lines from {n_clips} clips")
                else:
                    self.count.set(f"{len(lines)} lines from {n_clips} clips")
                text = shown
            self.text.delete("1.0", "end")
            if text:
                self.text.insert("1.0", text if text.endswith("\n") else text + "\n")
        finally:
            self._filling = False

    def _on_click(self, event):
        moment = self.moment_at(event.x, event.y)
        if moment is None:
            return None
        self.workflow.play_at_book_time(moment)
        return None

    def moment_at(self, x: int, y: int) -> float | None:
        """Book time of the line under a click, or None."""
        index = self.text.index(f"@{x},{y}")
        line = self.text.get(f"{index} linestart", f"{index} lineend")
        return transcript_line_time(line)

    def _key(self, event):
        ctrl = event.state & 0x0004
        if ctrl and event.keysym in ("c", "C", "a", "A"):
            return None
        return "break"

    def copy_all(self) -> None:
        root = self.workflow.winfo_toplevel()
        root.clipboard_clear()
        root.clipboard_append(self.text.get("1.0", "end-1c"))

    def _select_all(self) -> None:
        self.text.tag_add("sel", "1.0", "end")

    def _copy_selection(self) -> None:
        if not self.text.tag_ranges("sel"):
            return
        try:
            root = self.workflow.winfo_toplevel()
            root.clipboard_clear()
            root.clipboard_append(self.text.get("sel.first", "sel.last"))
        except tk.TclError:
            pass
