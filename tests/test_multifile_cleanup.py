"""Multi-file cleanup keeps first originals outside the library."""
from __future__ import annotations

import json
import os
from pathlib import Path

from chaptertools.audio_quality.worker import replace_library


def test_second_write_keeps_first_original(tmp_path):
    library = tmp_path / "Author" / "Book"
    library.mkdir(parents=True)
    work = tmp_path / "work" / "Author" / "Book"
    work.mkdir(parents=True)
    source = library / "01 - Chapter.mp3"
    source.write_bytes(b"ORIGINAL")
    cleaned1 = work / "01 - Chapter.quality.mp3"
    cleaned1.write_bytes(b"CLEAN1")
    original = replace_library(str(source), str(cleaned1), str(work))
    assert Path(original).read_bytes() == b"ORIGINAL"
    assert source.read_bytes() == b"CLEAN1"
    # Second write must not replace the first original
    cleaned2 = work / "01 - Chapter.quality2.mp3"
    cleaned2.write_bytes(b"CLEAN2")
    again = replace_library(str(source), str(cleaned2), str(work))
    assert again == original
    assert Path(original).read_bytes() == b"ORIGINAL"
    assert source.read_bytes() == b"CLEAN2"


def test_librivox_cleanup_entry_shape(tmp_path):
    folder = tmp_path / "Author" / "Book"
    folder.mkdir(parents=True)
    meta = {"id": 1, "title": "Book", "sections": []}
    path = folder / "librivox.json"
    path.write_text(json.dumps(meta))
    data = json.loads(path.read_text())
    data["cleanup"] = {
        "written": "2026-10-08",
        "preset": "Gentle cleanup",
        "bitrate": "128k",
        "across": "each",
        "files": 2,
    }
    path.write_text(json.dumps(data, indent=2))
    loaded = json.loads(path.read_text())
    assert loaded["cleanup"]["written"] == "2026-10-08"
    assert "original" not in str(folder)  # originals stay in work, not library
