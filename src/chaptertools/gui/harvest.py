"""Read everything a book folder can tell us, for GUI prefill."""
from __future__ import annotations

import json
import os
import subprocess
import xml.etree.ElementTree as ET

from chaptertools.output import read_tags


def read_nfo(folder: str) -> dict:
    """album.nfo fields worth prefilling: title/artist/year/track count."""
    path = os.path.join(folder, "album.nfo")
    if not os.path.isfile(path):
        return {}
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {}
    if root.tag != "album":
        return {}
    return {
        "title": root.findtext("title"),
        "artist": root.findtext("albumartist") or root.findtext("artist"),
        "year": root.findtext("year") or root.findtext("releasedate"),
        "tracks": len(root.findall("track")),
    }


def embedded_chapters(path: str) -> list[dict]:
    """Chapters already inside the file: [{start, title}] (Audnexus shape)."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_chapters",
                        "-of", "json", path],
                       capture_output=True, text=True)
    if r.returncode:
        return []
    try:
        raw = json.loads(r.stdout).get("chapters", [])
    except ValueError:
        return []
    return [{"start": float(c.get("start_time", 0) or 0),
             "title": (c.get("tags") or {}).get("title") or "Chapter"}
            for c in raw]


def _first(*vals) -> str:
    return next((str(v).strip() for v in vals if v and str(v).strip()), "")


def harvest_book(audio: str) -> dict:
    """Everything present in the folder, best-effort, for the GUI.

    Returns tags (from the file), nfo (album.nfo fields), sidecar flags,
    embedded chapter list, and `suggested` metadata with the fallback
    chain ID3 tag -> album.nfo -> folder names.
    """
    audio = os.path.abspath(audio)
    folder = os.path.dirname(audio)
    author_folder = os.path.basename(os.path.dirname(folder))
    book_folder = os.path.basename(folder)

    tags = read_tags(audio)
    nfo = read_nfo(folder)
    chapters = embedded_chapters(audio)

    return {
        "tags": tags,
        "nfo": nfo,
        "embedded_chapters": chapters,
        "has_chapters_json": os.path.isfile(os.path.join(folder,
                                                         "chapters.json")),
        "has_chapters_txt": os.path.isfile(os.path.join(folder,
                                                        "chapters.txt")),
        "suggested": {
            "title": _first(tags.get("title"), nfo.get("title"), book_folder),
            "author": _first(tags.get("artist"), nfo.get("artist"),
                             author_folder),
            "album": _first(tags.get("album"), nfo.get("title"), book_folder),
            "year": _first(tags.get("date"), nfo.get("year")),
            "genre": _first(tags.get("genre")),
            "comment": _first(tags.get("comment")),
        },
    }


def found_summary(h: dict) -> str:
    """One human line about what the folder gave us."""
    bits = []
    if h["tags"]:
        bits.append("ID3 tags: " + ", ".join(sorted(h["tags"])))
    n = len(h["embedded_chapters"])
    bits.append(f"{n} embedded chapter{'s' if n != 1 else ''}" if n
                else "no embedded chapters")
    if h["nfo"]:
        nfo = h["nfo"]
        bits.append(f"album.nfo ({nfo.get('title') or '?'}, "
                    f"{nfo.get('tracks', 0)} tracks)")
    else:
        bits.append("no album.nfo")
    if h["has_chapters_json"]:
        bits.append("chapters.json")
    if h["has_chapters_txt"]:
        bits.append("chapters.txt")
    return " · ".join(bits)
