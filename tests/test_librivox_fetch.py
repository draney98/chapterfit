"""Fixture tests for librivox search/fetch. No network calls."""
from __future__ import annotations

import json
import os
import urllib.error
from pathlib import Path
from unittest import mock

import pytest

from chaptertools.sources import librivox_fetch as lv

FIX = Path(__file__).parent / "fixtures" / "librivox"


def _load(name: str) -> dict:
    with open(FIX / name) as handle:
        return json.load(handle)


def test_search_splits_unfinished(monkeypatch):
    payload = _load("search_pride.json")

    def fake_json(url: str, retries: int = 3):
        assert "title=%5E" in url or "title=^" in url or "%5EPride" in url or "Pride" in url
        assert "extended=1" in url
        return payload

    monkeypatch.setattr(lv, "_http_json", fake_json)
    result = lv.search_catalog("Pride")
    assert len(result["hits"]) == 1
    assert result["hits"][0]["id"] == 77
    assert result["hits"][0]["sections"] == 2
    assert result["hits"][0]["readers"] == ["Karen Savage"]
    assert len(result["unfinished"]) == 1
    assert result["unfinished"][0]["id"] == 9999
    assert "sections" not in result["unfinished"][0]


def test_search_title_plus_author_does_not_query_both(monkeypatch):
    """LibriVox HTTP 500s if title and author are in the same request."""
    payload = _load("search_pride.json")
    payload["books"].append({
        "id": "88",
        "title": "Pride of the other author",
        "language": "English",
        "url_iarchive": "https://archive.org/details/other",
        "authors": [{"first_name": "Charlotte", "last_name": "Bronte"}],
        "sections": [{"section_number": "1", "title": "One", "playtime": "60",
                      "readers": [{"display_name": "X"}]}],
    })
    urls = []

    def fake_json(url: str, retries: int = 3):
        urls.append(url)
        assert "author=" not in url
        return payload

    monkeypatch.setattr(lv, "_http_json", fake_json)
    result = lv.search_catalog("Pride", "Austen")
    assert urls and "title=" in urls[0]
    assert [h["id"] for h in result["hits"]] == [77]
    assert [u["id"] for u in result["unfinished"]] == [9999]


def test_search_http_500_is_error_not_raise(monkeypatch):
    def boom(url: str, retries: int = 3):
        raise urllib.error.HTTPError(url, 500, "Internal Server Error", {}, None)

    monkeypatch.setattr(lv, "_http_json", boom)
    result = lv.search_catalog("Pride")
    assert result["event"] == "error"
    assert "500" in result["error"]
    assert result["hits"] == []
    assert result["unfinished"] == []


def test_search_unreachable_is_error_not_raise(monkeypatch):
    def boom(url: str, retries: int = 3):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(lv, "_http_json", boom)
    result = lv.search_catalog("Pride")
    assert result["event"] == "error"
    assert "LibriVox" in result["error"]
    assert result["hits"] == []


def test_print_search_error_exits_nonzero(capsys):
    code = lv.print_search(
        {"event": "error", "error": "LibriVox HTTP 500",
         "hits": [], "unfinished": []},
        as_json=True)
    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["event"] == "error"
    assert "500" in out["error"]


def test_plan_default_set_and_zero_padding(tmp_path):
    book = _load("book_starts_at_zero.json")["books"]
    ia = _load("ia_zero.json")
    plan = lv.build_plan(817, str(tmp_path), book=book, ia=ia)
    assert plan["event"] == "plan"
    assert plan["set"] == "128Kbps MP3"
    assert plan["section_count"] == 2
    assert plan["playlist_count"] == 2
    assert plan["files"][0]["section_number"] == 0
    assert plan["files"][0]["dest"].endswith("00 - Introduction.mp3")
    assert plan["files"][1]["dest"].endswith("01 - Chapter One.mp3")


def test_plan_count_mismatch_refuses(tmp_path):
    book = _load("book_77.json")["books"]
    ia = _load("ia_mismatch_count.json")
    plan = lv.build_plan(77, str(tmp_path), book=book, ia=ia)
    assert plan["event"] == "refused"
    assert plan["reason"] == "count_mismatch"
    assert plan["playlist_count"] == 3
    assert plan["section_count"] == 2


def test_plan_number_mismatch_refuses(tmp_path):
    book = _load("book_77.json")["books"]
    ia = _load("ia_number_disagree.json")
    plan = lv.build_plan(77, str(tmp_path), book=book, ia=ia)
    assert plan["event"] == "refused"
    assert plan["reason"] == "number_mismatch"
    assert plan["catalog_numbers"] == [1, 2]
    assert 5 in plan["filename_numbers"]


def test_fetch_plan_only_no_download(tmp_path):
    book = _load("book_77.json")["books"]
    ia = _load("ia_pride.json")
    events = []

    def download(*_a, **_k):
        raise AssertionError("download must not run for --plan-only")

    code = lv.fetch_book(
        77, str(tmp_path), plan_only=True, book=book, ia=ia,
        emit=events.append, download_fn=download)
    assert code == 0
    assert events[0]["event"] == "plan"
    assert len(events) == 1


def test_fetch_refused_downloads_nothing(tmp_path):
    book = _load("book_77.json")["books"]
    ia = _load("ia_mismatch_count.json")
    events = []

    def download(*_a, **_k):
        raise AssertionError("must not download")

    code = lv.fetch_book(
        77, str(tmp_path), book=book, ia=ia,
        emit=events.append, download_fn=download)
    assert code == 2
    assert events[0]["event"] == "refused"


def test_fetch_tags_and_sidecars(tmp_path, monkeypatch):
    book = _load("book_77.json")["books"]
    ia = _load("ia_pride.json")
    events = []
    tagged = []

    def fake_download(url, dest, **_k):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as handle:
            handle.write(b"ID3fake")
        return {"bytes": 7, "total": 7, "resumed_from": 0}

    def fake_tag(path, **kwargs):
        tagged.append((path, kwargs))

    monkeypatch.setattr(lv, "_tag_file", fake_tag)
    code = lv.fetch_book(
        77, str(tmp_path), book=book, ia=ia,
        emit=events.append, download_fn=fake_download)
    assert code == 0
    assert events[-1]["event"] == "done"
    assert tagged[0][1]["track"] == 1
    assert tagged[1][1]["track"] == 2
    dest = events[0]["dest"]
    assert (Path(dest) / "librivox.json").is_file()
    assert (Path(dest) / "album.nfo").is_file()
    meta = json.loads((Path(dest) / "librivox.json").read_text())
    assert meta["id"] == 77
    assert meta["set"] == "128Kbps MP3"
    assert meta["sections"][0]["section_number"] == 1
    assert (Path(dest) / "01 - Chapter 01.mp3").is_file()


def test_fetch_resume_reports_resumed_from(tmp_path, monkeypatch):
    book = _load("book_77.json")["books"]
    ia = _load("ia_pride.json")
    events = []

    def fake_download(url, dest, **_k):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as handle:
            handle.write(b"more")
        return {"bytes": 100, "total": 100, "resumed_from": 40}

    monkeypatch.setattr(lv, "_tag_file", lambda *a, **k: None)
    lv.fetch_book(
        77, str(tmp_path), book=book, ia=ia,
        emit=events.append, download_fn=fake_download)
    done = [e for e in events if e.get("event") == "file" and e.get("state") == "done"]
    assert done[0]["resumed_from"] == 40


def test_gui_does_not_import_librivox_fetch():
    import chaptertools.gui.app as app_mod
    import sys
    # Ensure the module name is not pulled in by gui.app import chain
    # (a fresh check: sources.librivox_fetch must not be a required dep of app)
    src = Path(app_mod.__file__).read_text()
    assert "librivox_fetch" not in src


def test_cli_help_lists_librivox():
    from chaptertools.cli import chapterfit as cli
    with mock.patch("sys.argv", ["chapterfit", "librivox", "search", "--help"]):
        with pytest.raises(SystemExit) as caught:
            cli.main()
    assert caught.value.code == 0
