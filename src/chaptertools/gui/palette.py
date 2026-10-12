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
BORDER = "#C8C5BE"      # card outlines, rail gaps
RAIL_IDLE = "#E9E7E2"   # step rail idle cell
RAIL_CURRENT = FIELD    # step rail selected cell
RAIL_ACCENT = "#4A6984" # selected step underline
RAIL_DONE = "#2E7D32"   # done step title

ROW_UNPLACED = "#b0b0b0"   # owner directive: light grey
ROW_LOWCONF = "#e8c680"
ROW_CONFLICT = "#f4b4b4"

PIN_BAR = "#7b2fa8"
GAP_BAR = "#1f7fd1"

# Quality tab. Text pairs below are checked for WCAG AA; a few of the
# mockup hexes were darkened so muted and warning text still pass.
Q_GROUND = "#F3F2EF"
Q_PANEL = "#FFFFFF"
Q_BORDER = "#D9D6D0"
Q_TEXT = "#1F1E1C"
Q_MUTED = "#4A4844"
Q_ACCENT = "#2B5FA8"
Q_SELECT = "#EEF3FB"
Q_WARN_TEXT = "#6B3E00"
Q_WARN_BG = "#FFF4E0"
Q_WARN_BORDER = "#E8B77A"
Q_CLIP = "#D97706"
Q_NOISY = "#EADFC8"
Q_ON_ACCENT = "#FFFFFF"

# CTkButton faces (theme.json). Disabled fill is muted so off actions
# stay readable instead of grey-on-slate.
BTN_FACE = "#4A6984"
BTN_FACE_DARK = "#3a5670"
BTN_DISABLED_FACE = "#5A738C"
BTN_DISABLED_FACE_DARK = "#4a5a6a"
BTN_DISABLED_TEXT = "#E8E6E1"
BTN_DISABLED_TEXT_DARK = "#D5D3CD"
BTN_ON_DISABLED = "#FFFFFF"  # light mode: white on muted fill

# Dark appearance (CustomTkinter Dark mode). Checked for WCAG AA too.
D_WINDOW = "#1e1e1c"
D_FIELD = "#2a2a28"
D_TEXT = "#E8E6E1"
D_MUTED = "#B0AEA8"
D_BORDER = "#3d3d3a"
D_SELECT = "#243548"
D_PANEL = "#2a2a28"
D_GROUND = "#1e1e1c"
D_WARN_BG = "#3d2e14"
D_WARN_TEXT = "#F0C080"
D_OK = "#81C784"
D_TIP = "#3d3d20"
D_BANNER = "#3d2e14"
D_ROW_UNPLACED = "#4a4a4a"
D_ROW_LOWCONF = "#6b5320"
D_ROW_CONFLICT = "#6b3030"

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
    "q-body": (Q_TEXT, Q_GROUND),
    "q-panel": (Q_TEXT, Q_PANEL),
    "q-muted": (Q_MUTED, Q_GROUND),
    "q-muted-panel": (Q_MUTED, Q_PANEL),
    "q-select": (Q_TEXT, Q_SELECT),
    "q-muted-select": (Q_MUTED, Q_SELECT),
    "q-warn": (Q_WARN_TEXT, Q_WARN_BG),
    "q-accent": (Q_ON_ACCENT, Q_ACCENT),
    "q-changed": (Q_TEXT, Q_SELECT),
    "btn": (Q_ON_ACCENT, BTN_FACE),
    "btn-disabled-theme": (BTN_DISABLED_TEXT, BTN_FACE),
    "btn-disabled": (BTN_ON_DISABLED, BTN_DISABLED_FACE),
    "seg-unselected": (Q_ON_ACCENT, BTN_DISABLED_FACE),
    "d-body": (D_TEXT, D_WINDOW),
    "d-muted": (D_MUTED, D_WINDOW),
    "d-ok": (D_OK, D_WINDOW),
    "d-warn": (D_WARN_TEXT, D_WINDOW),
    "d-banner": (D_WARN_TEXT, D_BANNER),
    "d-tip": (D_TEXT, D_TIP),
    "d-row": (D_TEXT, D_FIELD),
    "d-row-unplaced": (D_TEXT, D_ROW_UNPLACED),
    "d-row-lowconf": (D_TEXT, D_ROW_LOWCONF),
    "d-row-conflict": (D_TEXT, D_ROW_CONFLICT),
    "d-panel": (D_TEXT, D_PANEL),
    "d-muted-panel": (D_MUTED, D_PANEL),
    "d-select": (D_TEXT, D_SELECT),
    "d-warn-panel": (D_WARN_TEXT, D_WARN_BG),
    "d-accent": (Q_ON_ACCENT, Q_ACCENT),
    "d-btn": (Q_ON_ACCENT, BTN_FACE_DARK),
    "d-btn-disabled-theme": (BTN_DISABLED_TEXT_DARK, BTN_FACE_DARK),
    "d-btn-disabled": (BTN_DISABLED_TEXT, BTN_DISABLED_FACE_DARK),
    "d-seg-unselected": (Q_ON_ACCENT, BTN_FACE_DARK),
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
