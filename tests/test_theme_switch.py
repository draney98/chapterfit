"""Switching appearance matches starting in that appearance."""
from __future__ import annotations

import json
import os

import pytest

STEPS = ("scan", "match", "place", "finish", "cleanup")


def test_colors_keep_both_modes():
    from chaptertools.gui.theme import active, pair
    from chaptertools.gui.ui_kit import C, hex

    assert C["text"] == pair("text")
    assert hex("text") == active()["text"]
    assert C["bg"][0] != C["bg"][1]


def _book(tmp_path):
    root = tmp_path / "library"
    book = root / "Author" / "Book"
    book.mkdir(parents=True)
    audio = book / "Book.mp3"
    audio.touch()
    return root, audio


def _open(tmp_path, monkeypatch, mode, step):
    tkinter = pytest.importorskip("tkinter")
    try:
        probe = tkinter.Tk()
        probe.destroy()
    except tkinter.TclError:
        pytest.skip("no display available")
    cfg = tmp_path / f"config-{mode}-{step}"
    cfg.mkdir()
    (cfg / "gui.json").write_text(json.dumps({"appearance": mode}))
    monkeypatch.setenv("CHAPTERFIT_CONFIG", str(cfg))
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / f"work-{mode}-{step}"))
    from chaptertools.gui.app import App

    root, audio = _book(tmp_path / f"lib-{mode}-{step}")
    app = App(library_root=str(root), modal_busy=False)
    app.geometry("1280x800+0+0")
    app.workflow.set_book(str(audio))
    work = app.workflow.work_dir
    os.makedirs(work, exist_ok=True)
    chapters = [{"title": f"Chapter {i + 1}", "start": i * 60.0} for i in range(8)]
    results = [{
        "index": i, "title": c["title"], "published": c["start"],
        "time": c["start"] + 0.4, "confidence": 0.9, "pinned": False,
    } for i, c in enumerate(chapters)]
    with open(os.path.join(work, audio.name + ".analysis.json"), "w") as handle:
        json.dump({
            "schema": 3,
            "envelope": {"duration": 500.0},
            "gaps": {"gaps": [{"mid": 60.0, "dur": 2.0}], "populations": []},
            "source": {"timeline_trustworthy": True,
                       "declared_duration": 500.0, "decoded_duration": 500.0},
            "identification": {"label": "Book", "chapters": chapters,
                               "best_trial": {"score": 8.0, "rate_spread": 0.0},
                               "duration_ratio": 1.0},
            "alignment": {"rate": 1.0, "conflicts": [], "results": results},
        }, handle)
    app.workflow.set_book(str(audio))
    app.workflow.show_step(step)
    app.update()
    return app


def _capture(app, path):
    from chaptertools.gui.screenshot import grab_window

    app.geometry("1280x800+0+0")
    app.update_idletasks()
    app.update()
    grab_window(app, str(path))


def _same(left, right) -> float:
    from PIL import Image, ImageChops

    a = Image.open(left).convert("RGB")
    b = Image.open(right).convert("RGB")
    if a.size != b.size:
        return 0.0
    diff = ImageChops.difference(a, b)
    if diff.getbbox() is None:
        return 1.0
    pixels = diff.get_flattened_data() if hasattr(diff, "get_flattened_data") \
        else diff.getdata()
    changed = sum(1 for px in pixels if px != (0, 0, 0))
    return 1.0 - changed / (a.size[0] * a.size[1])


@pytest.mark.parametrize("step", STEPS)
def test_switch_to_dark_matches_starting_dark(tmp_path, monkeypatch, step):
    switched = _open(tmp_path, monkeypatch, "Light", step)
    try:
        switched.set_appearance("Dark")
        switched.update()
        _capture(switched, tmp_path / "switched-dark.png")
    finally:
        switched.destroy()
    started = _open(tmp_path, monkeypatch, "Dark", step)
    try:
        _capture(started, tmp_path / "started-dark.png")
    finally:
        started.destroy()
    ratio = _same(tmp_path / "switched-dark.png", tmp_path / "started-dark.png")
    assert ratio >= 0.99, f"{step} light→dark matches start-in-dark {ratio:.3%}"


@pytest.mark.parametrize("step", STEPS)
def test_switch_to_light_matches_starting_light(tmp_path, monkeypatch, step):
    switched = _open(tmp_path, monkeypatch, "Dark", step)
    try:
        switched.set_appearance("Light")
        switched.update()
        _capture(switched, tmp_path / "switched-light.png")
    finally:
        switched.destroy()
    started = _open(tmp_path, monkeypatch, "Light", step)
    try:
        _capture(started, tmp_path / "started-light.png")
    finally:
        started.destroy()
    ratio = _same(tmp_path / "switched-light.png", tmp_path / "started-light.png")
    assert ratio >= 0.99, f"{step} dark→light matches start-in-light {ratio:.3%}"
