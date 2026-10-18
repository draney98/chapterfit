"""Legacy colour names. Values come from the active theme palette."""
from __future__ import annotations

from .theme import active, contrast, text_pairs

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

def current(name: str) -> str:
    """One hex for the mode on screen. Canvases, ttk, and tags need this."""
    return active()[_ACTIVE[name]]


def __getattr__(name: str):
    if name in _ACTIVE:
        from .theme import pair
        return pair(_ACTIVE[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
