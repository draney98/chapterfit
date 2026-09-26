"""Tests for the Kodi-style album.nfo writer."""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET

import pytest

from chaptertools.nfo import (
    chapters_differ,
    chapters_from_file,
    fmt_duration,
    nfo_would_replace,
    parse_nfo_duration,
    read_nfo_chapters,
    render_nfo,
)

SAMPLE_NFO = """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<album>
  <review />
  <outline />
  <lockdata>false</lockdata>
  <dateadded>2026-09-01 21:44:55</dateadded>
  <title>03 - Book 1 - Foundation - Isaac Asimov - 1951</title>
  <year>1951</year>
  <runtime>519</runtime>
  <artist>Top 100 Sci-Fi Books</artist>
  <albumartist>Top 100 Sci-Fi Books</albumartist>
  <musicbrainzalbumid>abcd-1234</musicbrainzalbumid>
  <track>
    <title>03 - Book 1 - Foundation - Isaac Asimov - 1951</title>
    <duration>39:04</duration>
  </track>
</album>
"""

CHAPTERS = [
    {"title": "Opening Credits", "start": 0.0, "end": 20.5},
    {"title": "Chapter One", "start": 20.5, "end": 3904.0},
    {"title": "Chapter Two", "start": 3904.0, "end": 7523.0},
]


MULTI_NFO = """<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<album>
  <title>Revelation Space</title>
  <artist>Alastair Reynolds</artist>
  <track>
    <title>Opening Credits</title>
    <duration>0:25</duration>
  </track>
  <track>
    <title>One: Mantell Sector</title>
    <duration>37:36</duration>
  </track>
  <track>
    <title>Two</title>
    <duration>2:05:23</duration>
  </track>
</album>
"""


def test_parse_nfo_duration():
    assert parse_nfo_duration("37:36") == 37 * 60 + 36
    assert parse_nfo_duration("2:05:23") == 2 * 3600 + 5 * 60 + 23
    assert parse_nfo_duration("0:25") == 25
    assert parse_nfo_duration("") == 0
    assert parse_nfo_duration("nope") == 0


def test_read_nfo_chapters_accumulates_starts(tmp_path):
    nfo = tmp_path / "album.nfo"
    nfo.write_text(MULTI_NFO, encoding="utf-8")
    chapters = read_nfo_chapters(str(nfo))
    assert [c["title"] for c in chapters] == [
        "Opening Credits", "One: Mantell Sector", "Two"]
    assert chapters[0]["start"] == 0
    assert chapters[0]["end"] == 25
    assert chapters[1]["start"] == 25
    assert chapters[1]["end"] == 25 + 37 * 60 + 36
    assert chapters[2]["start"] == chapters[1]["end"]


def test_read_nfo_chapters_skips_single_track_album(tmp_path):
    nfo = tmp_path / "album.nfo"
    nfo.write_text(SAMPLE_NFO, encoding="utf-8-sig")
    assert read_nfo_chapters(str(nfo)) == []


def test_read_nfo_chapters_missing_or_broken(tmp_path):
    assert read_nfo_chapters(str(tmp_path / "missing.nfo")) == []
    bad = tmp_path / "bad.nfo"
    bad.write_text("<album><track>not closed", encoding="utf-8")
    assert read_nfo_chapters(str(bad)) == []


def test_chapters_differ_allows_whole_second_rounding():
    a = [{"title": "One", "start": 0.0, "end": 25.4},
         {"title": "Two", "start": 25.4, "end": 100.2}]
    b = [{"title": "One", "start": 0.0, "end": 25.0},
         {"title": "Two", "start": 25.0, "end": 100.0}]
    assert chapters_differ(a, b) is False
    assert chapters_differ(a, [{**b[0], "title": "Other"}, b[1]]) is True
    assert chapters_differ(a, b[:1]) is True


def test_nfo_would_replace_only_when_chapter_lists_differ(tmp_path):
    nfo = tmp_path / "album.nfo"
    nfo.write_text(MULTI_NFO, encoding="utf-8")
    same = read_nfo_chapters(str(nfo))
    assert nfo_would_replace(str(nfo), same) is False
    other = [{"title": "Different", "start": 0.0, "end": 10.0},
             {"title": "Also", "start": 10.0, "end": 20.0}]
    assert nfo_would_replace(str(nfo), other) is True
    single = tmp_path / "one.nfo"
    single.write_text(SAMPLE_NFO, encoding="utf-8-sig")
    assert nfo_would_replace(str(single), other) is False


def test_write_refuses_nfo_overwrite_without_force(tmp_path, monkeypatch):
    from chaptertools.nfo import write_album_nfo

    monkeypatch.setattr("chaptertools.nfo.chapters_from_file",
                        lambda _path: [
                            {"title": "A", "start": 0.0, "end": 10.0},
                            {"title": "B", "start": 10.0, "end": 40.0}])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    book = tmp_path / "Author" / "Book"
    book.mkdir(parents=True)
    nfo = book / "album.nfo"
    nfo.write_text(MULTI_NFO, encoding="utf-8")
    before = nfo.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        write_album_nfo(str(book), "unused.mp3", apply=True)
    assert nfo.read_text(encoding="utf-8") == before

    n = write_album_nfo(str(book), "unused.mp3", apply=True, force=True)
    assert n == 2
    titles = [t.findtext("title")
              for t in ET.parse(nfo).getroot().findall("track")]
    assert titles == ["A", "B"]


def test_fmt_duration():
    assert fmt_duration(0) == "0:00"
    assert fmt_duration(2344) == "39:04"
    assert fmt_duration(7523) == "2:05:23"


def test_render_replaces_tracks_and_keeps_metadata(tmp_path):
    nfo = tmp_path / "album.nfo"
    nfo.write_text(SAMPLE_NFO, encoding="utf-8-sig")

    out = render_nfo(str(nfo), CHAPTERS, "unused", "unused")
    root = ET.fromstring(out)

    assert root.tag == "album"
    assert root.findtext("year") == "1951"
    assert root.findtext("musicbrainzalbumid") == "abcd-1234"
    assert root.findtext("runtime") == "125"
    tracks = root.findall("track")
    assert len(tracks) == 3
    assert tracks[0].findtext("title") == "Opening Credits"
    assert tracks[1].findtext("duration") == "1:04:44"


def test_render_fresh_when_no_existing(tmp_path):
    out = render_nfo(None, CHAPTERS, "The Stars My Destination",
                     "Alfred Bester")
    root = ET.fromstring(out)

    assert root.tag == "album"
    assert root.findtext("title") == "The Stars My Destination"
    assert root.findtext("artist") == "Alfred Bester"
    assert root.findtext("albumartist") == "Alfred Bester"
    assert len(root.findall("track")) == 3


def test_render_fresh_when_existing_is_broken(tmp_path):
    bad = tmp_path / "bad.nfo"
    bad.write_text("<album><track>not closed", encoding="utf-8")
    root = ET.fromstring(render_nfo(str(bad), CHAPTERS, "T", "A"))
    assert root.findtext("title") == "T"
    assert len(root.findall("track")) == 3


@pytest.mark.integration
def test_write_album_nfo_end_to_end(tmp_path):
    from chaptertools.nfo import write_album_nfo

    book = tmp_path / "Alfred Bester" / "The Demolished Man"
    book.mkdir(parents=True)
    nfo = book / "album.nfo"
    nfo.write_text(SAMPLE_NFO, encoding="utf-8-sig")

    # a small chaptered file, emit-style (mp3 keeps ID3 chapters)
    audio = tmp_path / "tone.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=10",
                    "-t", "10", str(audio), "-y"],
                   check=True, capture_output=True)
    ffmeta = tmp_path / "ch.txt"
    ffmeta.write_text(
        ";FFMETADATA1\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=3000\nTITLE=Part One\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=3000\nEND=7000\nTITLE=Part Two\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=7000\nEND=10000\nTITLE=Part Three\n")
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio),
                    "-i", str(ffmeta), "-map_metadata", "1",
                    "-map_chapters", "1", "-c:a", "libmp3lame",
                    str(tmp_path / "chaptered.mp3"), "-y"],
                   check=True, capture_output=True)

    # dry run: reports, does not touch anything
    n = write_album_nfo(str(book), str(tmp_path / "chaptered.mp3"),
                        apply=False)
    assert n == 3
    assert nfo.read_text(encoding="utf-8-sig") == SAMPLE_NFO
    assert not (book / "album.nfo.bak").exists()

    # apply: backup + rewrite
    n = write_album_nfo(str(book), str(tmp_path / "chaptered.mp3"), apply=True)
    assert n == 3
    assert (book / "album.nfo.bak").read_text(
        encoding="utf-8-sig") == SAMPLE_NFO
    raw = nfo.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf<?xml")
    root = ET.fromstring(raw.decode("utf-8-sig"))
    assert [t.findtext("title") for t in root.findall("track")] == \
        ["Part One", "Part Two", "Part Three"]

    # a second apply must not clobber the first backup
    write_album_nfo(str(book), str(tmp_path / "chaptered.mp3"), apply=True)
    assert (book / "album.nfo.bak").read_text(
        encoding="utf-8-sig") == SAMPLE_NFO


@pytest.mark.integration
def test_chapters_from_file_requires_two(tmp_path):
    from chaptertools.nfo import write_album_nfo

    book = tmp_path / "Author" / "Book"
    book.mkdir(parents=True)
    bare = tmp_path / "bare.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=2",
                    "-t", "2", str(bare), "-y"],
                   check=True, capture_output=True)
    assert chapters_from_file(str(bare)) == []
    assert write_album_nfo(str(book), str(bare), apply=True) == 0
    assert not (book / "album.nfo").exists()


@pytest.mark.integration
def test_fresh_nfo_uses_emitted_tags(tmp_path):
    """write_album_nfo prefers emitted title/artist over folder names."""
    from chaptertools.nfo import write_album_nfo

    book = tmp_path / "Folder Author" / "Folder Title"
    book.mkdir(parents=True)
    audio = tmp_path / "chaptered.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=3", "-t", "3",
                    str(audio), "-y"], check=True, capture_output=True)
    ffmeta = tmp_path / "m.txt"
    ffmeta.write_text(
        ";FFMETADATA1\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1500\nTITLE=A\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1500\nEND=3000\nTITLE=B\n")
    muxed = tmp_path / "muxed.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio),
                    "-i", str(ffmeta), "-map_chapters", "1",
                    "-c:a", "libmp3lame", str(muxed), "-y"],
                   check=True, capture_output=True)
    muxed.replace(audio)

    write_album_nfo(str(book), str(audio), apply=True,
                    title="Real Title", artist="Real Author")
    root = ET.parse(book / "album.nfo").getroot()
    assert root.findtext("title") == "Real Title"
    assert root.findtext("artist") == "Real Author"
    assert len(root.findall("track")) == 2

    # without overrides: folder names
    book2 = tmp_path / "Other Author" / "Other Title"
    book2.mkdir(parents=True)
    write_album_nfo(str(book2), str(audio), apply=True)
    root2 = ET.parse(book2 / "album.nfo").getroot()
    assert root2.findtext("title") == "Other Title"
    assert root2.findtext("artist") == "Other Author"
