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


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def audnex_stainless(fixtures_dir: Path) -> dict:
    path = fixtures_dir / "audnex" / "chapters.B0041M5MRE.us.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)
