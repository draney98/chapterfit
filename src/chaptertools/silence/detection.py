"""Silence detection and sidecar cache."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

from chaptertools.util import die

__all__ = [
    "Silence",
    "SIL_START",
    "SIL_END",
    "SIDECAR",
    "DEFAULT_CACHE",
    "FALLBACK_CACHE",
    "probe_duration",
    "sidecar_path",
    "central_path",
    "silence_cache_path",
    "detect_silences",
]

def probe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0 or not out.stdout.strip():
        die(f"ffprobe could not read {path}: {out.stderr.strip()[:300]}")
    return float(out.stdout.strip())


@dataclass
class Silence:
    start: float
    end: float

    @property
    def mid(self) -> float:
        return (self.start + self.end) / 2.0

    @property
    def dur(self) -> float:
        return self.end - self.start


SIL_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
SIL_END = re.compile(r"silence_end:\s*(-?[\d.]+)")

SIDECAR = "sidecar"
# Default: write the cache next to the audio file as <name>.silences.json.
DEFAULT_CACHE = SIDECAR
# Used only when the audio's own directory is not writable.
FALLBACK_CACHE = os.path.expanduser("~/.cache/chapterize")


def _fingerprint(path: str) -> tuple[int, int]:
    st = os.stat(path)
    return st.st_size, int(st.st_mtime)


def sidecar_path(path: str) -> str:
    return os.path.abspath(path) + ".silences.json"


def central_path(path: str, cache_dir: str) -> str:
    ap = os.path.abspath(path)
    h = hashlib.sha1(ap.encode("utf-8")).hexdigest()[:12]
    base = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(ap))[:60]
    return os.path.join(cache_dir, "silences", f"{base}.{h}.json")


def silence_cache_path(path: str, cache_dir: str | None) -> str:
    """Where the cache for this file lives. SIDECAR means beside the audio."""
    if cache_dir in (None, SIDECAR, ""):
        return sidecar_path(path)
    return central_path(path, cache_dir)


def _candidate_paths(path: str, cache_dir: str | None) -> list[str]:
    """Read order: the configured location first, then the fallback."""
    primary = silence_cache_path(path, cache_dir)
    out = [primary]
    if cache_dir in (None, SIDECAR, ""):
        alt = central_path(path, FALLBACK_CACHE)
        if alt != primary:
            out.append(alt)
    return out


def _read_blob(cp: str, path: str):
    if not os.path.exists(cp):
        return None
    try:
        with open(cp) as f:
            blob = json.load(f)
    except (OSError, ValueError):
        return None
    size, mtime = _fingerprint(path)
    if blob.get("size") != size or blob.get("mtime") != mtime:
        return None  # file changed underneath us
    return blob


def _cache_load(path: str, cache_dir: str | None, key: str):
    for cp in _candidate_paths(path, cache_dir):
        blob = _read_blob(cp, path)
        if blob and key in blob.get("runs", {}):
            return [Silence(a, b) for a, b in blob["runs"][key]]
    return None


def _write_blob(cp: str, path: str, key: str, silences: list["Silence"],
                duration: float | None):
    parent = os.path.dirname(cp)
    if parent:
        os.makedirs(parent, exist_ok=True)
    size, mtime = _fingerprint(path)
    blob = _read_blob(cp, path) or {
        "file": os.path.abspath(path), "size": size, "mtime": mtime, "runs": {}}
    blob.setdefault("runs", {})
    if duration is not None:
        blob["duration"] = round(duration, 3)
    blob["runs"][key] = [[round(s.start, 4), round(s.end, 4)] for s in silences]
    tmp = cp + ".tmp"
    with open(tmp, "w") as f:
        json.dump(blob, f, indent=1)
    os.replace(tmp, cp)


def _cache_store(path: str, cache_dir: str | None, key: str,
                 silences: list["Silence"], duration: float | None = None,
                 verbose: bool = False):
    primary = silence_cache_path(path, cache_dir)
    try:
        _write_blob(primary, path, key, silences, duration)
        return primary
    except OSError as e:
        if cache_dir not in (None, SIDECAR, ""):
            raise
        # library directories are often read-only or owned by the service user
        alt = central_path(path, FALLBACK_CACHE)
        if verbose:
            print(f"  (cannot write {primary}: {e.strerror}; "
                  f"using {alt})", file=sys.stderr)
        _write_blob(alt, path, key, silences, duration)
        return alt


def detect_silences(path: str, noise_db: float, min_dur: float,
                    verbose: bool, cache_dir: str | None = DEFAULT_CACHE
                    ) -> list[Silence]:
    key = f"noise={noise_db}:d={min_dur}"

    if cache_dir is not False:
        hit = _cache_load(path, cache_dir, key)
        if hit is not None:
            if verbose:
                print(f"  silence cache hit ({len(hit)} runs) "
                      f"{silence_cache_path(path, cache_dir)}", file=sys.stderr)
            return hit

    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
           "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}",
           "-f", "null", "-"]
    if verbose:
        print(f"  $ {' '.join(cmd)}", file=sys.stderr)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    # silencedetect writes to stderr; a non-zero exit here is a real failure
    if proc.returncode != 0:
        die(f"ffmpeg silencedetect failed:\n{proc.stderr.strip()[-800:]}")

    silences: list[Silence] = []
    pending: float | None = None
    for line in proc.stderr.splitlines():
        m = SIL_START.search(line)
        if m:
            pending = float(m.group(1))
            continue
        m = SIL_END.search(line)
        if m and pending is not None:
            silences.append(Silence(pending, float(m.group(1))))
            pending = None

    if cache_dir is not False:
        try:
            _cache_store(path, cache_dir, key, silences, verbose=verbose)
        except OSError as e:
            if verbose:
                print(f"  (could not write silence cache: {e})", file=sys.stderr)

    return silences


