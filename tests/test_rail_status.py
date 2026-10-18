"""Place / Finish rail status derived from alignment data."""
from chaptertools.gui.stages import (
    ATTENTION,
    BLOCKED,
    DONE,
    PENDING,
    DiskFacts,
    finish_step_status,
    place_step_status,
    short_edition_label,
    stage_states,
)


def _placed_book(n=2, conf=1.0):
    return {
        "envelope": {"frames": 1},
        "calibration": {"threshold_db": -60},
        "gaps": {"gaps": [1]},
        "fingerprint": {"longest_gap": 1},
        "source": {"timeline_trustworthy": True},
        "identification": {
            "best_trial": {"score": 6.0},
            "chapters": list(range(n)),
            "label": "/home/oem/book/chapters.nfo.json",
        },
        "alignment": {
            "results": [
                {"index": i, "time": float(i * 100), "confidence": conf,
                 "pinned": False}
                for i in range(n)
            ],
            "conflicts": [],
        },
    }


def test_short_edition_label_strips_paths():
    assert short_edition_label("/a/b/chapters.nfo.json") == "NFO"
    assert short_edition_label("B002V5GYHA") == "B002V5GYHA"
    assert short_edition_label("Audnexus B002V5GYHA") == "B002V5GYHA"
    assert short_edition_label(None) == ""


def test_place_done_when_every_row_placed_clean():
    analysis = _placed_book()
    place = place_step_status(analysis, 0.5)
    assert place["state"] == DONE
    assert place["detail"] == "2 placed · 0 to check"


def test_place_blocked_only_without_edition():
    place = place_step_status({}, 0.5)
    assert place["state"] == BLOCKED
    assert "Match" in place["detail"]


def test_place_attention_when_low_confidence():
    analysis = _placed_book(conf=0.2)
    place = place_step_status(analysis, 0.5)
    assert place["state"] == ATTENTION
    assert "1 to check" in place["detail"] or "2 to check" in place["detail"]


def test_place_done_from_nfo_when_alignment_missing():
    """Table shows album.nfo times as placed; rail must match."""
    analysis = {
        "identification": {
            "best_trial": {"score": 6.0},
            "chapters": [{"start": 0.0}, {"start": 100.0}],
            "label": "chapters.nfo.json",
        },
    }
    nfo = [{"title": "A", "start": 0.0}, {"title": "B", "start": 100.0}]
    place = place_step_status(analysis, 0.5, nfo_chapters=nfo)
    assert place["state"] == DONE
    assert place["detail"] == "2 placed · 0 to check"
    fin = finish_step_status(place, {"state": PENDING, "detail": "needs a dry run"})
    assert fin["state"] == PENDING
    assert fin["detail"] == "needs a dry run"


def test_finish_waits_on_place_only_when_place_not_done():
    place = place_step_status({}, 0.5)
    fin = finish_step_status(place, {"state": BLOCKED, "detail": ""})
    assert fin["state"] == BLOCKED
    assert fin["detail"] == "waiting on Place"

    analysis = _placed_book()
    place = place_step_status(analysis, 0.5)
    fin = finish_step_status(place, {"state": PENDING, "detail": "needs a dry run"})
    assert place["state"] == DONE
    assert fin["state"] == PENDING
    assert fin["detail"] == "needs a dry run"


def test_identify_detail_never_contains_a_path():
    analysis = _placed_book()
    states = stage_states(analysis, 0.5, DiskFacts())
    detail = states["identify"]["detail"]
    assert "chapters" in detail
    assert "/" not in detail
    assert "NFO" in detail
