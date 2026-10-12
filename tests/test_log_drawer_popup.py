"""Log drawer context menu always releases its grab."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_popup_menu_releases_grab():
    from chaptertools.gui.log_drawer import LogDrawer

    released = []
    drawer = LogDrawer.__new__(LogDrawer)
    drawer._menu = SimpleNamespace(
        tk_popup=lambda x, y: None,
        grab_release=lambda: released.append(True),
    )
    drawer._popup_menu(SimpleNamespace(x_root=10, y_root=20))
    assert released == [True]


def test_popup_menu_releases_grab_even_when_popup_fails():
    from chaptertools.gui.log_drawer import LogDrawer

    released = []

    def boom(_x, _y):
        raise RuntimeError("popup failed")

    drawer = LogDrawer.__new__(LogDrawer)
    drawer._menu = SimpleNamespace(
        tk_popup=boom,
        grab_release=lambda: released.append(True),
    )
    with pytest.raises(RuntimeError, match="popup failed"):
        drawer._popup_menu(SimpleNamespace(x_root=1, y_root=2))
    assert released == [True]
