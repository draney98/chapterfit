"""Shared pytest fixtures. Tests must not call live network, ABS, or full-book ffmpeg."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _private_work_root(tmp_path_factory, monkeypatch):
    """No test may write into the user's real ~/.local/share/chapterfit."""
    monkeypatch.setenv("CHAPTERFIT_WORK",
                       str(tmp_path_factory.mktemp("work-root")))


@pytest.fixture(autouse=True)
def _private_log(tmp_path, monkeypatch):
    from chaptertools import file_log
    monkeypatch.setattr(file_log, "LOG_PATH", str(tmp_path / "chapterfit.log"))
    file_log.reset_for_tests()
    yield
    file_log.reset_for_tests()


@pytest.fixture(autouse=True)
def dialogs(monkeypatch):
    """Every Tk dialog fails fast unless the test queued an answer.

    Patch the tkinter modules themselves, so `from tkinter import messagebox`
    in any GUI module sees the stub.
    """
    import tkinter.filedialog as fd
    import tkinter.messagebox as mb
    import tkinter.simpledialog as sd

    class Dialogs:
        def __init__(self):
            self.answers, self.seen = [], []

        def answer(self, *values):  # tests queue answers in order
            self.answers.extend(values)

        def _ask(self, kind, title=None, message=None, *a, **k):
            self.seen.append((kind, title, message))
            if kind.startswith("show"):
                return "ok"  # info/warning/error just get logged
            if not self.answers:
                raise AssertionError(
                    f"unexpected dialog {kind}: {title!r} {message!r}")
            return self.answers.pop(0)

    d = Dialogs()
    for name in ("showinfo", "showwarning", "showerror",
                 "askyesno", "askokcancel", "askyesnocancel",
                 "askretrycancel", "askquestion"):
        monkeypatch.setattr(
            mb, name, lambda *a, _n=name, **k: d._ask(_n, *a, **k))
    for name in ("askstring", "askinteger", "askfloat"):
        monkeypatch.setattr(
            sd, name, lambda *a, _n=name, **k: d._ask(_n, *a, **k))
    for name in ("askopenfilename", "asksaveasfilename", "askdirectory",
                 "askopenfilenames"):
        monkeypatch.setattr(
            fd, name, lambda *a, _n=name, **k: d._ask(_n, *a, **k))
    return d


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def audnex_stainless(fixtures_dir: Path) -> dict:
    path = fixtures_dir / "audnex" / "chapters.B0041M5MRE.us.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)
