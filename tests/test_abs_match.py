"""ABS Match provider → find_asin candidates (no network)."""
from __future__ import annotations

import json

import pytest

from chaptertools.abs_client import ABS
from chaptertools.sources import abs_match, find_asin
from chaptertools.sources.abs_match import (
    candidate_from_abs_hit,
    candidates_from_abs_hits,
    is_audible_asin,
    merge_by_asin,
    provider_choices,
)


def test_is_audible_asin():
    assert is_audible_asin("B002V5GYHA")
    assert is_audible_asin("b002v5gyha")
    assert not is_audible_asin("OL123M")
    assert not is_audible_asin("")
    assert not is_audible_asin(None)
    assert not is_audible_asin("B002")


def test_candidate_from_abs_hit_maps_and_drops_non_asin():
    hit = {
        "asin": "b002v5gyha",
        "title": "The Lathe of Heaven",
        "subtitle": "A Novel",
        "author": "Ursula K. Le Guin",
        "narrator": "George Guidall",
        "duration": 7200,
        "publishedYear": "2008",
        "publisher": "Recorded Books",
        "language": "english",
    }
    cand = candidate_from_abs_hit(hit, "abs:audible")
    assert cand is not None
    assert cand.asin == "B002V5GYHA"
    assert cand.title == "The Lathe of Heaven"
    assert cand.subtitle == "A Novel"
    assert cand.authors == ["Ursula K. Le Guin"]
    assert cand.narrators == ["George Guidall"]
    assert cand.runtime_min == 120
    assert cand.release == "2008"
    assert cand.source == "abs:audible"

    assert candidate_from_abs_hit({"asin": "OL123M", "title": "X"},
                                  "abs:openlibrary") is None
    assert candidate_from_abs_hit({"title": "No id"}, "abs:google") is None


def test_candidates_from_abs_hits_filters():
    hits = [
        {"asin": "B002V5GYHA", "title": "A"},
        {"asin": "itunes:99", "title": "B"},
        {"title": "C"},
        {"asin": "B00Y18C77Q", "title": "D", "authors": [{"name": "Huxley"}]},
    ]
    out = candidates_from_abs_hits(hits, "abs:google")
    assert [c.asin for c in out] == ["B002V5GYHA", "B00Y18C77Q"]
    assert out[1].authors == ["Huxley"]


def test_merge_by_asin_keeps_best_score():
    a = find_asin.Candidate(
        asin="B002V5GYHA", title="A", subtitle=None, authors=[],
        narrators=[], runtime_min=100, release=None, publisher=None,
        language=None, source="find-asin", score=0.5)
    b = find_asin.Candidate(
        asin="b002v5gyha", title="B", subtitle=None, authors=[],
        narrators=[], runtime_min=100, release=None, publisher=None,
        language=None, source="abs:audible", score=0.9)
    c = find_asin.Candidate(
        asin="B00Y18C77Q", title="C", subtitle=None, authors=[],
        narrators=[], runtime_min=90, release=None, publisher=None,
        language=None, source="abs:google", score=0.2)
    merged = merge_by_asin([[a], [b, c]])
    assert [m.asin for m in merged] == ["B002V5GYHA", "B00Y18C77Q"]
    assert merged[0].source == "abs:audible"
    assert merged[0].score == 0.9


def test_provider_choices():
    choices = provider_choices()
    assert choices[0] == "find-asin"
    assert choices[-1] == "all"
    assert "abs:audible" in choices
    assert "abs:audiobookcovers" not in choices


def test_search_books_parses_list(monkeypatch):
    payload = json.dumps([
        {"asin": "B002V5GYHA", "title": "Lathe"},
        {"title": "no asin"},
    ]).encode()

    class FakeResponse:
        def __init__(self, body):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        return FakeResponse(payload)

    monkeypatch.setattr(
        "chaptertools.abs_client.urllib.request.urlopen", fake_urlopen)
    api = ABS("http://abs.example", "tok")
    hits = api.search_books("audible", "Lathe", "Le Guin", region="us")
    assert "provider=audible" in seen["url"]
    assert "title=Lathe" in seen["url"]
    assert "author=Le+Guin" in seen["url"] or "author=Le%20Guin" in seen["url"]
    assert len(hits) == 2
    assert hits[0]["asin"] == "B002V5GYHA"


def test_search_abs_provider_mocked(monkeypatch, tmp_path):
    audio = tmp_path / "book.mp3"
    audio.write_bytes(b"")

    class FakeABS:
        def search_books(self, provider, title, author="", region=""):
            assert provider == "audible"
            assert title == "Lathe"
            return [
                {"asin": "B002V5GYHA", "title": "The Lathe of Heaven",
                 "author": "Ursula K. Le Guin", "durationMin": 480},
                {"asin": "OL1", "title": "skip"},
            ]

    monkeypatch.setattr(
        "chaptertools.abs_client.abs_client_from_env",
        lambda *a, **k: FakeABS())
    cands = find_asin.search_abs_provider(
        "abs:audible", "Lathe", "Le Guin", None, "us", str(audio), False)
    assert len(cands) == 1
    assert cands[0].asin == "B002V5GYHA"
    assert cands[0].source == "abs:audible"
    assert cands[0].runtime_min == 480


def test_collect_candidates_all_dedupes(monkeypatch, tmp_path):
    audio = tmp_path / "book.mp3"
    audio.write_bytes(b"")

    def fake_audible(title, author, keywords, region, num, verbose):
        return [find_asin.Candidate(
            asin="B002V5GYHA", title="from-find", subtitle=None, authors=[],
            narrators=[], runtime_min=100, release=None, publisher=None,
            language=None, source="find-asin", score=0.0)]

    class FakeABS:
        def search_books(self, provider, title, author="", region=""):
            if provider == "audible":
                return [{"asin": "B002V5GYHA", "title": "from-abs"}]
            if provider == "google":
                return [{"asin": "B00Y18C77Q", "title": "other"}]
            return []

    monkeypatch.setattr(find_asin, "search_audible", fake_audible)
    monkeypatch.setattr(
        "chaptertools.abs_client.abs_client_from_env",
        lambda *a, **k: FakeABS())
    # Limit ABS providers so the test stays small.
    monkeypatch.setattr(abs_match, "ABS_BOOK_PROVIDERS", ("audible", "google"))

    cands = find_asin.collect_candidates(
        "all", "Lathe", "Le Guin", None, "us", 20, str(audio), False)
    asins = {c.asin: c for c in cands}
    assert set(asins) == {"B002V5GYHA", "B00Y18C77Q"}
    # Equal scores keep the first (find-asin) hit.
    assert asins["B002V5GYHA"].source == "find-asin"
    assert asins["B00Y18C77Q"].source == "abs:google"


def test_abs_client_from_env_requires_creds(monkeypatch, tmp_path):
    from chaptertools import abs_client

    monkeypatch.setattr(
        abs_client, "load_env",
        lambda *a, **k: {"_source": str(tmp_path / ".env")})
    with pytest.raises(RuntimeError, match="ABS_URL and ABS_TOKEN"):
        abs_client.abs_client_from_env(str(tmp_path / "x.mp3"))
