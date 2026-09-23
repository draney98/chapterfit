"""Silence-snap pipeline internals."""

from chaptertools.silence.alignment import (
    Alignment,
    filter_by_duration,
    match_stats,
    place_chapters,
    solve_alignment,
)
from chaptertools.silence.detection import (
    DEFAULT_CACHE,
    FALLBACK_CACHE,
    SIDECAR,
    Silence,
    detect_silences,
    probe_duration,
    silence_cache_path,
)

__all__ = [
    "Alignment",
    "DEFAULT_CACHE",
    "FALLBACK_CACHE",
    "SIDECAR",
    "Silence",
    "detect_silences",
    "filter_by_duration",
    "match_stats",
    "place_chapters",
    "probe_duration",
    "silence_cache_path",
    "solve_alignment",
]
