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
        assert str(wf.dry_log.cget("fg_color")).lower() == palette.WINDOW.lower()
        # Write-table values sit on the themed card, not a stray white island.
        wanted = {str(wf.chapters_card), str(wf.tags_card), str(wf.cleanup_card)}
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if type(child).__name__ == "CTkLabel":
                    try:
                        if str(child.cget("textvariable")) in wanted:
                            found.append(str(child.master.cget("fg_color")).lower())
                    except (tk.TclError, ValueError):
                        pass
                walk(child)

        walk(wf)
        assert found
        assert all(bg == palette.RAIL_IDLE.lower() for bg in found)
    finally:
        app.destroy()
