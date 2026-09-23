"""Tests for the Kodi-style album.nfo writer."""
from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET

import pytest

from chaptertools.nfo import chapters_from_file, fmt_duration, render_nfo

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
