"""LibriVox GUI views driven by recorded --json lines. No network."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

FIX = Path(__file__).parent / "fixtures" / "librivox"


@pytest.fixture
def root(tmp_path, monkeypatch):
    """Minimal Tk root when DISPLAY is available; otherwise skip."""
    monkeypatch.setenv("CHAPTERFIT_LIBRARY", str(tmp_path))
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "work"))
    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        pytest.skip("no display")
    yield root
    root.destroy()


def test_search_apply_recorded_json(root, tmp_path):
    from chaptertools.gui.librivox_view import LibrivoxView

    app = mock.MagicMock()
    app.library_root = str(tmp_path)
    app.runner.busy = False
    app.session.set_job = mock.MagicMock()
    view = LibrivoxView(root, app)
    payload = {
        "hits": [{
            "id": 77, "title": "Pride and Prejudice", "author": "Jane Austen",
            "readers": ["Karen Savage"], "duration": 1080, "language": "English",
            "sections": 2,
        }],
        "unfinished": [{
            "id": 9999, "title": "Pride unfinished", "author": "Jane Austen",
            "language": "English",
        }],
    }
    view._apply_search(payload, "Pride")
    kids = view.results.get_children()
    assert "77" in kids
    assert "9999" in kids
    assert "downloadable" in view._status_var.get()
    assert "unfinished" in view._status_var.get()


def test_search_apply_http_error_is_not_empty_catalog(root, tmp_path):
    from chaptertools.gui.librivox_view import LibrivoxView

    app = mock.MagicMock()
    app.library_root = str(tmp_path)
    app.runner.busy = False
    view = LibrivoxView(root, app)
    view._status_var.set("Searching…")
    view._apply_search(
        {"event": "error", "error": "LibriVox HTTP 500",
         "hits": [], "unfinished": []},
        "Pride")
    text = view._empty_var.get()
    assert "500" in text
    assert "Nothing on LibriVox" not in text
    assert view._status_var.get() == ""
    assert not view.results.get_children()
    logged = " ".join(str(c.args[0]) for c in app.log.call_args_list if c.args)
    assert "500" in logged


def test_do_search_logs_the_query(root, tmp_path):
    from chaptertools.gui.librivox_view import LibrivoxView

    app = mock.MagicMock()
    app.library_root = str(tmp_path)
    app.runner.busy = False
    view = LibrivoxView(root, app)
    view.title_var.set("Pride")
    view.author_var.set("Austen")
    view._do_search()
    logged = " ".join(str(c.args[0]) for c in app.log.call_args_list if c.args)
    assert "search" in logged.lower()
    assert "Pride" in logged
    assert app.runner.run.called


def test_plan_refused_disables_fetch(root, tmp_path):
    from chaptertools.gui.librivox_view import LibrivoxView

    app = mock.MagicMock()
    app.library_root = str(tmp_path)
    app.runner.busy = False
    view = LibrivoxView(root, app)
    view._apply_plan({
        "event": "refused",
        "reason": "count_mismatch",
        "playlist_count": 3,
        "section_count": 2,
        "detail": "counts differ",
    })
    assert str(view.fetch_btn.cget("state")) == "disabled"
    assert "3" in view._detail_order.get()


def test_plan_ready_enables_fetch(root, tmp_path):
    from chaptertools.gui.librivox_view import LibrivoxView

    app = mock.MagicMock()
    app.library_root = str(tmp_path)
    app.runner.busy = False
    view = LibrivoxView(root, app)
    dest = tmp_path / "Jane Austen" / "Pride and Prejudice"
    plan = {
        "event": "plan",
        "id": 77,
        "title": "Pride and Prejudice",
        "author": "Jane Austen",
        "language": "English",
        "set": "128Kbps MP3",
        "sets_available": ["64Kbps MP3", "128Kbps MP3"],
        "playlist_count": 2,
        "section_count": 2,
        "dest": str(dest),
        "files": [
            {"section_number": 1, "title": "Chapter 01", "readers": ["A"],
             "duration": 600, "dest": str(dest / "01 - Chapter 01.mp3"),
             "source": "a.mp3"},
            {"section_number": 2, "title": "Chapter 02", "readers": ["A"],
             "duration": 480, "dest": str(dest / "02 - Chapter 02.mp3"),
             "source": "b.mp3"},
        ],
    }
    view._apply_plan(plan)
    assert view.fetch_btn.cget("text") == "Fetch into library"
    assert str(view.fetch_btn.cget("state")) == "normal"


def test_multifile_book_load(root, tmp_path):
    from chaptertools.gui.multifile_book import MultifileBook

    folder = tmp_path / "Author" / "Book"
    folder.mkdir(parents=True)
    (folder / "00 - Intro.mp3").write_bytes(b"x")
    (folder / "01 - Ch1.mp3").write_bytes(b"x")
    meta = {
        "id": 817,
        "title": "Book",
        "author": "Author",
        "set": "128Kbps MP3",
        "language": "English",
        "fetched": "2026-01-01",
        "sections": [
            {"section_number": 0, "title": "Intro", "reader": "R",
             "readers": ["R"], "duration": 120, "file": "00 - Intro.mp3"},
            {"section_number": 1, "title": "Ch1", "reader": "R",
             "readers": ["R"], "duration": 300, "file": "01 - Ch1.mp3"},
        ],
    }
    (folder / "librivox.json").write_text(json.dumps(meta))
    app = mock.MagicMock()
    pane = MultifileBook(root, app, on_cleanup=lambda: None)
    pane.load(str(folder))
    assert "2 files" in pane.meta_var.get()
    assert "LibriVox 817" in pane.meta_var.get()
    assert len(pane.table.get_children()) == 2
    assert "nothing to scan" in pane.chapters_status.get()


def test_app_does_not_import_librivox_fetch():
    import chaptertools.gui.app as app_mod
    import chaptertools.gui.librivox_view as view_mod
    for mod in (app_mod, view_mod):
        src = Path(mod.__file__).read_text()
        assert "from chaptertools.sources.librivox_fetch" not in src
        assert "import librivox_fetch" not in src
    assert "chaptertools.sources.librivox_fetch" not in app_mod.__dict__
    assert "chaptertools.sources.librivox_fetch" not in view_mod.__dict__
