"""ttk leftovers for the Quality tab (trees and paned sashes)."""

from __future__ import annotations

from chaptertools.gui.theme import restyle_ttk


def install(widget) -> None:
    """Keep Treeview / sash colors aligned with the active appearance."""
    restyle_ttk(widget.winfo_toplevel())
