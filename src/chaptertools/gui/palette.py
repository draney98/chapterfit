"""Every colour the GUI draws text with or behind text, in one place.

tests/test_gui_legibility.py checks each text/background pair against
WCAG AA (4.5:1), so a colour change here cannot quietly make text faint.
"""
from __future__ import annotations

WINDOW = "#dcdad5"      # clam frame background
FIELD = "#ffffff"       # treeview / entry background
TEXT = "#1a1a1a"
MUTED = "#3d3d3d"       # result lines, legends, axis labels
OK = "#1b5e20"          # done stages, the recommended next step
WARN = "#6b4000"        # attention stages and notes
BLOCKED = "#555555"     # blocked stage labels
LINK = "#0b4a94"        # clickable chapter numbers
BANNER = "#ffe9c8"      # attention strip background
TIP = "#ffffe0"         # tooltip background

ROW_UNPLACED = "#b0b0b0"   # owner directive: light grey
ROW_LOWCONF = "#e8c680"
ROW_CONFLICT = "#f4b4b4"

PIN_BAR = "#7b2fa8"
GAP_BAR = "#1f7fd1"

# (foreground, background) pairs that carry text
TEXT_PAIRS = {
    "body": (TEXT, WINDOW),
    "muted": (MUTED, WINDOW),
    "ok": (OK, WINDOW),
    "warn": (WARN, WINDOW),
    "blocked": (BLOCKED, WINDOW),
    "link": (LINK, WINDOW),
    "banner": (WARN, BANNER),
    "tip": (TEXT, TIP),
    "row": (TEXT, FIELD),
    "row-unplaced": (TEXT, ROW_UNPLACED),
    "row-lowconf": (TEXT, ROW_LOWCONF),
    "row-conflict": (TEXT, ROW_CONFLICT),
}


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
