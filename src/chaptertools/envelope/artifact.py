"""Persistent per-book analysis artifact."""
from __future__ import annotations

import array
import json
import os
import sys

from chaptertools.util import die, normalize_work_dir

ENVELOPE_HZ = 20          # 50 ms frames
ENV_FLOOR_DB = -96.0
AUDNEX = "https://api.audnex.us"
__version__ = "1.13.0"
SCHEMA = 3

class Artifact:
    """One JSON document per book. Stages read and write it; each records
    what it depended on so staleness is detectable rather than assumed."""

    def __init__(self, audio: str, work_dir: str | None = None):
        self.audio = os.path.abspath(audio)
        base = os.path.basename(self.audio)
        if work_dir:
            raw = work_dir
            work_dir = os.path.abspath(normalize_work_dir(work_dir))
            if work_dir != os.path.abspath(raw) and "\\" in raw:
                print(f"  ! work-dir had shell escapes; using {work_dir}",
                      file=sys.stderr)
            os.makedirs(work_dir, exist_ok=True)
            self.path = os.path.join(work_dir, base + ".analysis.json")
            self.env_path = os.path.join(work_dir, base + ".envelope.i8")
        else:
            self.path = self.audio + ".analysis.json"
            self.env_path = self.audio + ".envelope.i8"
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
        if self.data.get("schema") != SCHEMA:
            self.data = {}
        if self.data.get("file") != self.fingerprint():
            # the audio changed underneath us; everything derived is void
            self.data = {}
        self.data.setdefault("schema", SCHEMA)
        self.data.setdefault("audio", self.audio)
        self.data["file"] = self.fingerprint()

    def save(self):
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

