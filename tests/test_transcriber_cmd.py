"""Regression tests for Transcriber command parsing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import chapterfit as cf  # noqa: E402


def test_transcriber_cmd_respects_quotes(tmp_path, monkeypatch):
    script = tmp_path / "fake_asr.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json,sys\n"
        "print(json.dumps({'start':0.0,'end':1.0,'text':'Chapter One','words':[]}))\n"
    )
    script.chmod(0o755)
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"RIFF")

    tr = cf.Transcriber(cmd=f'{sys.executable} "{script}"')
    segs = tr.segments(str(wav))
    assert segs and segs[0]["t"] == "Chapter One"


def test_transcriber_cmd_nonzero_exits(tmp_path):
    script = tmp_path / "fail.py"
    script.write_text("#!/usr/bin/env python3\nimport sys\nsys.exit(3)\n")
    script.chmod(0o755)
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"RIFF")
    tr = cf.Transcriber(cmd=f"{sys.executable} {script}")
    with pytest.raises(SystemExit):
        tr.segments(str(wav))
