"""CustomTkinter appearance and the themed ttk leftovers (trees, sashes)."""
from __future__ import annotations

import contextlib
import importlib.util
import json
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk

from . import palette

THEME_PATH = Path(__file__).with_name("theme.json")
APPEARANCE_MODES = ("Light", "Dark", "System")

_CTK_CONTROL = frozenset({
    "CTkButton", "CTkCheckBox", "CTkSwitch", "CTkRadioButton",
    "CTkSegmentedButton", "CTkLabel", "CTkEntry", "CTkSlider",
})


def customtkinter_available() -> bool:
    return importlib.util.find_spec("customtkinter") is not None


def require_customtkinter():
    """Import customtkinter or raise SystemExit with an install hint."""
    try:
        import customtkinter as ctk
    except ImportError as exc:
        raise SystemExit(
            "chaptergui needs CustomTkinter. Install with: "
            "pip install 'chaptertools[gui]'"
        ) from exc
    return ctk


def ctk_frame(master, **kwargs):
    """A layout frame that shrink-wraps (CTkFrame defaults to 200×200)."""
    ctk = require_customtkinter()
    kwargs.setdefault("fg_color", "transparent")
    kwargs.setdefault("corner_radius", 0)
    kwargs.setdefault("width", 1)
    kwargs.setdefault("height", 1)
    return ctk.CTkFrame(master, **kwargs)


def equalize_segments(seg, *, width: int | None = None) -> None:
    """Give each CTkSegmentedButton column the same share of the width."""
    values = list(seg.cget("values") or ())
    n = len(values)
    if n == 0:
        return
    if width is not None:
        with contextlib.suppress(tk.TclError):
            seg.configure(width=width, dynamic_resizing=False)
    for i in range(n):
        seg.grid_columnconfigure(i, weight=1, uniform="seg")


def apply_appearance(mode: str = "Light") -> None:
    """Load the chaptertools theme and set Light / Dark / System."""
    ctk = require_customtkinter()
    if mode not in APPEARANCE_MODES:
        mode = "Light"
    ctk.set_appearance_mode(mode)
    ctk.set_default_color_theme(str(THEME_PATH))
    ctk.set_widget_scaling(1.0)


def restyle_ttk(root: tk.Misc) -> None:
    """Point Treeview, headings, and paned sashes at the active palette."""
    style = ttk.Style(root)
    style.theme_use("clam")
    dark = _is_dark()
    window = palette.D_WINDOW if dark else palette.WINDOW
    field = palette.D_FIELD if dark else palette.FIELD
    text = palette.D_TEXT if dark else palette.TEXT
    muted = palette.D_MUTED if dark else palette.MUTED
    border = palette.D_BORDER if dark else palette.BORDER
    select = palette.D_SELECT if dark else palette.Q_SELECT
    row_h = tkfont.nametofont("TkDefaultFont", root=root).metrics("linespace") + 6
    style.configure(".", background=window, foreground=text,
                    fieldbackground=field, troughcolor=border)
    style.configure("TFrame", background=window)
    style.configure("TLabel", background=window, foreground=text)
    style.configure("TButton", padding=(8, 1))
    style.configure("TCheckbutton", background=window, foreground=text)
    style.configure("TRadiobutton", background=window, foreground=text)
    style.configure("TLabelframe", background=window, foreground=text)
    style.configure("TLabelframe.Label", background=window, foreground=text)
    style.configure("TMenubutton", background=window, foreground=text)
    style.configure("TPanedwindow", background=window)
    style.configure("Sash", sashthickness=6, background=border)
    style.configure("Treeview", background=field, fieldbackground=field,
                    foreground=text, rowheight=row_h, borderwidth=0)
    style.configure("Treeview.Heading", background=window, foreground=muted,
                    relief="flat")
    style.map("Treeview",
              background=[("selected", select)],
              foreground=[("selected", text)])
    style.configure("Chapter.Treeview", background=field,
                    fieldbackground=field, foreground=text, rowheight=row_h)
    style.configure("TCombobox", fieldbackground=field, foreground=text,
                    background=window)
    style.configure("TSpinbox", fieldbackground=field, foreground=text)
    style.configure("TScrollbar", background=window, troughcolor=border)
    style.configure("TSeparator", background=border)
    style.configure("RailGap.TSeparator", background=border)
    style.configure("Library.TButton", padding=(2, 2))
    style.configure("Accent.TButton", background=palette.Q_ACCENT,
                    foreground=palette.Q_ON_ACCENT, padding=(8, 1))
    style.map("Accent.TButton",
              background=[("pressed", "#1d467f"), ("active", "#3a6fba"),
                          ("disabled", "#8EA0BE")],
              foreground=[("disabled", palette.Q_ON_ACCENT)])


def _is_dark() -> bool:
    try:
        import customtkinter as ctk
        return ctk.get_appearance_mode() == "Dark"
    except Exception:
        return False


def is_ctk(widget) -> bool:
    return type(widget).__module__.startswith("customtkinter")


def ctk_kind(widget) -> str:
    return type(widget).__name__ if is_ctk(widget) else ""


def ctk_control(widget):
    """Nearest CustomTkinter control under a click (canvas children included)."""
    current = widget
    for _ in range(8):
        if current is None:
            return None
        kind = type(current).__name__
        if is_ctk(current) and kind in _CTK_CONTROL:
            return current
        current = getattr(current, "master", None)
    return None


def widget_text(widget) -> str:
    kind = ctk_kind(widget)
    if kind == "CTkSegmentedButton":
        try:
            return str(widget.get() or "").strip()
        except (tk.TclError, AttributeError, ValueError):
            return ""
    try:
        return str(widget.cget("text") or "").strip()
    except (tk.TclError, ValueError):
        return ""


_BTN_DISABLED_FG = (palette.BTN_DISABLED_FACE, palette.BTN_DISABLED_FACE_DARK)
_BTN_DISABLED_TEXT = (palette.BTN_ON_DISABLED, palette.BTN_DISABLED_TEXT)


def set_enabled(widget, enabled: bool) -> None:
    """Enable or disable ttk and CustomTkinter controls.

    CTk keeps the full button fill when disabled; dim the fill so
    ``text_color_disabled`` stays readable.
    """
    state = "normal" if enabled else "disabled"
    if is_ctk(widget):
        with contextlib.suppress(tk.TclError):
            if ctk_kind(widget) == "CTkButton":
                _set_button_enabled(widget, enabled)
            else:
                widget.configure(state=state)
        return
    try:
        widget.state(["!disabled"] if enabled else ["disabled"])
        return
    except (tk.TclError, AttributeError):
        with contextlib.suppress(tk.TclError):
            widget.configure(state=state)


def _set_button_enabled(widget, enabled: bool) -> None:
    state = "normal" if enabled else "disabled"
    if enabled:
        saved = getattr(widget, "_ctk_enabled_fg", None)
        if saved is not None:
            widget.configure(state=state, fg_color=saved)
            widget._ctk_enabled_fg = None  # noqa: SLF001
        else:
            widget.configure(state=state)
        return
    current = widget.cget("fg_color")
    if not _is_disabled_button_fg(current):
        widget._ctk_enabled_fg = current  # noqa: SLF001
    widget.configure(state=state, fg_color=_BTN_DISABLED_FG)


def _is_disabled_button_fg(color) -> bool:
    if isinstance(color, (list, tuple)) and len(color) >= 2:
        return (
            str(color[0]).lower() == palette.BTN_DISABLED_FACE.lower()
            and str(color[1]).lower() == palette.BTN_DISABLED_FACE_DARK.lower()
        )
    return str(color).lower() in {
        palette.BTN_DISABLED_FACE.lower(),
        palette.BTN_DISABLED_FACE_DARK.lower(),
    }


def is_disabled(widget) -> bool:
    if is_ctk(widget):
        try:
            return str(widget.cget("state")) == "disabled"
        except tk.TclError:
            return False
    try:
        return bool(widget.instate(["disabled"]))
    except (tk.TclError, AttributeError):
        pass
    try:
        return "disabled" in str(widget.cget("state"))
    except tk.TclError:
        return False


def accent_colors(on: bool) -> dict:
    """fg/hover for a primary action button."""
    disabled = {
        "text_color_disabled": _BTN_DISABLED_TEXT,
    }
    if on:
        return {
            "fg_color": palette.Q_ACCENT,
            "hover_color": "#1d467f",
            "text_color": palette.Q_ON_ACCENT,
            **disabled,
        }
    return {
        "fg_color": (palette.BTN_FACE, palette.BTN_FACE_DARK),
        "hover_color": ("#3d5a72", "#2e455a"),
        "text_color": ("#FFFFFF", "#FFFFFF"),
        **disabled,
    }


def load_gui_prefs() -> dict:
    path = _prefs_path()
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_gui_prefs(**updates) -> None:
    path = _prefs_path()
    data = load_gui_prefs()
    data.update(updates)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
    except OSError:
        pass


def _prefs_path():
    from chaptertools.audio_quality.presets import config_dir
    return config_dir() / "gui.json"
