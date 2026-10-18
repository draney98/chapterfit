"""Colours, ttk/tk theme, and CustomTkinter appearance.

Hex literals live only in ``LIGHT`` and ``DARK``. Everything else reads
the active palette.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import subprocess
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import ttk

# Canonical keys, plus the semantic colours canvases and row tags draw.
# Text/background pairs are checked in tests/test_gui_legibility.py.
LIGHT = {
    "bg": "#ECEBE8",
    "surface": "#FFFFFF",
    "surface_alt": "#F4F3F0",
    "border": "#C9C5BD",
    "text": "#1C1B19",
    "text_muted": "#5E5A53",
    "accent": "#3E6A92",
    "accent_text": "#FFFFFF",
    "accent_hover": "#335A7D",
    "accent_muted": "#4A6274",
    "ok": "#1B5E20",
    "warn": "#6B4000",
    "error": "#8A1C12",
    "select_bg": "#E3EBF3",
    "select_fg": "#1C1B19",
    "field_bg": "#FFFFFF",
    "field_fg": "#1C1B19",
    "chapter": "#5B2E8C",
    "silence": "#3E5A78",
    "warn_bg": "#FFF4E0",
    "warn_border": "#C4924A",
    "ok_bg": "#E6F1E8",
    "error_bg": "#FBEDEB",
    "row_unplaced": "#b0b0b0",
    "row_lowconf": "#e8c680",
    "row_conflict": "#f4b4b4",
    "link": "#0B4A94",
    "tip": "#FFF8D6",
    "banner": "#FFF4E0",
    "blocked": "#5C5A55",
    "disabled": "#5E5A53",
    "disabled_fill": "#5A738C",
    "pin": "#5B2E8C",
    "gap": "#1A5F9E",
    "clip": "#9A3412",
    "noisy": "#EADFC8",
    "placeholder": "#5E5A53",
    "chrome": "#8A8680",
}
DARK = {
    "bg": "#1E1E1C",
    "surface": "#2A2A28",
    "surface_alt": "#343432",
    "border": "#4A4A46",
    "text": "#E8E6E1",
    "text_muted": "#B0AEA8",
    "accent": "#3E6A92",
    "accent_text": "#FFFFFF",
    "accent_hover": "#5A86AD",
    "accent_muted": "#2E4554",
    "ok": "#8FCB94",
    "warn": "#F0C080",
    "error": "#F0B4AE",
    "select_bg": "#243548",
    "select_fg": "#E8E6E1",
    "field_bg": "#2A2A28",
    "field_fg": "#E8E6E1",
    "chapter": "#D2B4EA",
    "silence": "#9DB3CC",
    "warn_bg": "#3D2E14",
    "warn_border": "#C4924A",
    "ok_bg": "#1E3324",
    "error_bg": "#3D2422",
    "row_unplaced": "#4a4a4a",
    "row_lowconf": "#6b5320",
    "row_conflict": "#6b3030",
    "link": "#9EC0E8",
    "tip": "#3D3A28",
    "banner": "#3D2E14",
    "blocked": "#B0AEA8",
    "disabled": "#B0AEA8",
    "disabled_fill": "#4A5A6A",
    "pin": "#D2B4EA",
    "gap": "#8EB4DC",
    "clip": "#F0B080",
    "noisy": "#4A4030",
    "placeholder": "#B0AEA8",
    "chrome": "#6E6C68",
}

APPEARANCE_MODES = ("Auto", "Light", "Dark")
_MODE = "Light"
_active = LIGHT


def active() -> dict:
    """The palette currently installed."""
    return _active


def pair(key: str) -> tuple[str, str]:
    """Light and dark values, for CustomTkinter widgets that store both."""
    return (LIGHT[key], DARK[key])


def text_pairs() -> dict[str, tuple[str, str]]:
    """Foreground/background pairs that carry text, both themes."""
    pairs = {}
    for name, colors in (("light", LIGHT), ("dark", DARK)):
        def add(label, fg, bg, colors=colors, name=name):
            pairs[f"{name}-{label}"] = (colors[fg], colors[bg])
        add("body", "text", "bg")
        add("muted", "text_muted", "bg")
        add("surface", "text", "surface")
        add("muted-surface", "text_muted", "surface")
        add("sunken", "text", "surface_alt")
        add("muted-sunken", "text_muted", "surface_alt")
        add("field", "field_fg", "field_bg")
        add("select", "select_fg", "select_bg")
        add("muted-select", "text_muted", "select_bg")
        add("ok", "ok", "bg")
        add("warn", "warn", "bg")
        add("error", "error", "bg")
        add("error-surface", "error", "surface")
        add("blocked", "blocked", "bg")
        add("link", "link", "bg")
        add("banner", "warn", "banner")
        add("tip", "text", "tip")
        add("warn-bg", "warn", "warn_bg")
        add("row", "text", "row_unplaced")
        add("row-low", "text", "row_lowconf")
        add("row-conflict", "text", "row_conflict")
        add("accent", "accent_text", "accent")
        add("accent-muted", "accent_text", "accent_muted")
        add("disabled-fill", "accent_text", "disabled_fill")
        add("placeholder", "placeholder", "field_bg")
    return pairs


def _luminance(color: str) -> float:
    c = color.lstrip("#")
    chans = [int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
           for v in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio, 1.0 (none) to 21.0 (black on white)."""
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def desktop_is_dark() -> bool:
    """True when the XFCE theme name or GNOME color-scheme says dark."""
    commands = (
        ["xfconf-query", "-c", "xsettings", "-p", "/Net/ThemeName"],
        ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
    )
    for cmd in commands:
        try:
            out = subprocess.check_output(
                cmd, text=True, stderr=subprocess.DEVNULL, timeout=2)
        except (OSError, subprocess.SubprocessError):
            continue
        if "dark" in out.lower():
            return True
    return False


def resolve_mode(choice: str | None) -> str:
    """Menu choice Auto/Light/Dark (System counts as Auto) -> Light or Dark."""
    if choice in (None, "", "Auto", "System"):
        return "Dark" if desktop_is_dark() else "Light"
    if choice == "Dark":
        return "Dark"
    return "Light"


def _use(resolved: str) -> None:
    global _MODE, _active
    _MODE = "Dark" if resolved == "Dark" else "Light"
    _active = DARK if _MODE == "Dark" else LIGHT


def mode() -> str:
    return _MODE


def _ctk_theme() -> dict:
    """CustomTkinter theme. Each colour is [light, dark] from the palette."""
    from .ui_kit import BODY, ctk_px

    def p(key: str):
        return [LIGHT[key], DARK[key]]

    _body_px = ctk_px(BODY)
    return {
        "CTk": {"fg_color": p("bg")},
        "CTkToplevel": {"fg_color": p("bg")},
        "CTkFrame": {
            "corner_radius": 8, "border_width": 0,
            "fg_color": p("bg"), "top_fg_color": p("surface_alt"),
            "border_color": p("border"),
        },
        "CTkButton": {
            "corner_radius": 8, "border_width": 0,
            "fg_color": p("accent"), "hover_color": p("accent_hover"),
            "border_color": p("border"), "text_color": p("accent_text"),
            "text_color_disabled": p("text"),
        },
        "CTkLabel": {
            "corner_radius": 0, "border_width": 0, "fg_color": "transparent",
            "border_color": p("border"), "text_color": p("text"),
        },
        "CTkEntry": {
            "corner_radius": 8, "border_width": 2,
            "fg_color": p("field_bg"), "border_color": p("border"),
            "text_color": p("field_fg"),
            "placeholder_text_color": p("placeholder"),
        },
        "CTkCheckBox": {
            "corner_radius": 6, "border_width": 2,
            "fg_color": p("accent"), "border_color": p("accent"),
            "hover_color": p("accent_hover"),
            "checkmark_color": p("accent_text"), "text_color": p("text"),
            "text_color_disabled": p("text_muted"),
        },
        "CTkSwitch": {
            "corner_radius": 1000, "border_width": 3, "button_length": 0,
            "fg_color": p("border"), "progress_color": p("accent"),
            "button_color": p("accent"), "button_hover_color": p("accent_hover"),
            "text_color": p("text"), "text_color_disabled": p("text_muted"),
        },
        "CTkRadioButton": {
            "corner_radius": 1000, "border_width_checked": 6,
            "border_width_unchecked": 3,
            "fg_color": p("accent"), "border_color": p("accent"),
            "hover_color": p("accent_hover"), "text_color": p("text"),
            "text_color_disabled": p("text_muted"),
        },
        "CTkProgressBar": {
            "corner_radius": 1000, "border_width": 0,
            "fg_color": p("border"), "progress_color": p("accent"),
            "border_color": p("border"),
        },
        "CTkSlider": {
            "corner_radius": 1000, "button_corner_radius": 1000,
            "border_width": 6, "button_length": 0,
            "fg_color": p("border"), "progress_color": p("accent"),
            "button_color": p("accent"), "button_hover_color": p("accent_hover"),
        },
        "CTkOptionMenu": {
            "corner_radius": 8, "fg_color": p("accent"),
            "button_color": p("accent_hover"),
            "button_hover_color": p("accent_hover"),
            "text_color": p("accent_text"),
            "text_color_disabled": p("text"),
        },
        "CTkComboBox": {
            "corner_radius": 8, "border_width": 2,
            "fg_color": p("field_bg"), "border_color": p("border"),
            "button_color": p("border"), "button_hover_color": p("chrome"),
            "text_color": p("field_fg"),
            "text_color_disabled": p("text_muted"),
        },
        "CTkScrollbar": {
            "corner_radius": 1000, "border_spacing": 4, "fg_color": "transparent",
            "button_color": p("chrome"), "button_hover_color": p("accent"),
        },
        "CTkSegmentedButton": {
            "corner_radius": 8, "border_width": 2,
            "fg_color": p("accent_muted"), "selected_color": p("accent"),
            "selected_hover_color": p("accent_hover"),
            "unselected_color": p("accent_muted"),
            "unselected_hover_color": p("accent"),
            "text_color": p("accent_text"),
            "text_color_disabled": p("text"),
        },
        "CTkTextbox": {
            "corner_radius": 8, "border_width": 0,
            "fg_color": p("field_bg"), "border_color": p("border"),
            "text_color": p("field_fg"),
            "scrollbar_button_color": p("chrome"),
            "scrollbar_button_hover_color": p("accent"),
        },
        "CTkScrollableFrame": {"label_fg_color": p("surface_alt")},
        "DropdownMenu": {
            "fg_color": p("field_bg"), "hover_color": p("select_bg"),
            "text_color": p("field_fg"),
        },
        "CTkFont": {
            "macOS": {"family": "SF Display", "size": _body_px, "weight": "normal"},
            "Windows": {"family": "Noto Sans", "size": _body_px, "weight": "normal"},
            "Linux": {"family": "Noto Sans", "size": _body_px, "weight": "normal"},
        },
    }


def _theme_path() -> Path:
    from chaptertools.audio_quality.presets import config_dir
    return config_dir() / "ctk_theme.json"


def _install_ctk_theme(resolved: str) -> None:
    ctk = require_customtkinter()
    path = _theme_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_ctk_theme()), encoding="utf-8")
    ctk.set_appearance_mode("Dark" if resolved == "Dark" else "Light")
    ctk.set_default_color_theme(str(path))
    ctk.set_widget_scaling(1.0)


def _style_ttk(root: tk.Misc) -> None:
    p = active()
    style = ttk.Style(root)
    style.theme_use("clam")
    row_h = tkfont.nametofont("TkDefaultFont", root=root).metrics("linespace") + 6
    style.configure(".", background=p["bg"], foreground=p["text"],
                    fieldbackground=p["field_bg"], troughcolor=p["border"],
                    bordercolor=p["border"], lightcolor=p["surface"],
                    darkcolor=p["border"], arrowcolor=p["text"])
    style.configure("TFrame", background=p["bg"])
    style.configure("TLabel", background=p["bg"], foreground=p["text"])
    style.configure("Tip.TLabel", background=p["tip"], foreground=p["text"],
                    padding=(6, 3))
    style.configure("TButton", background=p["surface"], foreground=p["text"],
                    bordercolor=p["border"], padding=(8, 1))
    style.map("TButton",
              background=[("active", p["surface_alt"]),
                          ("disabled", p["surface_alt"])],
              foreground=[("disabled", p["text_muted"])])
    style.configure("TEntry", fieldbackground=p["field_bg"],
                    foreground=p["field_fg"], bordercolor=p["border"],
                    lightcolor=p["surface"], darkcolor=p["border"])
    style.map("TEntry",
              fieldbackground=[("disabled", p["surface_alt"])],
              foreground=[("disabled", p["text_muted"])])
    style.configure("TCombobox", fieldbackground=p["field_bg"],
                    foreground=p["field_fg"], background=p["surface"],
                    bordercolor=p["border"], arrowcolor=p["text"],
                    arrowsize=12)
    style.map("TCombobox",
              fieldbackground=[("readonly", p["field_bg"]),
                               ("disabled", p["surface_alt"])],
              background=[("readonly", p["field_bg"]),
                          ("active", p["surface_alt"])],
              foreground=[("readonly", p["field_fg"]),
                          ("disabled", p["text_muted"])],
              arrowcolor=[("disabled", p["text_muted"]),
                          ("!disabled", p["text"])])
    style.configure("TMenubutton", background=p["surface"],
                    foreground=p["text"], bordercolor=p["border"],
                    arrowcolor=p["text"])
    style.map("TMenubutton",
              background=[("active", p["surface_alt"]),
                          ("disabled", p["surface_alt"])],
              foreground=[("disabled", p["text_muted"])])
    style.configure("Treeview", background=p["field_bg"],
                    fieldbackground=p["field_bg"], foreground=p["field_fg"],
                    bordercolor=p["border"], rowheight=row_h, borderwidth=0)
    style.configure("Treeview.Heading", background=p["surface_alt"],
                    foreground=p["text_muted"], relief="flat",
                    bordercolor=p["border"])
    style.map("Treeview",
              background=[("selected", p["select_bg"])],
              foreground=[("selected", p["select_fg"])])
    style.map("Treeview.Heading",
              background=[("active", p["surface"])],
              foreground=[("active", p["text"])])
    style.configure("Chapter.Treeview", background=p["field_bg"],
                    fieldbackground=p["field_bg"], foreground=p["field_fg"],
                    rowheight=30)
    style.configure("TScrollbar", background=p["surface_alt"],
                    troughcolor=p["bg"], bordercolor=p["border"],
                    arrowcolor=p["text"])
    style.map("TScrollbar",
              background=[("active", p["border"])])
    style.configure("TNotebook", background=p["bg"], bordercolor=p["border"])
    style.configure("TNotebook.Tab", background=p["surface_alt"],
                    foreground=p["text"], padding=(8, 4))
    style.map("TNotebook.Tab",
              background=[("selected", p["surface"])],
              foreground=[("selected", p["text"])])
    style.configure("TCheckbutton", background=p["bg"], foreground=p["text"])
    style.map("TCheckbutton",
              foreground=[("disabled", p["text_muted"])])
    style.configure("TProgressbar", background=p["accent"],
                    troughcolor=p["border"], bordercolor=p["border"])
    style.configure("TRadiobutton", background=p["bg"], foreground=p["text"])
    style.configure("TLabelframe", background=p["bg"], foreground=p["text"])
    style.configure("TLabelframe.Label", background=p["bg"],
                    foreground=p["text"])
    style.configure("TPanedwindow", background=p["bg"])
    style.configure("Sash", sashthickness=6, background=p["border"])
    style.configure("TSpinbox", fieldbackground=p["field_bg"],
                    foreground=p["field_fg"])
    style.configure("TSeparator", background=p["border"])
    style.configure("RailGap.TSeparator", background=p["border"])
    style.configure("Library.TButton", padding=(2, 2))
    style.configure("Accent.TButton", background=p["accent"],
                    foreground=p["accent_text"], padding=(8, 1))
    style.map("Accent.TButton",
              background=[("pressed", p["accent_hover"]),
                          ("active", p["accent_hover"]),
                          ("disabled", p["disabled_fill"])],
              foreground=[("disabled", p["accent_text"])])


def _option_add(root: tk.Misc) -> None:
    p = active()
    options = {
        "*Background": p["bg"],
        "*Foreground": p["text"],
        "*Canvas.Background": p["surface"],
        "*Listbox.background": p["field_bg"],
        "*Listbox.foreground": p["field_fg"],
        "*Listbox.selectBackground": p["select_bg"],
        "*Listbox.selectForeground": p["select_fg"],
        "*Text.background": p["field_bg"],
        "*Text.foreground": p["field_fg"],
        "*Text.selectBackground": p["select_bg"],
        "*Text.selectForeground": p["select_fg"],
        "*Entry.background": p["field_bg"],
        "*Entry.foreground": p["field_fg"],
        "*Entry.selectBackground": p["select_bg"],
        "*Entry.selectForeground": p["select_fg"],
        "*Menu.background": p["surface"],
        "*Menu.foreground": p["text"],
        "*Menu.activeBackground": p["select_bg"],
        "*Menu.activeForeground": p["select_fg"],
        "*selectBackground": p["select_bg"],
        "*selectForeground": p["select_fg"],
        "*TCombobox*Listbox.background": p["field_bg"],
        "*TCombobox*Listbox.foreground": p["field_fg"],
        "*TCombobox*Listbox.selectBackground": p["select_bg"],
        "*TCombobox*Listbox.selectForeground": p["select_fg"],
    }
    for pattern, value in options.items():
        root.option_add(pattern, value)


def apply_theme(root: tk.Misc | None = None, mode: str | None = None) -> str:
    """Install the palette.

    Call once before the CTk root is constructed, then again with that
    root before any child widget. Returns ``Light`` or ``Dark``.
    """
    choice = mode
    if choice is None:
        choice = load_gui_prefs().get("appearance", "Auto")
    resolved = resolve_mode(choice)
    _use(resolved)
    _install_ctk_theme(resolved)
    if root is not None:
        _style_ttk(root)
        _option_add(root)
    return resolved


def apply_appearance(mode: str = "Light") -> None:
    """Back-compat wrapper. Prefer apply_theme."""
    apply_theme(mode=mode)


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


def paint_segment_text(seg) -> None:
    """Dark text on every segment. The selected chip is not an accent fill."""
    from .ui_kit import C

    for _value, button in getattr(seg, "_buttons_dict", {}).items():
        button.configure(text_color=C["text"])
    if getattr(seg, "_segment_text_wrapped", False):
        return
    orig = seg.set

    def _set(value, *args, **kwargs):
        result = orig(value, *args, **kwargs)
        paint_segment_text(seg)
        return result

    seg.set = _set
    seg._segment_text_wrapped = True


def focus_clicked_widget(event) -> None:
    """Move focus to the widget under the pointer.

    CustomTkinter does this so an entry blurs when you click elsewhere.
    A menu must be skipped. Focusing one on press leaves an X pointer
    grab: the cursor disappears, menu items cannot be chosen, and no
    other window can be clicked until the process is killed.
    """
    try:
        widget = getattr(event, "widget", None)
        root = widget._root() if isinstance(widget, tk.Misc) else None
        grabbed = _grabbed(root) if root is not None else None
        if isinstance(widget, tk.Menu) or grabbed is not None:
            trace_menu_event(root, "focus-hook", event)
        if isinstance(widget, tk.Menu):
            return
        focus = getattr(widget, "focus_set", None)
        if focus is None:
            return
        with contextlib.suppress(tk.TclError):
            focus()
    except Exception:
        return


def _replace_menu_focus_binding(widget: tk.Misc) -> None:
    """Drop CustomTkinter's set_focus binding and keep the menu-safe one.

    CTk and every CTkToplevel append set_focus with bind_all. unbind_all
    would also drop the click logger, so only those callbacks are removed.
    The callback is registered on the Tk root even when a Toplevel adds it.
    """
    root = widget._root()
    script = root.bind_all("<Button-1>") or ""
    for funcid in _bind_funcids(script):
        if funcid.endswith("set_focus"):
            root._unbind(("bind", "all", "<Button-1>"), funcid)
            _forget_tcl_command(root, funcid)
    script = root.bind_all("<Button-1>") or ""
    if "focus_clicked_widget" not in script:
        root.bind_all("<Button-1>", focus_clicked_widget, add="+")


def _forget_tcl_command(widget: tk.Misc, funcid: str) -> None:
    cmds = getattr(widget, "_tclCommands", None)
    if cmds:
        widget._tclCommands = [name for name in cmds if name != funcid]
    for child in widget.winfo_children():
        _forget_tcl_command(child, funcid)


def _bind_funcids(script: str) -> list[str]:
    ids = []
    for line in script.splitlines():
        start = line.find("[")
        if start < 0:
            continue
        token = line[start + 1:].split(" ", 1)[0]
        if token[:1].isdigit():
            ids.append(token)
    return ids


def bind_menu_before(root: tk.Misc, sequence: str, func) -> None:
    """Run func before Tk's Menu binding without replacing it.

    A ``<Button-1>`` or ``<ButtonRelease-1>`` binding is more specific
    than ``<Button>`` / ``<ButtonRelease>`` and hides ``tk::MenuButtonDown``
    and ``tk::MenuInvoke``. The menu then never posts, or posts under a
    global grab that is never released.
    """
    existing = root.bind_class("Menu", sequence) or ""
    if func.__name__ in existing:
        return
    root.bind_class("Menu", sequence, func)
    ours = root.bind_class("Menu", sequence) or ""
    root.tk.call("bind", "Menu", sequence, ours + existing)


def install_click_focus(root: tk.Misc) -> None:
    """Replace CustomTkinter's Button-1 focus binding with a menu-safe one."""
    _replace_menu_focus_binding(root)
    _patch_toplevel_focus()


def _patch_toplevel_focus() -> None:
    """Each new CTkToplevel reinstalls set_focus. Strip it again."""
    ctk = require_customtkinter()
    cls = ctk.CTkToplevel
    if getattr(cls, "_menu_focus_patched", False):
        return
    orig = cls.__init__

    def __init__(self, *args, **kwargs):
        orig(self, *args, **kwargs)
        _replace_menu_focus_binding(self)

    cls.__init__ = __init__
    cls._menu_focus_patched = True


def install_menu_trace(root: tk.Misc) -> None:
    """Write menu, focus, and pointer-grab events to /tmp/chapterfit.log.

    The lines are flushed to disk, so they are still there after the
    process is killed from another console. Look for ``menu-trace``.
    """
    def on_class(tag):
        def handler(event, tag=tag):
            trace_menu_event(root, tag, event)
            if tag == "class-press":
                _arm_grab_watch(root)
        return handler

    # <Button> and <ButtonRelease> are Tk's menu bindings (post and
    # invoke). <Button-1> is a more specific pattern, so binding it
    # replaces those and the menu never opens. Append to the real ones.
    for seq, tag in (
        ("<Button>", "class-press"),
        ("<ButtonRelease>", "class-release"),
        ("<Map>", "class-map"),
        ("<Unmap>", "class-unmap"),
        ("<FocusIn>", "class-focus-in"),
        ("<FocusOut>", "class-focus-out"),
    ):
        root.bind_class("Menu", seq, on_class(tag), add="+")

    def trace_press(event):
        _trace_pointer(root, "all-press", event)

    def trace_release(event):
        _trace_pointer(root, "all-release", event)

    root.bind_all("<Button-1>", trace_press, add="+")
    root.bind_all("<ButtonRelease-1>", trace_release, add="+")
    trace_menu_event(root, "installed")


def trace_menu_event(root: tk.Misc | None, tag: str, event=None) -> None:
    """One flushed ``menu-trace`` line. Never raises."""
    if root is None:
        return
    try:
        from chaptertools import file_log
        parts = [tag, _pointer_state(root)]
        widget = getattr(event, "widget", None)
        if event is not None:
            parts.append(f"widget={_widget_bits(widget)}")
            parts.append(
                f"xy={getattr(event, 'x_root', '?')},{getattr(event, 'y_root', '?')}")
        if isinstance(widget, tk.Menu):
            active = _menu_active(widget)
            if active:
                parts.append(active)
        stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        file_log.write(f"{stamp} menu-trace {' '.join(parts)}\n")
    except Exception as exc:
        with contextlib.suppress(Exception):
            from chaptertools import file_log
            stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            file_log.write(f"{stamp} menu-trace {tag} trace-error {exc}\n")


def _trace_pointer(root: tk.Misc, tag: str, event) -> None:
    try:
        widget = getattr(event, "widget", None)
        if isinstance(widget, tk.Menu) or _grabbed(root) is not None:
            trace_menu_event(root, tag, event)
    except Exception:
        return


def _arm_grab_watch(root: tk.Misc) -> None:
    if getattr(root, "_menu_grab_watch", False):
        return
    root._menu_grab_watch = True
    root.after(100, lambda: _grab_watch(root, 0))


def _grab_watch(root: tk.Misc, n: int) -> None:
    try:
        if not root.winfo_exists():
            return
        trace_menu_event(root, f"watch-{n}")
        grab = _grabbed(root)
    except Exception:
        root._menu_grab_watch = False
        return
    if grab is not None and n < 19:
        root.after(250, lambda n=n + 1: _grab_watch(root, n))
        return
    root._menu_grab_watch = False


def _grabbed(root: tk.Misc):
    """Widget holding the pointer grab, a raw path, or None.

    A posted menu grabs ``.__tk__messagebox``. Tkinter cannot resolve that
    name, so ``grab_current`` raises KeyError from inside the click binding
    and the menu never finishes.
    """
    try:
        name = root.tk.call("grab", "current", root._w)
    except tk.TclError:
        return None
    return _resolve_grab(root, name)


def _resolve_grab(root: tk.Misc, name):
    if not name:
        return None
    try:
        return root.nametowidget(name)
    except (KeyError, tk.TclError):
        return str(name)


def _pointer_state(root: tk.Misc) -> str:
    try:
        focus = root.focus_get()
    except tk.TclError:
        focus = None
    names = []
    for funcid in _bind_funcids(root.bind_all("<Button-1>") or ""):
        names.append(funcid.lstrip("0123456789"))
    return (
        f"grab={_widget_bits(_grabbed(root))} "
        f"focus={_widget_bits(focus)} "
        f"bindings={','.join(names)}"
    )


def _widget_bits(widget) -> str:
    if widget is None:
        return "none"
    if isinstance(widget, str):
        return widget
    try:
        kind = widget.winfo_class()
    except tk.TclError:
        kind = "?"
    path = getattr(widget, "_w", "?")
    cursor = ""
    with contextlib.suppress(tk.TclError, AttributeError):
        cursor = str(widget.cget("cursor") or "")
    shown = f"{kind}:{path}"
    if cursor:
        shown += f" cursor={cursor}"
    return shown


def _menu_active(menu: tk.Menu) -> str:
    try:
        index = menu.index("active")
    except tk.TclError:
        return ""
    if index is None:
        return "active=none"
    try:
        kind = menu.type(index)
        label = menu.entrycget(index, "label")
    except tk.TclError:
        return f"active={index}"
    return f"active={index}:{kind}:{label}"


def restyle_ttk(root: tk.Misc) -> None:
    """Point Treeview, headings, and paned sashes at the active palette."""
    _style_ttk(root)


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


def _btn_disabled_fg():
    return pair("disabled_fill")


def _btn_disabled_text():
    return pair("accent_text")


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
    widget.configure(state=state, fg_color=_btn_disabled_fg())


def _is_disabled_button_fg(color) -> bool:
    light, dark = (c.lower() for c in pair("disabled_fill"))
    if isinstance(color, (list, tuple)) and len(color) >= 2:
        return str(color[0]).lower() == light and str(color[1]).lower() == dark
    return str(color).lower() in {light, dark}


def is_disabled(widget) -> bool:
    if is_ctk(widget):
        try:
            return str(widget.cget("state")) == "disabled"
        except (tk.TclError, ValueError):
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
    """fg/hover for a primary action button. ``on`` is the chosen action."""
    fill = "accent" if on else "accent_muted"
    hover = "accent_hover" if on else "accent"
    return {
        "fg_color": pair(fill),
        "hover_color": pair(hover),
        "text_color": pair("accent_text"),
        "text_color_disabled": _btn_disabled_text(),
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
