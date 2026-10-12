"""LibriVox section folders as one book timeline (no glue file).

Pure helpers: concat list, cumulative starts, book↔file time, pin specs.
The GUI and measure CLI call these; Tk never lives here.
"""
from __future__ import annotations

import os

from chaptertools.util import clock


def write_ffconcat(files: list[str], dest: str) -> str:
    """Write an ffmpeg concat demuxer list. Paths are absolute and quoted."""
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    lines = []
    for path in files:
        abs_path = os.path.abspath(path).replace("'", r"'\''")
        lines.append(f"file '{abs_path}'")
    with open(dest, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    return dest


def cumulative_starts(durations: list[float]) -> list[float]:
    starts: list[float] = []
    t = 0.0
    for dur in durations:
        starts.append(t)
        t += float(dur)
    return starts


def pin_specs(starts: list[float]) -> list[str]:
    """chapterfit pin --at values: 1-based chapter = book time."""
    return [f"{i + 1}={clock(start)}" for i, start in enumerate(starts)]


def chapters_from_durations(titles: list[str],
                            durations: list[float]) -> list[dict]:
    """Published-style chapter list from section titles and file lengths."""
    starts = cumulative_starts(durations)
    out = []
    for i, title in enumerate(titles):
        start = starts[i] if i < len(starts) else 0.0
        dur = float(durations[i]) if i < len(durations) else 0.0
        out.append({
            "title": title,
            "start": start,
            "end": start + dur,
        })
    return out


def book_to_file(book_t: float, starts: list[float],
                 durations: list[float]) -> tuple[int, float]:
    """Map book time → (file index, offset in that file)."""
    if not starts:
        return 0, max(0.0, book_t)
    for i, start in enumerate(starts):
        dur = float(durations[i]) if i < len(durations) else 0.0
        end = start + dur
        if book_t < end or i == len(starts) - 1:
            return i, max(0.0, book_t - start)
    return len(starts) - 1, max(0.0, book_t - starts[-1])


def file_to_book(index: int, offset: float, starts: list[float]) -> float:
    if not starts or index < 0:
        return max(0.0, offset)
    i = min(index, len(starts) - 1)
    return starts[i] + max(0.0, offset)


def playlist_sig(files: list[str]) -> tuple:
    """Cache key: path + size + mtime for every section."""
    out = []
    for path in files:
        try:
            st = os.stat(path)
            out.append((os.path.abspath(path), st.st_size, st.st_mtime_ns))
        except OSError:
            out.append((os.path.abspath(path), 0, 0))
    return tuple(out)
