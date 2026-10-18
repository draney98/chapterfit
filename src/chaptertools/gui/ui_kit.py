"""Fonts, spacing, and widget builders. Colours come from the theme palette.

Sizes are real pixels. Tk treats a negative size as pixels, so every Tk
font is given a negative size. Nothing outside this module constructs a
font.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
import weakref
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
        "link": "link",
        "accent_soft": "select_bg",
        "ok": "ok",
        "warn_bg": "warn_bg",
        "warn_fg": "warn",
        "warn_border": "warn_border",
        "bad_bg": "error_bg",
        "bad_fg": "error",
        "silence": "silence",
        "silence_soft": "silence_soft",
        "silence_band": "silence_band",
        "mark": "chapter",
        "white": "accent_text",
        "ok_bg": "ok_bg",
    }

    def __getitem__(self, key: str) -> tuple[str, str]:
        """Both modes. CustomTkinter stores the pair and switches itself."""
        from .theme import pair
        return pair(self._KEYS[key])


C = _Colors()


def hex(key: str) -> str:
    """The colour for the mode on screen. For canvases, ttk, menus, and tags."""
    from .theme import active
    return active()[C._KEYS[key]]


def ground(parent):
    """Window ground. The first child of a pane or a scrollable frame."""
    from .theme import ctk_frame
    return ctk_frame(parent, fg_color=C["bg"], corner_radius=0)


def watch_scroll(frame) -> None:
    """Repaint a scrollable frame's tk canvas when the appearance changes."""
    from .theme import follow_theme

    def _paint(frame=frame):
        stored = frame.cget("fg_color")
        if stored in (None, "transparent"):
            stored = C["bg"]
        frame.configure(fg_color=stored)

    follow_theme(frame, _paint)
PAD = 8  # base spacing unit: use PAD, PAD*2, PAD//2 only


# Real pixels. Tk treats a negative size as pixels. Never pass a positive size.
# SMALL and TITLE track the View-menu base (base-2 and base+2).
SMALL, BODY, TITLE = 12, 14, 16
MONO_DELTA = -1          # mono looks larger at the same px, so it's drawn 1 px smaller
ROW_H = 26               # Treeview row height at BODY
CTRL_H = 28              # buttons, entries, option menus
TYPE_MIN, TYPE_MAX = 12, 18

_ctk_fonts: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_tk_fonts: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def set_type_base(base: int) -> int:
    """View menu. Body is `base` px; secondary and title sit 2 px either side."""
    global SMALL, BODY, TITLE
    base = max(TYPE_MIN, min(int(base), TYPE_MAX))
    SMALL, BODY, TITLE = base - 2, base, base + 2
    return base


def _px(size: int, mono: bool) -> int:
    return int(size) + (MONO_DELTA if mono else 0)


def font(size=None, weight="normal", mono=False, underline=False):
    """A cached CTk font. `size` is pixels (SMALL / BODY / TITLE).

    The cache is per Tk interpreter. Tests create and destroy roots;
    a font from a destroyed root must not be handed to the next one.
    """
    if size is None:
        size = BODY
    delta = int(size) - BODY
    key = (delta, weight, bool(mono), bool(underline))
    px = _px(int(size), mono)
    root = tk._default_root
    per = None
    if root is not None:
        try:
            if root.winfo_exists():
                per = _ctk_fonts.setdefault(root, {})
        except tk.TclError:
            per = None
    cached = per.get(key) if per is not None else None
    if cached is not None:
        try:
            cached.configure(size=px, weight=weight, underline=underline)
            return cached
        except tk.TclError:
            cached = None
    cached = ctk.CTkFont(
        family=MONO if mono else FONT, size=px, weight=weight,
        underline=underline)
    if per is not None:
        per[key] = cached
    return cached


def tk_font(root, size=None, weight="normal", mono=False) -> tkfont.Font:
    """For ttk styles and canvases. Cached per root so Tk never collects it."""
    if size is None:
        size = BODY
    top = root.winfo_toplevel()
    delta = int(size) - BODY
    key = (delta, weight, bool(mono))
    per = _tk_fonts.setdefault(top, {})
    px = _px(int(size), mono)
    cached = per.get(key)
    if cached is None:
        cached = tkfont.Font(
            root=top, family=MONO if mono else FONT, size=-px, weight=weight)
        per[key] = cached
    else:
        cached.configure(size=-px, weight=weight)
    return cached


def refresh_type(root) -> None:
    """Re-apply every cached font after the View menu changes the base."""
    top = root.winfo_toplevel()
    for (delta, weight, mono, underline), face in (
            _ctk_fonts.get(top) or {}).items():
        try:
            face.configure(
                size=_px(BODY + delta, mono), weight=weight,
                underline=underline)
        except tk.TclError:
            continue
    per = _tk_fonts.get(top) or {}
    for (delta, weight, mono), face in per.items():
        face.configure(size=-_px(BODY + delta, mono), weight=weight)


def apply_named_fonts(root, base: int) -> int:
    """Every predefined Tk font, in pixels. Heading is secondary bold; fixed is mono."""
    base = set_type_base(base)
    for name in tkfont.names(root):
        if not str(name).startswith("Tk"):
            continue
        try:
            face = tkfont.nametofont(name, root=root)
        except tk.TclError:
            continue
        if name == "TkHeadingFont":
            face.configure(family=FONT, size=-(base - 2), weight="bold")
        elif name == "TkFixedFont":
            face.configure(family=MONO, size=-(base - 1), weight="normal")
        else:
            face.configure(family=FONT, size=-base)
    refresh_type(root)
    return base


def linespace_of(spec) -> int | None:
    """Drawn line height in pixels, whatever created the font."""
    face = _font_for(spec)
    if face is None:
        return None
    try:
        return int(face.metrics("linespace"))
    except tk.TclError:
        return None


def text_width(spec, text: str) -> int:
    """Pixel width of `text` in `spec`."""
    face = _font_for(spec)
    if face is None:
        return 0
    try:
        return int(face.measure(str(text)))
    except tk.TclError:
        return 0


def _font_for(spec):
    if spec is None or spec == "":
        return None
    if hasattr(spec, "metrics"):
        return spec
    try:
        return tkfont.Font(font=spec)
    except tk.TclError:
        return None


def fmt_int(value: int) -> str:
    """Counts use a comma thousands separator: 4549 → 4,549."""
    return f"{int(value):,}"


def fmt_fit(score) -> str:
    """One decimal for an alignment fit score.

    The stored value is the trial score (a consistent edition is at
    least 5). It is not a 0–1 fraction or a percentage.
    """
    return f"{float(score):.1f}"


def ellipsize(text: str, measure, width: int) -> str:
    """End a string with … so it fits `width` pixels."""
    if not text or width <= 0 or measure(text) <= width:
        return text
    ell = "…"
    if measure(ell) > width:
        return ell
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(text[:mid] + ell) <= width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ell


def option_menu(parent, variable, values, **kw):
    """A standard-height menu that is not an accent button."""
    return ctk.CTkOptionMenu(
        parent, variable=variable, values=list(values),
        font=font(BODY), height=CTRL_H, fg_color=C["panel"], button_color=C["border"],
        button_hover_color=C["sunken"], text_color=C["text"],
        dropdown_fg_color=C["panel"], dropdown_text_color=C["text"],
        **kw)


def toggle(parent, text, variable=None, command=None, **kw):
    return ctk.CTkSwitch(
        parent, text=text, variable=variable, command=command,
        font=font(BODY), text_color=C["text"],
        fg_color=C["border"], progress_color=C["accent"],
        button_color=C["panel"], **kw)


# type scale: title 16 bold, section/small 12, body 14
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
    btn = ctk.CTkButton(
        parent, text=text, command=command, height=CTRL_H, corner_radius=4,
        font=font(BODY), fg_color=C["accent"],
        hover_color=C["accent_hover"], text_color=C["white"],
        text_color_disabled=C["disabled"], border_width=0, **kw)
    btn._kind = "primary"  # noqa: SLF001
    return btn


def secondary(parent, text, command=None, **kw):
    btn = ctk.CTkButton(
        parent, text=text, command=command, height=CTRL_H, corner_radius=4,
        font=font(BODY), fg_color=C["panel"], hover_color=C["sunken"],
        text_color=C["text"], text_color_disabled=C["disabled"],
        border_width=1, border_color=C["border"], **kw)
    btn._kind = "secondary"  # noqa: SLF001
    return btn


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
    kw.setdefault("height", CTRL_H)
    btn = ctk.CTkButton(
        parent, text=text, command=command, corner_radius=4,
        font=font(SMALL), fg_color="transparent",
        hover_color=C["sunken"], text_color=C["link"],
        text_color_disabled=C["disabled"], border_width=0, **kw)
    btn._kind = "text"  # noqa: SLF001
    return btn


def wrap_to_parent(label, pad=None):
    """Wrap a sentence to the width the layout actually gave the label."""
    del pad

    def _fit(_event=None):
        try:
            width = label.winfo_width()
        except tk.TclError:
            return
        if width > 40:
            label.configure(wraplength=width)

    label.bind("<Configure>", _fit, add="+")
    label.after_idle(_fit)
    return label


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


def checkbox(parent, text, variable=None, command=None, *, border_color=None):
    return ctk.CTkCheckBox(
        parent, text=text, variable=variable, command=command, font=font(BODY),
        checkbox_width=18, checkbox_height=18, corner_radius=3, border_width=1,
        fg_color=C["accent"],
        border_color=C["border"] if border_color is None else border_color,
        text_color=C["text"])


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
        "Treeview", font=tk_font(root), rowheight=ROW_H, background=hex("panel"),
        fieldbackground=hex("panel"), foreground=hex("text"), borderwidth=0)
    s.configure(
        "Treeview.Heading", font=tk_font(root, SMALL, "bold"),
        foreground=hex("muted"),
        background=hex("sunken"), relief="flat", padding=(PAD, 4))
    s.map(
        "Treeview",
        background=[("selected", hex("accent"))],
        foreground=[("selected", hex("white"))])
    # tags used by every table:
    #   tree.tag_configure("bad",  background=C["bad_bg"])
    #   tree.tag_configure("warn", background=C["warn_bg"])
    #   tree.tag_configure("grey", foreground=C["disabled"])
