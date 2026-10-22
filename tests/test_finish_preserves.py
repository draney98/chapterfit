"""Finish must keep the original audio, its cover, and its analysis."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAPTERFIT = os.path.join(PROJECT_ROOT, "chapterfit.py")
SECONDS = 4


def _run(args: list[str], env: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, CHAPTERFIT, *args],
        capture_output=True, text=True, env=env)


def _env(tmp_path: Path) -> dict:
    env = os.environ.copy()
    for key in ("ABS_URL", "ABS_TOKEN", "ABS_LIBRARY_ID", "ABS_LIBRARY_NAME"):
        env.pop(key, None)
    env["CHAPTERFIT_WORK"] = str(tmp_path / "workroot")
    return env


def _sine(path: Path, seconds: int = SECONDS) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={seconds}",
         "-t", str(seconds), str(path)],
        check=True, capture_output=True)


def _analysis(work: Path, audio: Path, *, envelope: bool = True) -> None:
    results = [
        {"index": 0, "title": "Part One", "published": 0.0, "time": 0.0,
         "confidence": 1.0, "pinned": False, "spread": None,
         "n_options": 1, "residual": None},
        {"index": 1, "title": "Part Two", "published": 2.0, "time": 2.0,
         "confidence": 0.9, "pinned": False, "spread": None,
         "n_options": 1, "residual": None},
    ]
    payload = {
        "schema": 3,
        "file": {"size": audio.stat().st_size,
                 "mtime": int(audio.stat().st_mtime)},
        "audio": str(audio),
        "alignment": {"rate": 1.0, "min_confidence": 0.5, "results": results},
    }
    if envelope:
        payload["envelope"] = {
            "hz": 20, "frames": SECONDS * 20, "duration": float(SECONDS),
        }
    (work / f"{audio.name}.analysis.json").write_text(json.dumps(payload))


def _book(tmp_path: Path, name: str = "KeepOriginal"):
    library = tmp_path / "Ada" / name
    library.mkdir(parents=True)
    audio = library / f"{name}.mp3"
    _sine(audio)
    work = tmp_path / "workroot" / "Ada" / name
    work.mkdir(parents=True)
    _analysis(work, audio)
    empty = tmp_path / "empty.env"
    empty.write_text("")
    return audio, work, empty


def _finish_args(audio: Path, work: Path, empty: Path, *extra: str) -> list[str]:
    command, *rest = extra
    args = ["--work-dir", str(work), command]
    if command == "finalize":
        args += ["--env", str(empty)]
    return [*args, *rest, str(audio)]


def _attached_pictures(path: Path) -> list[dict]:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        check=True, capture_output=True, text=True)
    streams = json.loads(proc.stdout).get("streams") or []
    return [stream for stream in streams
            if stream.get("codec_type") == "video"
            and (stream.get("disposition") or {}).get("attached_pic") == 1]


@pytest.mark.integration
def test_finalize_twice_keeps_the_first_original_byte_identical(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "workroot"))
    audio, work, empty = _book(tmp_path)
    first = audio.read_bytes()
    env = _env(tmp_path)
    original = work / "originals" / audio.name

    emitted = _run(_finish_args(audio, work, empty, "emit", "--embed"), env)
    assert emitted.returncode == 0, emitted.stderr
    applied = _run(_finish_args(
        audio, work, empty, "finalize", "--apply", "-y", "--force-nfo"), env)
    assert applied.returncode == 0, applied.stderr
    assert original.is_file()
    assert original.read_bytes() == first
    assert audio.read_bytes() != first

    again_emit = _run(_finish_args(audio, work, empty, "emit", "--embed"), env)
    assert again_emit.returncode == 0, again_emit.stderr
    again = _run(_finish_args(
        audio, work, empty, "finalize", "--apply", "-y", "--force-nfo"), env)
    assert again.returncode == 0, again.stderr
    assert original.read_bytes() == first
    staged = list((work / "originals").iterdir())
    assert len(staged) >= 2


@pytest.mark.integration
def test_finalize_keeps_the_analysis(tmp_path, monkeypatch):
    from chaptertools.envelope.artifact import Artifact
    from chaptertools.gui.library import book_marks

    monkeypatch.setenv("CHAPTERFIT_WORK", str(tmp_path / "workroot"))
    audio, work, empty = _book(tmp_path, "KeepAnalysis")
    first = audio.read_bytes()
    want = hashlib.sha256(first).hexdigest()
    env = _env(tmp_path)

    emitted = _run(_finish_args(audio, work, empty, "emit", "--embed"), env)
    assert emitted.returncode == 0, emitted.stderr
    applied = _run(_finish_args(
        audio, work, empty, "finalize", "--apply", "-y", "--force-nfo"), env)
    assert applied.returncode == 0, applied.stderr

    art = Artifact(str(audio), str(work))
    assert art.data["alignment"]["results"][0]["title"] == "Part One"
    assert art.data["envelope"]["duration"] == float(SECONDS)
    derived = art.data["derived_from"]
    assert derived["sha256"] == want
    assert derived["size"] == len(first)
    assert Path(derived["path"]).read_bytes() == first
    assert art.data["file"]["size"] == audio.stat().st_size
    assert book_marks(str(audio)) == "✓"


@pytest.mark.integration
def test_finalize_without_an_envelope_names_the_missing_stage(tmp_path):
    audio, work, empty = _book(tmp_path, "NoEnvelope")
    _analysis(work, audio, envelope=False)
    proc = _run(_finish_args(audio, work, empty, "finalize"), _env(tmp_path))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "envelope" in proc.stderr


@pytest.mark.integration
def test_emit_without_an_envelope_names_the_missing_stage(tmp_path):
    audio, work, empty = _book(tmp_path, "EmitNoEnvelope")
    _analysis(work, audio, envelope=False)
    proc = _run(_finish_args(audio, work, empty, "emit", "--embed"),
                _env(tmp_path))
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "envelope" in proc.stderr


@pytest.mark.integration
def test_emit_embed_keeps_the_cover(tmp_path):
    library = tmp_path / "Ada" / "Covered"
    library.mkdir(parents=True)
    cover = library / "cover.png"
    bare = library / "bare.mp3"
    audio = library / "Covered.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "color=c=red:s=16x16", "-frames:v", "1", str(cover)],
        check=True, capture_output=True)
    _sine(bare)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(bare), "-i", str(cover),
         "-map", "0:a", "-map", "1:v", "-c", "copy",
         "-disposition:v:0", "attached_pic", "-id3v2_version", "3",
         str(audio)],
        check=True, capture_output=True)
    assert _attached_pictures(audio)
    work = tmp_path / "work"
    work.mkdir()
    _analysis(work, audio)
    proc = _run(["--work-dir", str(work), "emit", "--embed", str(audio)],
                _env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    chaptered = library / "Covered.chapters.mp3"
    assert _attached_pictures(chaptered)


@pytest.mark.integration
def test_emit_embed_keeps_the_m4a_covr_atom(tmp_path):
    library = tmp_path / "Ada" / "Covr"
    library.mkdir(parents=True)
    audio = library / "Covr.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
         "-f", "lavfi", "-i", "color=c=blue:s=16x16",
         "-t", "4", "-map", "0:a", "-map", "1:v",
         "-c:a", "aac", "-c:v", "png",
         "-disposition:v:0", "attached_pic", str(audio)],
        check=True, capture_output=True)
    assert b"covr" in audio.read_bytes()
    work = tmp_path / "work"
    work.mkdir()
    _analysis(work, audio)
    proc = _run(["--work-dir", str(work), "emit", "--embed", str(audio)],
                _env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    chaptered = library / "Covr.chapters.m4a"
    assert b"covr" in chaptered.read_bytes()
    assert _attached_pictures(chaptered)
