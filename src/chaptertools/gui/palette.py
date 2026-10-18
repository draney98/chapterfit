"""Legacy colour names. Values come from the active theme palette."""
from __future__ import annotations

from .theme import DARK, LIGHT, active, contrast, text_pairs

TEXT_PAIRS = text_pairs()

# Names that follow the theme currently on screen.
_ACTIVE = {
    "WINDOW": "bg",
    "FIELD": "field_bg",
    "TEXT": "text",
    "MUTED": "text_muted",
    "OK": "ok",
    "WARN": "warn",
    "ERROR": "error",
    "BLOCKED": "blocked",
    "LINK": "link",
    "BANNER": "banner",
    "TIP": "tip",
    "BORDER": "border",
    "RAIL_IDLE": "surface_alt",
    "RAIL_CURRENT": "surface",
    "RAIL_ACCENT": "accent",
    "RAIL_DONE": "ok",
    "ROW_UNPLACED": "row_unplaced",
    "ROW_LOWCONF": "row_lowconf",
    "ROW_CONFLICT": "row_conflict",
    "PIN_BAR": "pin",
    "GAP_BAR": "gap",
    "Q_GROUND": "bg",
    "Q_PANEL": "surface",
    "Q_BORDER": "border",
    "Q_TEXT": "text",
    "Q_MUTED": "text_muted",
    "Q_ACCENT": "accent",
    "Q_SELECT": "select_bg",
    "Q_WARN_TEXT": "warn",
    "Q_WARN_BG": "warn_bg",
    "Q_WARN_BORDER": "warn_border",
    "Q_CLIP": "clip",
    "Q_NOISY": "noisy",
    "Q_ON_ACCENT": "accent_text",
    "KIT_BG": "bg",
    "KIT_PANEL": "surface",
    "KIT_SUNKEN": "surface_alt",
    "KIT_TEXT": "text",
    "KIT_MUTED": "text_muted",
    "KIT_ACCENT": "accent",
    "KIT_WHITE": "accent_text",
}

# Names that always mean one side of the pair (CustomTkinter stores both).
_FIXED = {
    "BTN_FACE": ("light", "accent"),
    "BTN_FACE_DARK": ("dark", "accent"),
    "BTN_DISABLED_FACE": ("light", "disabled_fill"),
    "BTN_DISABLED_FACE_DARK": ("dark", "disabled_fill"),
    "BTN_DISABLED_TEXT": ("light", "accent_text"),
    "BTN_DISABLED_TEXT_DARK": ("dark", "accent_text"),
    "BTN_ON_DISABLED": ("light", "accent_text"),
    "D_WINDOW": ("dark", "bg"),
    "D_FIELD": ("dark", "field_bg"),
    "D_TEXT": ("dark", "text"),
    "D_MUTED": ("dark", "text_muted"),
    "D_BORDER": ("dark", "border"),
    "D_SELECT": ("dark", "select_bg"),
    "D_PANEL": ("dark", "surface"),
    "D_GROUND": ("dark", "bg"),
    "D_WARN_BG": ("dark", "warn_bg"),
    "D_WARN_TEXT": ("dark", "warn"),
    "D_OK": ("dark", "ok"),
    "D_TIP": ("dark", "tip"),
    "D_BANNER": ("dark", "banner"),
    "D_ROW_UNPLACED": ("dark", "row_unplaced"),
    "D_ROW_LOWCONF": ("dark", "row_lowconf"),
    "D_ROW_CONFLICT": ("dark", "row_conflict"),
}


def __getattr__(name: str):
    if name in _ACTIVE:
        return active()[_ACTIVE[name]]
    if name in _FIXED:
        side, key = _FIXED[name]
        return (LIGHT if side == "light" else DARK)[key]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
