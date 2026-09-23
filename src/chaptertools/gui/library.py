"""Media-library scanning and artifact path conventions."""
from __future__ import annotations

import json
import os

AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".wav"}
SKIP_DIRS = {"__pycache__", "src", "tests", ".pytest_cache"}


def library_root(explicit: str | None = None) -> str:
    """Explicit argument, then CHAPTERFIT_LIBRARY, then the current directory."""
    return os.path.abspath(
        explicit or os.environ.get("CHAPTERFIT_LIBRARY") or os.getcwd())


def work_root() -> str:
    return os.environ.get("CHAPTERFIT_WORK") or os.path.join(
        os.path.expanduser("~"), ".local", "share", "chapterfit")


def work_dir_for(audio: str) -> str:
    """Mirror chapterbook.sh: WORK/<author>/<book-folder>."""
    folder = os.path.dirname(os.path.abspath(audio))
    author = os.path.basename(os.path.dirname(folder)) or "unknown"
    book = os.path.basename(folder) or os.path.splitext(os.path.basename(audio))[0]
    return os.path.join(work_root(), author, book)


def analysis_path(audio: str, work_dir: str | None = None) -> str:
    base = os.path.basename(audio) + ".analysis.json"
    if work_dir:
        return os.path.join(work_dir, base)
    return audio + ".analysis.json"


def load_analysis(audio: str, work_dir: str | None = None) -> dict:
    try:
        with open(analysis_path(audio, work_dir)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def is_audio(name: str) -> bool:
    base = os.path.basename(name)
    if ".chapters." in base:
        return False  # emit's intermediate; finalize consumes it
    return os.path.splitext(base)[1].lower() in AUDIO_EXTS


def scan_library(root: str) -> dict[str, list[str]]:
    """Audio files grouped by top-level author directory: {author: [paths]}."""
    out: dict[str, list[str]] = {}
    try:
        entries = list(os.scandir(root))
    except OSError:
        return out
    for ent in entries:
        if not ent.is_dir() or ent.name.startswith((".", "_")) \
                or ent.name in SKIP_DIRS:
            continue
        found = []
        for dirpath, dirnames, filenames in os.walk(ent.path):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith((".", "_")) and d not in SKIP_DIRS]
            for fn in filenames:
                if is_audio(fn):
                    found.append(os.path.join(dirpath, fn))
        if found:
            out[ent.name] = sorted(found)
    return out
