"""Small delayed tooltip for any widget."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from chaptertools.gui import palette


class Tip:
    """One widget's hover tooltip. Created by `tooltip()`; not directly.

    `reason` overrides the text while set - disabled controls use it to
    say why they are disabled.
    """

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.reason: str | None = None
        self._tipwin: tk.Toplevel | None = None
        self._after = None
        # CustomTkinter SegmentedButton (and a few others) refuse bind().
        for target in (widget, *getattr(widget, "_buttons_dict", {}).values()):
            try:
                target.bind("<Enter>", self._schedule, add="+")
                target.bind("<Leave>", self.hide, add="+")
                target.bind("<ButtonPress>", self.hide, add="+")
            except (NotImplementedError, tk.TclError, AttributeError):
                continue

    @property
    def shown_text(self) -> str:
        if self.reason:
            return f"{self.reason}\n({self.text})" if self.text else self.reason
        return self.text

    def _schedule(self, _event=None):
        self._after = self.widget.after(350, self._show)

    def _show(self):
        self._after = None
        if self._tipwin is not None or not self.shown_text:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 2
        self._tipwin = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(background=palette.current("BORDER"))
        # Borderless tip: never take focus or a grab.
        ttk.Label(tw, text=self.shown_text, justify="left",
                  style="Tip.TLabel", wraplength=480,
                  font="TkDefaultFont", takefocus=0).pack(padx=1, pady=1)

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
