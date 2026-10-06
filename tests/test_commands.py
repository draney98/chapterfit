"""Argv builders for chapterfit jobs (no Tk)."""
from chaptertools.gui.commands import (
    CHAPTERFIT_MODULE,
    align_argv,
    chapterfit_prefix,
    emit_argv,
    identify_argv,
    job_argv,
    near_spec,
    same_source,
    whisper_argv,
)


def test_chapterfit_prefix_is_unbuffered_module():
    assert chapterfit_prefix("/venv/bin/python") == [
        "/venv/bin/python", "-u", "-m", CHAPTERFIT_MODULE]


def test_job_argv_puts_work_dir_before_the_subcommand():
    prefix = chapterfit_prefix("python3")
    assert job_argv(prefix, "/work", "measure", audio="/book/a.mp3") == [
        "python3", "-u", "-m", CHAPTERFIT_MODULE,
        "--work-dir", "/work", "measure", "/book/a.mp3"]


def test_near_spec_maps_bare_time_to_nearest_placed_chapter():
    analysis = {"alignment": {"results": [
        {"index": 0, "time": 0.0},
        {"index": 1, "time": 95.0},
        {"index": 2, "time": 813.0},
    ]}}
    assert near_spec("2:40", analysis) == "2=0:02:40"
    assert near_spec("3=43:47", analysis) == "3=0:43:47"


def test_near_spec_uses_published_starts_before_align():
    analysis = {"identification": {"chapters": [
        {"title": "a", "start": 0.0}, {"title": "b", "start": 682.0}]}}
    assert near_spec("11:30", analysis) == "2=0:11:30"


def test_identify_and_align_and_emit_argv():
    prefix = ["python3", "-u", "-m", CHAPTERFIT_MODULE]
    assert identify_argv(prefix, "/w", "/a.mp3", "asin", "B00X")[-3:] == [
        "--asin", "B00X", "/a.mp3"]
    assert "--chapters-json" in identify_argv(
        prefix, "/w", "/a.mp3", "json", "/c.json")
    al = align_argv(prefix, "/w", "/a.mp3", 0.55, "2=0:02:40")
    assert "--min-confidence" in al and "0.55" in al and "--near" in al
    em = emit_argv(prefix, "/w", "/a.mp3", 0.5, True,
                   {"--title": "Eon", "--author": ""})
    assert "--embed" in em and "--title" in em and "Eon" in em
    assert "--author" not in em
    assert "--source" not in em
    cleaned = emit_argv(prefix, "/w", "/w/a.quality.mp3", 0.5, True, {},
                        source="/library/a.mp3")
    assert cleaned[cleaned.index("--source") + 1] == "/library/a.mp3"
    assert cleaned[-1] == "/w/a.quality.mp3"


def test_whisper_argv_and_same_source():
    w = whisper_argv(chapterfit_prefix("/venv/bin/python"), "/w", "/a.mp3",
                     "listen", model="tiny")
    assert w[:4] == ["/venv/bin/python", "-u", "-m", CHAPTERFIT_MODULE]
    assert "listen" in w and "--model" in w and "tiny" in w
    assert same_source("asin", "B00X", "Audnexus B00X")
    assert not same_source("asin", "B00Y", "Audnexus B00X")
    assert same_source("emb", "", "/tmp/chapters.embedded.json")
