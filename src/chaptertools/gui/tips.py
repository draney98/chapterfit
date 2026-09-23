"""Small delayed tooltip for any widget."""
from __future__ import annotations

import tkinter as tk


class Tip:
    """One widget's hover tooltip. Created by `tooltip()`; not directly."""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self._tipwin: tk.Toplevel | None = None
        self._after = None
        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self.hide)
        widget.bind("<ButtonPress>", self.hide)

    def _schedule(self, _event=None):
        self._after = self.widget.after(350, self._show)

    def _show(self):
        if self._tipwin is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 2
        self._tipwin = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(tw, text=self.text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, padx=4, pady=1,
                 font=("TkDefaultFont", 9)).pack()

    def hide(self, _event=None):
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None
        if self._tipwin is not None:
            self._tipwin.destroy()
            self._tipwin = None


def tooltip(widget: tk.Widget, text: str) -> Tip:
    """Attach a hover tooltip to a widget."""
    return Tip(widget, text)
