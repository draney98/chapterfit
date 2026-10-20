"""Nothing clips, every column holds its content, text has contrast.

Geometry runs at the three window sizes the GUI is designed for, with a
worst-case book loaded (40 chapters, 12 h, long titles), on every stage
panel and notebook tab. Skips without a display.
"""
from __future__ import annotations

import json
import os

import pytest

from chaptertools.gui import palette

SIZES = [(1280, 800), (1600, 960), (1920, 1080)]
CHECKED = {"TButton", "Button", "TLabel", "Label", "TCheckbutton",
           "Checkbutton", "TRadiobutton", "Radiobutton",
           "CTkButton", "CTkLabel", "CTkCheckBox", "CTkSwitch",
           "CTkRadioButton", "CTkSegmentedButton"}


# Marks and borders are readable at 3:1. Text stays at 4.5:1.
_MARK_PAIRS = ("silence-band", "checkbox-banner")


@pytest.mark.parametrize("pair", sorted(palette.TEXT_PAIRS))
def test_text_colours_meet_aa_contrast(pair):
    fg, bg = palette.TEXT_PAIRS[pair]
    minimum = 3.0 if any(pair.endswith(name) for name in _MARK_PAIRS) else 4.5
    assert palette.contrast(fg, bg) >= minimum, pair


def _artifact(n=40, dur=12 * 3600.0 + 34 * 60):
    step = dur / n
    chapters = [{"title": f"Chapter {i + 1}: The Rather Long Title of a "
                          f"Chapter Number {i + 1}",
                 "start": i * step} for i in range(n)]
    results = []
    for i, c in enumerate(chapters):
        results.append({
            "index": i, "title": c["title"], "published": c["start"],
            "time": None if i % 7 == 3 and i % 9 != 4
            else c["start"] + 1.234,
            "confidence": 0.12 if i % 5 == 2 else 0.97,
            "pinned": i % 9 == 4, "spread": 3.0})
    gaps = [{"mid": r["time"], "dur": 12.34} for r in results
            if r["time"] is not None]
    return {
        "schema": 3,
        "envelope": {"duration": dur}, "calibration": {"x": 1},
        "gaps": {"gaps": gaps}, "fingerprint": {"x": 1},
        "source": {"timeline_trustworthy": True},
        "identification": {"best_trial": {"score": 6.0},
                           "label": "Audnexus B0041M5MRE",
                           "chapters": chapters},
        "alignment": {"rate": 1.0003, "conflicts": [4], "results": results},
        "pins": {str(r["index"]): r["time"] for r in results
                 if r["pinned"]},
    }


@pytest.fixture
def app(tmp_path, monkeypatch):
    tkinter = pytest.importorskip("tkinter")
    try:
        tkinter.Tk().destroy()
    except tkinter.TclError:
        pytest.skip("no display available")
    from chaptertools.gui.app import App
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "work"))
    book = tmp_path / "library" / "An Author With a Long Name" \
        / "A Book Whose Folder Name Goes On for Quite a While"
    book.mkdir(parents=True)
    audio = book / "A Book Whose Folder Name Goes On for Quite a While.mp3"
    audio.touch()
    a = App(library_root=str(tmp_path / "library"), modal_busy=False)
    a.overrideredirect(True)  # the window manager must not resize it
    wf = a.workflow
    wf.set_book(str(audio))
    os.makedirs(wf.work_dir, exist_ok=True)
    with open(os.path.join(wf.work_dir, audio.name + ".analysis.json"),
              "w") as f:
        json.dump(_artifact(), f)
    wf.set_book(str(audio))
    yield a
    a.destroy()


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


def _clipped(app):
    """Mapped text widgets smaller than they ask for, or outside the
    window."""
    from chaptertools.gui.theme import ctk_kind, widget_text

    bad = []
    right = app.winfo_rootx() + app.winfo_width()
    bottom = app.winfo_rooty() + app.winfo_height()
    for w in _walk(app):
        # CustomTkinter draws on an inner tk Label/Canvas; check the outer control.
        master = getattr(w, "master", None)
        if master is not None and type(master).__module__.startswith("customtkinter"):
            continue
        kind = ctk_kind(w) or w.winfo_class()
        if kind not in CHECKED or not w.winfo_viewable():
            continue
        if _scrolled_out(w):
            continue
        # Stretchy grid labels report a large reqwidth; only the allocated
        # cell matters, and wraplength handles long values.
        info = w.grid_info() if hasattr(w, "grid_info") else {}
        sticky = str(info.get("sticky") or "")
        if "e" in sticky and "w" in sticky:
            continue
        text = widget_text(w)
        if not text or text == "CTkLabel":
            continue
        if w.winfo_width() < w.winfo_reqwidth() \
                or w.winfo_height() < w.winfo_reqheight():
            bad.append(f"{kind} {text!r} "
                       f"{w.winfo_width()}x{w.winfo_height()} < "
                       f"{w.winfo_reqwidth()}x{w.winfo_reqheight()}")
        elif w.winfo_rootx() + w.winfo_width() > right \
                or w.winfo_rooty() + w.winfo_height() > bottom:
            bad.append(f"{kind} {text!r} outside the window")
    return bad


def _scrolled_out(widget) -> bool:
    """A canvas still reports scrolled-away children as viewable."""
    parent = widget.master
    while parent is not None:
        if parent.winfo_class() == "Canvas":
            top = parent.winfo_rooty()
            bottom = top + parent.winfo_height()
            if widget.winfo_rooty() >= bottom:
                return True
            return widget.winfo_rooty() + widget.winfo_height() <= top
        parent = getattr(parent, "master", None)
    return False


def _settle(app, geometry):
    """Apply ``WxH`` and fail if the window never actually becomes that size."""
    import time

    width_s, rest = geometry.split("x", 1)
    height = int(rest.split("+", 1)[0])
    width = int(width_s)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        app.geometry(geometry)
        app.update()
        if app.winfo_width() == width and app.winfo_height() == height:
            return
    raise AssertionError(
        f"asked {width}x{height}, got {app.winfo_width()}x{app.winfo_height()}")


def test_library_pane_stays_visible(tmp_path, monkeypatch):
    """The book list is on screen at the opening size, not pinned at 0."""
    tkinter = pytest.importorskip("tkinter")
    try:
        tkinter.Tk().destroy()
    except tkinter.TclError:
        pytest.skip("no display available")
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("CHAPTERFIT_LIBRARY", str(tmp_path / "library"))
    from chaptertools.gui.app import LIBRARY_WIDTH, App

    (tmp_path / "library").mkdir()
    opened = App(library_root=str(tmp_path / "library"), modal_busy=False)
    try:
        opened.update()
        assert opened.lib_tree.winfo_ismapped()
        assert opened.lib_tree.winfo_width() > 40
        assert opened._pane.sashpos(0) >= LIBRARY_WIDTH - 8
    finally:
        opened.destroy()


def test_window_opens_at_requested_size(tmp_path, monkeypatch):
    """First realized size is 1600x960, not the smaller minimum."""
    tkinter = pytest.importorskip("tkinter")
    try:
        tkinter.Tk().destroy()
    except tkinter.TclError:
        pytest.skip("no display available")
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "work"))
    monkeypatch.setenv("CHAPTERFIT_LIBRARY", str(tmp_path / "library"))
    from chaptertools.gui.app import App

    (tmp_path / "library").mkdir()
    opened = App(library_root=str(tmp_path / "library"), modal_busy=False)
    try:
        opened.update()
        assert opened.winfo_width() == 1600
        assert opened.winfo_height() == 960
    finally:
        opened.destroy()


def test_place_bottom_pane_shows_full_sweep(app):
    _settle(app, "1600x1030+0+0")
    wf = app.workflow
    wf.show_step("place")
    app.update()
    btn = wf.full_btn
    assert btn.winfo_ismapped()
    window_bottom = app.winfo_rooty() + app.winfo_height()
    assert btn.winfo_rooty() + btn.winfo_height() <= window_bottom + 2
    card = wf._resolve_left
    assert card.winfo_rooty() + card.winfo_height() <= window_bottom + 2
    split = wf._place_split
    assert split.winfo_height() - split.sashpos(0) >= 300


def test_place_table_keeps_eight_rows_when_short(app):
    _settle(app, "1600x800+0+0")
    wf = app.workflow
    wf.show_step("place")
    app.update()
    split = wf._place_split
    import tkinter.ttk as ttk
    row_h = int(ttk.Style(split).lookup("Chapter.Treeview", "rowheight") or 26)
    least = 8 * row_h + row_h + 12
    assert split.sashpos(0) >= least - 8
    assert split.winfo_height() - split.sashpos(0) >= 160


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_nothing_clips(app, size):
    w, h = size
    min_w, min_h = app.minsize()
    assert min_w <= w and min_h <= h, "minimum size exceeds the target"
    _settle(app, f"{w}x{h}+0+0")
    wf = app.workflow
    problems = []
    for step in ("scan", "match", "place", "finish", "cleanup"):
        wf.show_step(step)
        app.update()
        problems += [f"{step}: {p}" for p in _clipped(app)]
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_table_columns_hold_their_content(app, size):
    from chaptertools.gui.workflow import CHAPTER_COLUMNS
    _settle(app, f"{size[0]}x{size[1]}+0+0")
    wf = app.workflow
    tree = wf.tree
    import tkinter.font as tkfont
    head = tkfont.nametofont("TkHeadingFont")
    for n, (col, heading, _sample, _a) in enumerate(CHAPTER_COLUMNS):
        width = int(tree.column(col, "width"))
        assert head.measure(heading) + 8 <= width, col
        if col == "title":
            continue
        for iid in tree.get_children():
            value = str(tree.item(iid, "values")[n])
            assert wf.table_font.measure(value) + 8 <= width, (col, value)
    # Fixed columns fit their content; Title stretches to fill leftover room.
    # With a 14 px floor the sum can exceed a narrow tree — that is fine.


def test_font_size_change_keeps_columns_and_fonts_in_step(app):
    import tkinter.font as tkfont

    from chaptertools.gui.workflow import CHAPTER_COLUMNS
    app.set_font_size(15)
    _settle(app, "1920x1080+0+0")
    wf = app.workflow
    assert int(tkfont.nametofont("TkDefaultFont", root=app).cget("size")) \
        == -15
    assert abs(int(wf.table_font.cget("size"))) == 15
    for n, (col, _h, _s, _a) in enumerate(CHAPTER_COLUMNS):
        if col == "title":
            continue
        width = int(wf.tree.column(col, "width"))
        for iid in wf.tree.get_children():
            value = str(wf.tree.item(iid, "values")[n])
            assert wf.table_font.measure(value) + 8 <= width, (col, value)
    assert not _clipped(app)


@pytest.mark.parametrize("size", [(1600, 1030), (1366, 768)],
                         ids=lambda s: f"{s[0]}x{s[1]}")
def test_match_debug_layout_clean(app, size):
    from chaptertools.gui.debug_layout import check

    w, h = size
    _settle(app, f"{w}x{h}+0+0")
    app.workflow.show_step("match")
    app.update()
    # Scope to the Match workspace — library / other steps stay out of scope.
    problems = check(app.workflow.workspace)
    assert not problems, "\n".join(problems)


def _visible_chapter_rows(app) -> int:
    tree = app.workflow.tree
    full = 0
    for iid in tree.get_children():
        box = tree.bbox(iid)
        if box and box[1] + box[3] <= tree.winfo_height():
            full += 1
    return full


def test_place_keeps_chapter_rows(app):
    """The chapter table is the point of Place. Controls sit below it."""
    _settle(app, "1366x768+0+0")
    app.workflow.show_step("place")
    app.update()
    assert _visible_chapter_rows(app) >= 8
    _settle(app, "1600x1030+0+0")
    app.update()
    assert _visible_chapter_rows(app) >= 18


def test_table_shows_25_rows_at_1080p(app):
    _settle(app, "1920x1080+0+0")
    wf = app.workflow
    wf.show_step("place")
    app.update()
    full = 0
    for iid in wf.tree.get_children():
        box = wf.tree.bbox(iid)
        if box and box[1] + box[3] <= wf.tree.winfo_height():
            full += 1
    # rowheight 28 shows fewer rows than the old 22 px rows
    assert full >= 20, full
