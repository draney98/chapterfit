"""What ffprobe says about a media file, read once per file version.

One probe (chapters + format) serves chapters, tags and duration. Results
are cached by (path, size, mtime), so a rewritten file is probed again and
an unchanged one never is. Safe to call from any thread.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from collections import OrderedDict

_CACHE_MAX = 256
_cache: OrderedDict[tuple, dict | None] = OrderedDict()
_lock = threading.Lock()


def _key(path: str) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (os.path.abspath(path), st.st_size, st.st_mtime_ns)


def probe(path: str) -> dict | None:
    """ffprobe's {"chapters": [...], "format": {...}}, or None when the file
    is missing or unreadable."""
    key = _key(path)
    if key is None:
        return None
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    r = subprocess.run(["ffprobe", "-v", "error", "-show_chapters",
                        "-show_format", "-of", "json", path],
                       capture_output=True, text=True)
    info = None
    if r.returncode == 0:
        try:
            info = json.loads(r.stdout)
        except ValueError:
            info = None
    with _lock:
        _cache[key] = info
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return info


def probe_chapters(path: str) -> list[dict]:
    """Embedded chapters: [{title, start, end}] ([] when none/unreadable)."""
    info = probe(path) or {}
    return [{"title": (c.get("tags") or {}).get("title") or "Chapter",
             "start": float(c.get("start_time", 0) or 0),
             "end": float(c.get("end_time", 0) or 0)}
            for c in info.get("chapters") or []]


def probe_tags(path: str) -> dict:
    """Global tags, keys lower-cased, empty values dropped."""
    tags = ((probe(path) or {}).get("format") or {}).get("tags") or {}
    return {k.strip().lower(): v for k, v in tags.items()
            if isinstance(v, str) and v.strip()}


def probe_duration(path: str) -> float | None:
    """Container duration in seconds, None when unknown."""
    d = ((probe(path) or {}).get("format") or {}).get("duration")
    try:
        return float(d) if d is not None else None
    except ValueError:
        return None


def readable(path: str) -> bool:
    return probe(path) is not None


def clear_cache() -> None:
    with _lock:
        _cache.clear()
