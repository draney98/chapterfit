"""Seed an alignment stage from already-placed rows (e.g. album.nfo)."""
from chaptertools.envelope.emit import select_chapters
from chaptertools.gui.stages import placement_rows, seed_alignment


def test_seed_alignment_from_nfo_rows():
    analysis = {
        "identification": {
            "chapters": [{"start": 0.0, "title": "A"},
                         {"start": 100.0, "title": "B"}],
            "label": "NFO",
        },
    }
    nfo = [{"title": "Opening", "start": 0.0},
           {"title": "Chapter 1", "start": 100.0}]
    rows = placement_rows(analysis, nfo_chapters=nfo)
    al = seed_alignment(rows, min_conf=0.5)
    assert al["min_confidence"] == 0.5
    assert al["conflicts"] == []
    assert len(al["results"]) == 2
    assert al["results"][0]["time"] == 0.0
    assert al["results"][0]["title"] == "Opening"
    assert al["results"][0]["confidence"] == 1.0
    kept, withheld = select_chapters(al["results"], 0.5)
    assert len(kept) == 2
    assert withheld == set()


def test_seed_alignment_rejects_unplaced():
    rows = [{"index": 0, "title": "A", "time": 0.0, "confidence": 1.0},
            {"index": 1, "title": "B", "time": None, "confidence": None}]
    assert seed_alignment(rows, min_conf=0.5) is None
