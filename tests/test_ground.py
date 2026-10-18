"""The window ground is the palette background, in both modes."""
from __future__ import annotations

import pytest

from tests.gui_pixels import assert_ground_colors
from tests.test_theme_switch import STEPS, _capture, _open


@pytest.mark.parametrize("step", STEPS)
@pytest.mark.parametrize("mode", ("Light", "Dark"))
def test_ground_is_the_palette(tmp_path, monkeypatch, step, mode):
    app = _open(tmp_path, monkeypatch, mode, step)
    path = tmp_path / f"{mode}-{step}.png"
    try:
        _capture(app, path)
    finally:
        app.destroy()
    assert_ground_colors(path, mode)
