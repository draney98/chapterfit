"""Tests for the Audiobookshelf client's response handling (no network)."""
from __future__ import annotations

import io
import urllib.error

import pytest

from chaptertools.abs_client import ABS, metadata_from_emitted_tags


class FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _client(monkeypatch, payload: bytes) -> ABS:
    api = ABS("http://localhost:1", "secret-token")
    monkeypatch.setattr(
        "chaptertools.abs_client.urllib.request.urlopen",
        lambda req, timeout: FakeResponse(payload))
    return api


def test_req_parses_json_body(monkeypatch):
    api = _client(monkeypatch, b'{"libraries": []}')
    assert api.libraries() == []


def test_req_tolerates_non_json_2xx(monkeypatch):
    """ABS scan endpoints may answer 200 with plain text; _req must not raise."""
    api = _client(monkeypatch, b"Library scan started\n")
    assert api.scan_library("lib-1") == {}


def test_req_tolerates_empty_2xx(monkeypatch):
    api = _client(monkeypatch, b"")
    assert api.scan_library("lib-1") == {}


def test_http_errors_still_raise(monkeypatch):
    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 500, "boom", {},
                                     io.BytesIO(b"exploded"))

    monkeypatch.setattr(
        "chaptertools.abs_client.urllib.request.urlopen", boom)
    api = ABS("http://localhost:1", "secret-token")
    with pytest.raises(RuntimeError, match="HTTP 500"):
        api.ping()


def test_metadata_from_emitted_tags():
    assert metadata_from_emitted_tags({}) == {}
    assert metadata_from_emitted_tags({"genre": "Sci-Fi"}) == {}
    meta = metadata_from_emitted_tags({
        "title": "Revelation Space",
        "artist": "Alastair Reynolds",
        "date": "2009",
    })
    assert meta["title"] == "Revelation Space"
    assert meta["authorName"] == "Alastair Reynolds"
    assert meta["authors"] == [{"name": "Alastair Reynolds"}]
    assert meta["publishedYear"] == "2009"


def test_set_metadata_patches_media(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["method"] = req.get_method()
        seen["url"] = req.full_url
        seen["body"] = req.data
        return FakeResponse(b'{"updated": true}')

    monkeypatch.setattr(
        "chaptertools.abs_client.urllib.request.urlopen", fake_urlopen)
    api = ABS("http://localhost:1", "secret-token")
    api.set_metadata("item-1", {"title": "Revelation Space"})
    assert seen["method"] == "PATCH"
    assert seen["url"].endswith("/api/items/item-1/media")
    assert b'"title": "Revelation Space"' in seen["body"]
