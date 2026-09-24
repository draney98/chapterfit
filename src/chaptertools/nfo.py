"""Kodi-style album.nfo writing: one <track> per embedded chapter."""
from __future__ import annotations

import os
import shutil
import xml.etree.ElementTree as ET

from chaptertools.media import probe_chapters

INDENT = "  "


def chapters_from_file(path: str) -> list[dict]:
    """Embedded chapters of a media file: [{title, start, end}]."""
    return probe_chapters(path)


def fmt_duration(seconds: float) -> str:
    """Track duration in the NFO style of the library: mm:ss (or h:mm:ss)."""
    total = max(0, int(round(seconds)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _track(title: str, duration: str) -> ET.Element:
    t = ET.Element("track")
    ET.SubElement(t, "title").text = title
    ET.SubElement(t, "duration").text = duration
    return t


def render_nfo(existing: str | None, chapters: list[dict],
               title: str, artist: str) -> str:
    """Album NFO text with one track per chapter.

    When `existing` parses, all of its metadata is kept and only the track
    list is replaced. Otherwise a minimal fresh album is built from the
    folder names.
    """
    root = None
    if existing:
        try:
            root = ET.parse(existing).getroot()
        except (OSError, ET.ParseError):
            root = None
    if root is None or root.tag != "album":
        root = ET.Element("album")
        ET.SubElement(root, "title").text = title
        ET.SubElement(root, "artist").text = artist
        ET.SubElement(root, "albumartist").text = artist

    for tr in root.findall("track"):
        root.remove(tr)
    for c in chapters:
        root.append(_track(c["title"], fmt_duration(c["end"] - c["start"])))

    total = chapters[-1]["end"] if chapters else 0.0
    runtime = root.find("runtime")
    if runtime is not None:
        runtime.text = str(int(round(total / 60)))

    ET.indent(root, space=INDENT)
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def write_album_nfo(folder: str, audio: str, apply: bool = False,
                    title: str | None = None,
                    artist: str | None = None) -> int:
    """Write folder/album.nfo from the embedded chapters of `audio`.

    Dry runs only report. Real writes back up a pre-existing album.nfo once.
    `title`/`artist` (e.g. the tags emitted with the file) are used when
    creating a fresh NFO; without them the folder names are used.
    Returns the number of chapter tracks involved (0 when skipped).
    """
    chapters = chapters_from_file(audio)
    if len(chapters) < 2:
        print("  album.nfo       : skipped (fewer than 2 embedded chapters)")
        return 0

    nfo = os.path.join(folder, "album.nfo")
    title = (title or "").strip() or \
        os.path.basename(folder.rstrip(os.sep)) or "Album"
    artist = (artist or "").strip() or \
        os.path.basename(os.path.dirname(folder.rstrip(os.sep))) \
        or "Unknown Artist"
    text = render_nfo(nfo if os.path.isfile(nfo) else None,
                      chapters, title, artist)
    total = chapters[-1]["end"]

    if not apply:
        print(f"  album.nfo       : would write {len(chapters)} tracks "
          f"({fmt_duration(total)} total)"
          + (" - album.nfo.bak would hold the previous version"
             if os.path.isfile(nfo) else ""))
        return len(chapters)

    if os.path.isfile(nfo) and not os.path.isfile(nfo + ".bak"):
        shutil.copy2(nfo, nfo + ".bak")
        print("  album.nfo.bak   : original kept")
    with open(nfo, "w", encoding="utf-8-sig") as f:
        f.write('<?xml version="1.0" encoding="utf-8" '
                'standalone="yes"?>\n')
        f.write(text)
        f.write("\n")
    print(f"  album.nfo       : wrote {len(chapters)} tracks "
          f"({fmt_duration(total)} total)"
          " - Kodi picks it up on its next library refresh")
    return len(chapters)
