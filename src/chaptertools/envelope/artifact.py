"""Persistent per-book analysis artifact."""
from __future__ import annotations

import array
import json
import os
import sys

from chaptertools import __version__, workdir
from chaptertools.util import die

ENVELOPE_HZ = 20          # 50 ms frames
ENV_FLOOR_DB = -96.0
AUDNEX = "https://api.audnex.us"
SCHEMA = 3


def analysis_matches_audio(data: dict, audio: str) -> bool:
    """True when this analysis still belongs to ``audio``.

    A tag rewrite moves mtime without changing the audio. The same size
    keeps the measured stages; a different size does not. An older schema
    does not either.
    """
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return False
    stored = data.get("file")
    try:
        size = os.path.getsize(audio)
    except OSError:
        return False
    return isinstance(stored, dict) and stored.get("size") == size


class Artifact:
    """One JSON document per book. Stages read and write it; each records
    what it depended on so staleness is detectable rather than assumed."""

    def __init__(self, audio: str, work_dir: str | None = None):
        self.audio = os.path.abspath(audio)
        resolved = workdir.resolve_work_dir(self.audio, work_dir)
        if work_dir and resolved != os.path.abspath(work_dir) \
                and "\\" in work_dir:
            print(f"  ! work-dir had shell escapes; using {resolved}",
                  file=sys.stderr)
        self.work_dir = resolved
        try:
            os.makedirs(resolved, exist_ok=True)
        except OSError as e:
            die(f"cannot create work directory {resolved}: {e.strerror}. "
                f"Use --work-dir or set CHAPTERFIT_WORK.")
        notice = workdir.adopt_beside_audio(self.audio, resolved)
        if notice:
            print(f"  note: {notice}", file=sys.stderr)
        self.path = workdir.artifact_path(self.audio, resolved)
        self.env_path = workdir.envelope_path(self.audio, resolved)
        self.data = {}
        self.load()

    def fingerprint(self) -> dict:
        st = os.stat(self.audio)
        return {"size": st.st_size, "mtime": int(st.st_mtime)}

    def load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self.data = json.load(f)
            except (OSError, ValueError):
                self.data = {}
        if not analysis_matches_audio(self.data, self.audio):
            self.data = {}
        self.data.setdefault("schema", SCHEMA)
        self.data.setdefault("audio", self.audio)
        self.data["file"] = self.fingerprint()

    def save(self):
        self.data["written_by"] = f"chaptertools {__version__}"
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                # compact: the gap list can run to tens of thousands of entries
                json.dump(self.data, f, separators=(",", ":"))
            os.replace(tmp, self.path)
        except OSError as e:
            die(f"cannot write {self.path}: {e.strerror}. Use --work-dir.")

    def valid(self, name, sig) -> bool:
        """Is this stage still good? Each stage records the inputs it used,
        so changing one knob recomputes only what actually depends on it."""
        st = self.data.get(name)
        return bool(st) and st.get("_sig") == sig

    def put(self, name, value, sig):
        value = dict(value)
        value["_sig"] = sig
        self.data[name] = value
        return value

    def stage(self, name):
        return self.data.get(name)

    def require(self, name, how: str):
        v = self.data.get(name)
        if not v:
            die(f"no '{name}' stage yet - run:  {how}")
        return v

    # envelope lives beside the JSON as raw int8 dB
    def write_envelope(self, dbs):
        a = array.array("b", [max(-128, min(127, int(round(d)))) for d in dbs])
        with open(self.env_path, "wb") as f:
            a.tofile(f)

    def read_envelope(self):
        if not os.path.exists(self.env_path):
            die("envelope file missing - re-run 'measure'")
        a = array.array("b")
        with open(self.env_path, "rb") as f:
            a.frombytes(f.read())
        return a


# ==========================================================================
# stage 1 - verify the timeline
# ==========================================================================

