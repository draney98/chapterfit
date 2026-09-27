"""ttk styles for the Quality tab. Colors come from the shared palette."""

from __future__ import annotations

import tkinter.font as tkfont
from tkinter import ttk

from chaptertools.gui import palette


def install(widget) -> None:
    """Define the quality styles for this Tk interpreter."""
    style = ttk.Style(widget)
    if style.lookup("Quality.TFrame", "background") == palette.Q_GROUND:
        return
    root = widget.winfo_toplevel()
    mono = tkfont.nametofont("TkFixedFont", root=root)
    ground, panel = palette.Q_GROUND, palette.Q_PANEL
    text, muted = palette.Q_TEXT, palette.Q_MUTED
    select = palette.Q_SELECT

    def label(name, background, foreground, font=None):
        options = {"background": background, "foreground": foreground}
        if font is not None:
            options["font"] = font
        style.configure(name, **options)

    style.configure("Quality.TFrame", background=ground)
    style.configure("Panel.TFrame", background=panel)
    style.configure("ChainSelected.TFrame", background=select)
    style.configure("Card.TFrame", background=panel, relief="solid",
                    borderwidth=1)
    style.configure("Warn.TFrame", background=palette.Q_WARN_BG)
    label("Ground.TLabel", ground, text)
    label("GroundMuted.TLabel", ground, muted)
    label("Panel.TLabel", panel, text)
    label("Muted.TLabel", panel, muted)
    label("Dim.TLabel", panel, muted)
    label("Mono.TLabel", panel, text, mono)
    label("Chain.TLabel", panel, text)
    label("ChainMuted.TLabel", panel, muted)
    label("ChainMono.TLabel", panel, muted, mono)
    label("ChainSelected.TLabel", select, text)
    label("ChainSelectedMuted.TLabel", select, muted)
    label("ChainSelectedMono.TLabel", select, muted, mono)
    label("Warn.TLabel", palette.Q_WARN_BG, palette.Q_WARN_TEXT)
    label("WarnMuted.TLabel", palette.Q_WARN_BG, palette.Q_WARN_TEXT)
    label("Metric.TLabel", panel, text, mono)
    label("MetricMuted.TLabel", panel, muted)

    style.configure("Chain.TCheckbutton", background=panel, foreground=text)
    style.map("Chain.TCheckbutton", background=[("active", panel)])
    style.configure("ChainSelected.TCheckbutton", background=select, foreground=text)
    style.map("ChainSelected.TCheckbutton",
              background=[("active", select), ("selected", select)])
    style.configure("Panel.TCheckbutton", background=panel, foreground=text)
    style.map("Panel.TCheckbutton", background=[("active", panel)])

    style.configure("Quality.Accent.TButton", background=palette.Q_ACCENT,
                    foreground=palette.Q_ON_ACCENT, padding=(8, 1))
    style.map("Quality.Accent.TButton",
              background=[("pressed", "#1d467f"), ("active", "#3a6fba"),
                          ("disabled", "#8EA0BE")],
              foreground=[("disabled", palette.Q_ON_ACCENT)])
    style.configure("Hear.Toolbutton", background=ground, foreground=text,
                    padding=(8, 1))
    style.map("Hear.Toolbutton",
              background=[("selected", palette.Q_WARN_BG), ("active", ground)],
              foreground=[("selected", palette.Q_WARN_TEXT)])
    style.configure("Changed.TSpinbox", fieldbackground=select)
    style.configure("Quality.Reset.TButton", padding=(1, 0))
