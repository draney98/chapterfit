"""Media-library scanning and artifact path conventions."""
from __future__ import annotations

import json
import os

from chaptertools import workdir
from chaptertools.media import probe_tags

AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".wav"}
SKIP_DIRS = {"__pycache__", "src", "tests", ".pytest_cache"}


def library_root(explicit: str | None = None) -> str:
    """Explicit argument, then CHAPTERFIT_LIBRARY, then the current directory."""
    return os.path.abspath(
        explicit or os.environ.get("CHAPTERFIT_LIBRARY") or os.getcwd())


def work_dir_for(audio: str) -> str:
    """The shared rule (chaptertools.workdir): WORK/<author>/<book-folder>."""
    return workdir.default_work_dir(audio)


def asin_result_path(audio: str, work_dir: str) -> str:
    """find-asin-library.sh writes <audio>.asin.json beside the analysis."""
    return os.path.join(work_dir, os.path.basename(audio) + ".asin.json")


def _load_asin(audio: str, work_dir: str | None) -> dict:
    if not audio or not work_dir:
        return {}
    try:
        with open(asin_result_path(audio, work_dir)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def asin_query(audio: str, work_dir: str | None, *,
               prefer_tags: bool = True) -> dict[str, str]:
    """Title and author for Search.

    The file's title and artist tags win when present. A missing tag
    falls back to the last saved guess, then the book folder and the
    author folder above it. ``prefer_tags`` is off after a search so the
    boxes keep showing the query that was sent.
    """
    data = _load_asin(audio, work_dir)
    title = str(data.get("guessed_title") or "").strip()
    author = str(data.get("guessed_author") or "").strip()
    if audio:
        folder = os.path.dirname(os.path.abspath(audio))
        if not title:
            title = os.path.basename(folder)
        if not author:
            author = os.path.basename(os.path.dirname(folder))
        if prefer_tags:
            tags = probe_tags(audio)
            tagged_title = str(tags.get("title") or "").strip()
            tagged_author = str(tags.get("artist") or "").strip()
            if tagged_title:
                title = tagged_title
            if tagged_author:
                author = tagged_author
    return {"title": title, "author": author}


def asin_candidates(audio: str, work_dir: str | None) -> list[dict]:
    """Rows from a saved find_asin result.

    Each row has asin, title, author, narrator, runtime, score, and source.
    Missing, unreadable, or empty files return [].
    """
    data = _load_asin(audio, work_dir)
    if not data:
        return []
    rows = []
    for cand in data.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        asin = str(cand.get("asin") or "").strip()
        if not asin:
            continue
        authors = cand.get("authors") or []
        author = authors if isinstance(authors, str) else ", ".join(
            str(a) for a in authors if a)
        narrators = cand.get("narrators") or []
        narrator = narrators if isinstance(narrators, str) else ", ".join(
            str(n) for n in narrators if n)
        runtime = cand.get("runtime_min")
        score = cand.get("score")
        rows.append({
            "asin": asin,
            "title": str(cand.get("title") or ""),
            "author": author,
            "narrator": narrator,
            "runtime": "" if runtime is None else f"{runtime} min",
            "score": "" if score is None else f"{float(score):.2f}",
            "source": str(cand.get("source") or "find-asin"),
        })
    return rows


def asin_emit_defaults(audio: str, work_dir: str | None,
                       label: str) -> dict[str, str]:
    """Title, author, album, and year for the ASIN Identify used.

    The label is ``Audnexus <asin>``. Fields come from that candidate in
    the saved search. Genre and comment are not in the result. An unknown
    ASIN, or no file, returns {}.
    """
    asin = label.split(" ", 1)[1].strip() if label.startswith("Audnexus ") else ""
    if not asin:
        return {}
    for cand in _load_asin(audio, work_dir).get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        if str(cand.get("asin") or "").strip() != asin:
            continue
        title = str(cand.get("title") or "").strip()
        authors = cand.get("authors") or []
        author = authors if isinstance(authors, str) else ", ".join(
            str(a) for a in authors if a)
        release = str(cand.get("release") or "").strip()
        year = release[:4] if len(release) >= 4 and release[:4].isdigit() else ""
        out: dict[str, str] = {}
        if title:
            out["title"] = title
            out["album"] = title
        if author.strip():
            out["author"] = author.strip()
        if year:
            out["year"] = year
        return out
    return {}


def top_asin(audio: str, work_dir: str | None) -> str | None:
    """Best ASIN from a saved find_asin result, or None."""
    rows = asin_candidates(audio, work_dir)
    return rows[0]["asin"] if rows else None


def analysis_path(audio: str, work_dir: str | None = None) -> str:
    return workdir.artifact_path(audio, workdir.resolve_work_dir(audio, work_dir))


def load_analysis(audio: str, work_dir: str | None = None) -> dict:
    """The book's artifact; a legacy beside-audio one is shown until the
    first run adopts it."""
    for path in (analysis_path(audio, work_dir),
                 os.path.abspath(audio) + workdir.ARTIFACT_SUFFIX):
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        return data if isinstance(data, dict) else {}
    return {}


def book_marks(audio: str) -> str:
    """Status glyphs from files already on disk. No decode and no probe.

    ✓ chapters have been written, ● a scan or alignment is in progress,
    ♪ cleaned audio exists.
    """
    work = work_dir_for(audio)
    data: dict = {}
    path = analysis_path(audio, work)
    if os.path.isfile(path):
        try:
            with open(path) as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    marks = ""
    if data.get("emitted"):
        marks += "✓"
    elif data:
        marks += "●"
    stem, ext = os.path.splitext(os.path.basename(audio))
    cleaned = os.path.join(work, stem + ".quality.mp3")
    original = os.path.join(work, f"{stem}.original{ext}")
    if os.path.isfile(cleaned) or os.path.isfile(original):
        marks += "♪"
    return marks


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
