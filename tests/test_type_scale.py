"""The type scale is real pixels, defined once."""
from __future__ import annotations

import subprocess
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def root():
    tkinter = pytest.importorskip("tkinter")
    try:
        app = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display available")
    from chaptertools.gui.theme import restyle_ttk
    from chaptertools.gui.ui_kit import apply_named_fonts, style_treeview
    apply_named_fonts(app, 14)
    restyle_ttk(app)
    style_treeview(app)
    yield app
    app.destroy()


def test_linespace_is_the_pixel_scale(root):
    from chaptertools.gui.ui_kit import BODY, SMALL, TITLE, linespace_of, tk_font
    assert linespace_of(tk_font(root, BODY)) <= 21
    assert linespace_of(tk_font(root, TITLE)) <= 24
    assert linespace_of(tk_font(root, SMALL)) >= 16


def test_named_fonts_are_negative_pixels(root):
    for name in tkfont.names(root):
        if not str(name).startswith("Tk"):
            continue
        size = int(tkfont.nametofont(name, root=root).cget("size"))
        assert size < 0, name


def test_treeview_rows_share_one_height(root):
    from chaptertools.gui.ui_kit import ROW_H
    style = ttk.Style(root)
    for name in ("Treeview", "Chapter.Treeview"):
        assert int(style.lookup(name, "rowheight")) == ROW_H, name


def test_no_font_constructor_outside_the_type_module():
    proc = subprocess.run(
        ["grep", "-rnE",
         r"tkfont\.Font\(|CTkFont\(|font=\(|size=[0-9-]",
         "src/chaptertools/gui", "--include=*.py"],
        cwd=ROOT, capture_output=True, text=True, check=False)
    leaked = [
        line for line in proc.stdout.splitlines()
        if "ui_kit.py" not in line and "theme.py" not in line
    ]
    assert leaked == []
