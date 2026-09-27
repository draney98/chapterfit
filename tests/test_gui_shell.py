"""Window shell: one session, one player, log drawer."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("tkinter")


def test_session_player_and_log_drawer(tmp_path):
    if not os.environ.get("DISPLAY"):
        pytest.skip("no display")
    from chaptertools.gui.app import App

    app = App(library_root=str(tmp_path))
    try:
        assert app.session.book is None
        assert app.player_bar is not None
        assert app.log_text is app.log_drawer.text
        assert not app.log_drawer.visible
        names = [app.notebook.tab(t, "text") for t in app.notebook.tabs()]
        assert "Log" not in names
        app.log("hello")
        assert "hello" in app.log_text.get("1.0", "end")
        app.toggle_log()
        assert app.log_drawer.visible
        app.toggle_log()
        assert not app.log_drawer.visible
    finally:
        app.destroy()
