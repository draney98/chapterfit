"""Both pipelines share one Audnexus cache: chapters.{asin}.{region}.json."""
from __future__ import annotations

import json

import pytest

from chaptertools import audnex
from chaptertools.envelope import identification

PAYLOAD = {"chapters": [{"title": "One", "startOffsetMs": 0,
                         "lengthMs": 1000}], "runtimeLengthMs": 1000}


@pytest.fixture
def net(monkeypatch):
    calls = []

    def fake(asin, region, user_agent=None, timeout=25):
        calls.append((asin, region))
        return PAYLOAD
    monkeypatch.setattr(audnex, "fetch_chapters_payload", fake)
    monkeypatch.setattr(audnex, "fetch_book_meta",
                        lambda *a, **k: ("Title", "Author"))
    return calls


def test_both_pipelines_write_and_read_the_canonical_name(tmp_path, net):
    identification.fetch_chapters("B01", "us", str(tmp_path))
    assert (tmp_path / "chapters.B01.us.json").is_file()
    book = audnex.fetch_audnex("B01", "us", str(tmp_path))
    assert [c.title for c in book.chapters] == ["One"]
    assert net == [("B01", "us")]  # the second pipeline hit the cache


def test_silence_pipeline_writes_canonical_name(tmp_path, net):
    book = audnex.fetch_audnex("B02", "uk", str(tmp_path))
    assert book.title == "Title"
    assert (tmp_path / "chapters.B02.uk.json").is_file()
    assert not (tmp_path / "B02.uk.chapters.json").exists()


def test_legacy_name_still_read(tmp_path, net, capsys):
    (tmp_path / "B03.us.chapters.json").write_text(json.dumps(PAYLOAD))
    assert identification.fetch_chapters("B03", "us", str(tmp_path),
                                         offline=True) == PAYLOAD
    assert "B03.us.chapters.json" in capsys.readouterr().out
    assert net == []


def test_offline_without_cache_exits(tmp_path, net):
    with pytest.raises(SystemExit):
        identification.fetch_chapters("B04", "us", str(tmp_path), offline=True)
    assert net == []
