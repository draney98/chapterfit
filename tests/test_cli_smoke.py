"""Every CLI parses its arguments from the root shim and as a module, and the
root shims are the packaged modules (patches and globals are shared)."""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from chaptertools.util import package_env

ROOT = Path(__file__).resolve().parents[1]

CLIS = {
    "chapterfit": "chaptertools.cli.chapterfit",
    "chapterize": "chaptertools.silence.chapterize",
    "chapterdebug": "chaptertools.silence.chapterdebug",
    "abchapters": "chaptertools.silence.abchapters",
    "consensus_chapters": "chaptertools.consensus.consensus_chapters",
    "librivox_chapters": "chaptertools.sources.librivox_chapters",
    "find_asin": "chaptertools.sources.find_asin",
}


def _run(argv, cwd):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          env=package_env({"PATH": "/usr/bin:/bin"}),
                          timeout=60)


@pytest.mark.parametrize("name", sorted(CLIS))
def test_shim_is_the_package_module(name):
    assert importlib.import_module(name) is importlib.import_module(CLIS[name])


@pytest.mark.parametrize("name", sorted(CLIS))
def test_help_from_shim_and_module(name, tmp_path):
    shim = _run([sys.executable, str(ROOT / f"{name}.py"), "--help"], tmp_path)
    mod = _run([sys.executable, "-m", CLIS[name], "--help"], tmp_path)
    assert shim.returncode == 0, shim.stderr
    assert mod.returncode == 0, mod.stderr
    assert "usage:" in shim.stdout and "usage:" in mod.stdout


@pytest.mark.parametrize("argv, expect", [
    (["chapterdebug", "inspect", "--help"], "--asin"),
    (["chapterdebug", "profile", "--help"], "audio"),
    (["find_asin", "--help"], "--verify"),
    (["abchapters", "--help"], "--source"),
])
def test_subcommand_help(argv, expect, tmp_path):
    out = _run([sys.executable, "-m", CLIS[argv[0]], *argv[1:]], tmp_path)
    assert out.returncode == 0, out.stderr
    assert expect in out.stdout


@pytest.mark.parametrize("argv", [
    ["chapterdebug"],                      # a subcommand is required
    ["chapterdebug", "inspect"],           # audio is required
    ["find_asin"],                         # audio is required
    ["abchapters", "x.mp3", "--source", "nowhere"],
])
def test_bad_arguments_are_usage_errors(argv, tmp_path):
    out = _run([sys.executable, "-m", CLIS[argv[0]], *argv[1:]], tmp_path)
    assert out.returncode == 2
    assert "usage:" in out.stderr


def test_abchapters_children_run_as_modules():
    ab = importlib.import_module(CLIS["abchapters"])
    for argv, mod in ((ab.CHAPTERIZE, CLIS["chapterize"]),
                      (ab.FIND_ASIN, CLIS["find_asin"]),
                      (ab.LIBRIVOX, CLIS["librivox_chapters"]),
                      (ab.CONSENSUS, CLIS["consensus_chapters"])):
        assert argv[-2:] == ["-m", mod]
