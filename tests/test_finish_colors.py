"""Finish surfaces match the clam window colour (no white/grey islands)."""
from __future__ import annotations

import os
import tkinter as tk

import pytest

pytest.importorskip("tkinter")


def test_finish_write_table_and_plan_use_window_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAPTERFIT_CONFIG", str(tmp_path / "config"))
    if not os.environ.get("DISPLAY"):
        pytest.skip("no display")
    from chaptertools.gui import palette
    from chaptertools.gui.app import App

    app = App(library_root=str(tmp_path))
    try:
        wf = app.workflow
        wf.show_step("finish")
        app.update_idletasks()
        assert str(wf.dry_log.cget("bg")).lower() == palette.WINDOW.lower()
        # Labels bound to the write-table StringVars sit on WINDOW.
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Label):
                    try:
                        if str(child.cget("textvariable")) in (
                                str(wf.chapters_card), str(wf.tags_card),
                                str(wf.cleanup_card)):
                            found.append(str(child.cget("bg")).lower())
                    except tk.TclError:
                        pass
                walk(child)

        walk(wf)
        assert found
        assert all(bg == palette.WINDOW.lower() for bg in found)
    finally:
        app.destroy()
