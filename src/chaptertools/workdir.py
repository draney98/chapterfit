"""Where a book's analysis artifact lives - the one rule every tool uses.

1. An explicit ``--work-dir`` (shell escapes in pasted paths undone).
2. Otherwise ``$CHAPTERFIT_WORK/<author folder>/<book folder>``, with
   ``CHAPTERFIT_WORK`` defaulting to ``~/.local/share/chapterfit``.

The media tree stays data-only: nothing is written beside the audio. An
artifact an older version left there (``<audio>.analysis.json``) is copied
into the work directory the first time the book is opened, with a notice.
"""
from __future__ import annotations

import os
import shutil

from chaptertools.util import normalize_work_dir

ARTIFACT_SUFFIX = ".analysis.json"
ENVELOPE_SUFFIX = ".envelope.i8"


def work_root() -> str:
    return os.environ.get("CHAPTERFIT_WORK") or os.path.join(
        os.path.expanduser("~"), ".local", "share", "chapterfit")


def default_work_dir(audio: str) -> str:
    """WORK/<author>/<book-folder> (chapterbook.sh computes the same)."""
    folder = os.path.dirname(os.path.abspath(audio))
    author = os.path.basename(os.path.dirname(folder)) or "unknown"
    book = os.path.basename(folder) \
        or os.path.splitext(os.path.basename(audio))[0]
    return os.path.join(work_root(), author, book)


def resolve_work_dir(audio: str, explicit: str | None = None) -> str:
    if explicit:
        return os.path.abspath(normalize_work_dir(explicit))
    return default_work_dir(audio)


def artifact_path(audio: str, work_dir: str) -> str:
    return os.path.join(work_dir, os.path.basename(audio) + ARTIFACT_SUFFIX)


def envelope_path(audio: str, work_dir: str) -> str:
    return os.path.join(work_dir, os.path.basename(audio) + ENVELOPE_SUFFIX)


def adopt_beside_audio(audio: str, work_dir: str) -> str | None:
    """Copy a legacy beside-audio artifact (and envelope) into work_dir when
    work_dir has none. Returns a one-line notice, or None if nothing moved.
    The originals are left in place."""
    audio = os.path.abspath(audio)
    old = audio + ARTIFACT_SUFFIX
    new = artifact_path(audio, work_dir)
    if not os.path.isfile(old) or os.path.exists(new):
        return None
    try:
        os.makedirs(work_dir, exist_ok=True)
        shutil.copy2(old, new)
        old_env = audio + ENVELOPE_SUFFIX
        new_env = envelope_path(audio, work_dir)
        if os.path.isfile(old_env) and not os.path.exists(new_env):
            shutil.copy2(old_env, new_env)
    except OSError as e:
        return f"could not adopt {old}: {e.strerror}"
    return (f"adopted {os.path.basename(old)} from beside the audio into "
            f"{work_dir} (the original can be deleted)")
