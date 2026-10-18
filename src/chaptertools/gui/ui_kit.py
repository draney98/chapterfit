"""Fonts, spacing, and widget builders. Colours come from the theme palette."""
from __future__ import annotations

from tkinter import ttk

import customtkinter as ctk


def _pick_families() -> tuple[str, str]:
    """Prefer Noto; fall back in one place only (never per widget)."""
    try:
        import subprocess
        listed = subprocess.check_output(
            ["fc-list", ":", "family"], text=True,
            stderr=subprocess.DEVNULL, timeout=2)
    except Exception:
        listed = ""
    names = {line.strip() for line in listed.splitlines() if line.strip()}
    if "Noto Sans" in names:
        mono = "Noto Sans Mono" if "Noto Sans Mono" in names else "DejaVu Sans Mono"
        return "Noto Sans", mono
    return "DejaVu Sans", "DejaVu Sans Mono"


FONT, MONO = _pick_families()

class _Colors:
    """ui_kit names for the active palette. No hex of its own."""

    _KEYS = {
        "bg": "bg",
        "panel": "surface",
        "sunken": "surface_alt",
        "border": "border",
        "text": "text",
        "muted": "text_muted",
        "disabled": "disabled",
        "accent": "accent",
        "accent_hover": "accent_hover",
        "accent_soft": "select_bg",
        "ok": "ok",
        "warn_bg": "warn_bg",
        "warn_fg": "warn",
        "warn_border": "warn_border",
        "bad_bg": "error_bg",
        "bad_fg": "error",
        "silence": "silence",
        "mark": "chapter",
        "white": "accent_text",
        "ok_bg": "ok_bg",
    }

    def __getitem__(self, key: str) -> str:
        from .theme import active
        return active()[self._KEYS[key]]


C = _Colors()
PAD = 8  # base spacing unit: use PAD, PAD*2, PAD//2 only


# CustomTkinter paints fonts at ~75% of the requested size onto the
# inner Tk label. Ask for enough that the drawn size matches `size`.
_CTK_DRAW = 0.75

# Nominal sizes. Nothing is smaller than SMALL or larger than TITLE.
SMALL, BODY, TITLE = 13, 14, 16


def ctk_px(size: int = BODY) -> int:
    """Pixel size CustomTkinter must be asked for to draw `size`."""
    return max(size, int(round(size / _CTK_DRAW)))


def fmt_int(value: int) -> str:
    """Counts use a comma thousands separator: 4549 → 4,549."""
    return f"{int(value):,}"


def font(size=BODY, weight="normal", mono=False, underline=False):
    return ctk.CTkFont(
        family=MONO if mono else FONT, size=ctk_px(size), weight=weight,
        underline=underline)


# type scale: title 16 bold, section/small 13, body 14
def title(parent, text):
    return ctk.CTkLabel(
        parent, text=text, font=font(TITLE, "bold"), text_color=C["text"])


def section(parent, text):
    return ctk.CTkLabel(
        parent, text=text.upper(), font=font(SMALL, "bold"), text_color=C["muted"])


def body(parent, text="", **kw):
    return ctk.CTkLabel(
        parent, text=text, font=font(BODY), text_color=C["text"],
        justify="left", anchor="w", **kw)


def small(parent, text="", **kw):
    return ctk.CTkLabel(
        parent, text=text, font=font(SMALL), text_color=C["muted"],
        justify="left", anchor="w", **kw)


def number(parent, text="", **kw):
    return ctk.CTkLabel(
        parent, text=text, font=font(BODY, mono=True), text_color=C["text"],
        anchor="e", **kw)


def primary(parent, text, command=None, **kw):
    return ctk.CTkButton(
        parent, text=text, command=command, height=32, corner_radius=4,
        font=font(BODY, "bold"), fg_color=C["accent"],
        hover_color=C["accent_hover"], text_color=C["white"], **kw)


def secondary(parent, text, command=None, **kw):
    return ctk.CTkButton(
        parent, text=text, command=command, height=30, corner_radius=4,
        font=font(BODY), fg_color=C["panel"], hover_color=C["sunken"],
        text_color=C["text"], border_width=1, border_color=C["border"], **kw)


def post_menu(menu, anchor) -> None:
    """Open a menu under a widget and always drop its grab."""
    x = anchor.winfo_rootx()
    y = anchor.winfo_rooty() + anchor.winfo_height()
    try:
        menu.tk_popup(x, y)
    finally:
        menu.grab_release()


def text_button(parent, text, command=None, **kw):
    """e.g. reset glyph, Edit in Match."""
    kw.setdefault("width", 10)
    kw.setdefault("height", 26)
    return ctk.CTkButton(
        parent, text=text, command=command, corner_radius=4,
        font=font(SMALL), fg_color="transparent",
        hover_color=C["sunken"], text_color=C["accent"], **kw)


def panel(parent, **kw):
    return ctk.CTkFrame(
        parent, fg_color=C["panel"], corner_radius=4, border_width=1,
        border_color=C["border"], **kw)


def banner(parent, kind="warn"):
    """warn | bad | ok"""
    bg, fg = {
        "warn": (C["warn_bg"], C["warn_fg"]),
        "bad": (C["bad_bg"], C["bad_fg"]),
        "ok": (C["ok_bg"], C["ok"]),
    }[kind]
    return ctk.CTkFrame(
        parent, fg_color=bg, corner_radius=4, border_width=1, border_color=fg)


def checkbox(parent, text, variable=None, command=None):
    return ctk.CTkCheckBox(
        parent, text=text, variable=variable, command=command, font=font(BODY),
        checkbox_width=18, checkbox_height=18, corner_radius=3, border_width=1,
        fg_color=C["accent"], border_color=C["border"], text_color=C["text"])


def vscroll(parent, widget, **grid):
    """A themed scrollbar that hides when the widget's content fits."""
    bar = ctk.CTkScrollbar(parent, orientation="vertical", command=widget.yview)
    state = {"busy": False, "shown": False}

    def _set(first, last):
        bar.set(first, last)
        if state["busy"]:
            return
        fits = float(first) <= 0.0 and float(last) >= 0.999
        show = not fits
        if show == state["shown"]:
            return
        state["busy"] = True
        state["shown"] = show
        try:
            if show:
                bar.grid(**grid)
            else:
                bar.grid_remove()
        finally:
            state["busy"] = False

    widget.configure(yscrollcommand=_set)
    bar.grid(**grid)
    bar.grid_remove()
    return bar


def style_treeview(root):
    s = ttk.Style(root)
    s.theme_use("clam")
    s.configure(
        "Treeview", font=(FONT, BODY), rowheight=28, background=C["panel"],
        fieldbackground=C["panel"], foreground=C["text"], borderwidth=0)
    s.configure(
        "Treeview.Heading", font=(FONT, SMALL, "bold"), foreground=C["muted"],
        background=C["sunken"], relief="flat", padding=(PAD, 4))
    s.map(
        "Treeview",
        background=[("selected", C["accent"])],
        foreground=[("selected", C["white"])])
    # tags used by every table:
    #   tree.tag_configure("bad",  background=C["bad_bg"])
    #   tree.tag_configure("warn", background=C["warn_bg"])
    #   tree.tag_configure("grey", foreground=C["disabled"])
