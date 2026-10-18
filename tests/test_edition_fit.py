"""Edition fit labels and plain-language verdict copy."""
from __future__ import annotations

from chaptertools.gui.edition_fit import (
    fit_cell,
    fit_sort_key,
    format_length,
    plain_found_summary,
    provider_catalog_labels,
    reason_sentence,
    verdict_headline,
)


def test_provider_catalog_labels_are_plain():
    labels = provider_catalog_labels()
    assert labels["find-asin"] == "Audible"
    assert labels["all"] == "All catalogs"
    assert "find-asin" not in labels["find-asin"].lower() or labels["find-asin"] == "Audible"
    assert labels["all"] != "ALL"
    assert labels["abs:audible"] == "ABS · audible"


def test_format_length():
    assert format_length(95) == "1:35"
    assert format_length(58) == "0:58"
    assert format_length(None) == ""
    assert format_length(464) == "7:44"


def test_fit_cell_and_sort():
    assert fit_cell(None, checking=True) == "checking…"
    assert fit_cell(None) == "not checked"
    assert fit_cell({"verdict": "consistent", "score": 6.0}).startswith("✓")
    assert "fits" in fit_cell({"verdict": "consistent", "score": 6.0})
    assert "doesn't fit" in fit_cell({"verdict": "rejected", "score": 0.9})
    assert "doesn't fit" in fit_cell({"verdict": "weak", "score": 2.5})

    rows = [
        {"asin": "A", "fit": {"verdict": "rejected", "score": 0.9}},
        {"asin": "B", "fit": {"verdict": "consistent", "score": 6.0}},
        {"asin": "C", "fit": None},
    ]
    ordered = sorted(rows, key=fit_sort_key)
    assert [r["asin"] for r in ordered] == ["B", "A", "C"]


def test_verdict_headline_and_reason():
    assert verdict_headline("consistent") == "Fits this audio"
    assert verdict_headline("rejected") == "Doesn't fit this audio"
    assert verdict_headline(None) == ""

    ident = {
        "verdict": "rejected",
        "duration_ratio": 1.12,
        "published_runtime": 5 * 3600 + 52 * 60,
        "file_runtime": 6 * 3600 + 34 * 60,
        "blocked": [
            "file is 112% of the published runtime - further off than an "
            "edition match should be; worth double-checking the ASIN",
        ],
        "best_trial": {"score": 0.9, "rate_spread": 0.05},
    }
    reason = reason_sentence(ident)
    assert "12 %" in reason or "12%" in reason
    assert "different" in reason.lower()


def test_plain_found_summary():
    text = plain_found_summary({
        "tags": ["title", "artist", "album", "date", "encoder"],
        "embedded_chapters": 0,
        "nfo": True,
    })
    assert "ID3" not in text
    assert "Written as tags at Finish" in text
    assert "title" in text
    assert "no chapters" in text
