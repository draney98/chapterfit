"""One work-directory rule: --work-dir, else CHAPTERFIT_WORK/<author>/<book>."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from chaptertools import workdir
from chaptertools.envelope.artifact import SCHEMA, Artifact
from chaptertools.gui import library

ROOT = Path(__file__).resolve().parents[1]


def _book(tmp_path) -> Path:
    audio = tmp_path / "lib" / "Ann Author" / "Some Book" / "book.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"\0" * 64)
    return audio


def test_explicit_beats_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "root"))
    audio = _book(tmp_path)
    assert workdir.resolve_work_dir(str(audio), str(tmp_path / "x")) == \
        str(tmp_path / "x")
    assert workdir.resolve_work_dir(str(audio)) == \
        str(tmp_path / "root" / "Ann Author" / "Some Book")
    assert workdir.resolve_work_dir(str(audio), r"a\ b") == \
        os.path.abspath("a b")


def test_artifact_default_is_work_root_not_media_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "root"))
    audio = _book(tmp_path)
    art = Artifact(str(audio))
    art.save()
    assert art.path == library.analysis_path(str(audio))
    assert Path(art.path).is_file()
    assert sorted(p.name for p in audio.parent.iterdir()) == ["book.mp3"]


def test_beside_audio_artifact_is_adopted(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "root"))
    audio = _book(tmp_path)
    st = audio.stat()
    legacy = {"schema": SCHEMA, "audio": str(audio),
              "file": {"size": st.st_size, "mtime": int(st.st_mtime)},
              "envelope": {"duration": 12.0}}
    Path(str(audio) + ".analysis.json").write_text(json.dumps(legacy))
    # the library shows it before any run adopts it
    assert library.load_analysis(str(audio))["envelope"]["duration"] == 12.0
    art = Artifact(str(audio))
    assert "adopted book.mp3.analysis.json" in capsys.readouterr().err
    assert art.data["envelope"]["duration"] == 12.0
    assert Path(art.path).is_file()
    # once adopted, never again (and the original is left alone)
    Artifact(str(audio))
    assert "adopted" not in capsys.readouterr().err
    assert Path(str(audio) + ".analysis.json").is_file()


def test_chapterbook_uses_the_same_rule():
    script = (ROOT / "chapterbook.sh").read_text()
    assert 'WORK_ROOT="${CHAPTERFIT_WORK:-$HOME/.local/share/chapterfit}"' \
        in script
    assert '"$WORK_ROOT/$(basename "$(dirname "$FOLDER")")/$(basename ' \
        '"$FOLDER")"' in script


def test_cli_help_states_the_rule():
    out = subprocess.run(
        ["python3", str(ROOT / "chapterfit.py"), "--help"],
        capture_output=True, text=True, check=True).stdout
    assert "CHAPTERFIT_WORK" in out and "beside the audio" not in out
