"""Argv builders for chapterfit jobs. No Tk, no subprocess."""
from __future__ import annotations

import os

from chaptertools.util import clock, module_argv, parse_time

CHAPTERFIT_MODULE = "chaptertools.cli.chapterfit"
META_FLAGS = {
    "--title": "title", "--author": "artist", "--album": "album",
    "--year": "date", "--genre": "genre", "--comment": "comment",
}


def chapterfit_prefix(python: str | None = None) -> list[str]:
    """Unbuffered `python -m chaptertools.cli.chapterfit`."""
    return module_argv(CHAPTERFIT_MODULE, python=python, unbuffered=True)


def job_argv(prefix: list[str], work_dir: str, *extra: str, audio: str) -> list[str]:
    return [*prefix, "--work-dir", work_dir, *extra, audio]


def near_spec(raw: str, analysis: dict) -> str | None:
    """Normalize CH=TIME, or map a bare TIME onto the nearest chapter."""
    raw = raw.strip()
    if not raw:
        return None
    if "=" in raw:
        ch, _, t = raw.partition("=")
        try:
            n = int(ch)
            seconds = parse_time(t)
        except ValueError:
            raise ValueError(
                f"hint wants CH=TIME (e.g. 3=43:47) or just a time, "
                f"got {raw!r}") from None
        return f"{n}={clock(seconds)}"
    seconds = parse_time(raw)
    rows = list((analysis.get("alignment") or {}).get("results") or [])
    by_placed = [r for r in rows if r.get("time") is not None]
    if by_placed:
        best = min(by_placed, key=lambda r: abs(r["time"] - seconds))
        return f"{best['index'] + 1}={clock(seconds)}"
    ident = (analysis.get("identification") or {}).get("chapters") or []
    if not ident:
        raise ValueError("run Identify first - no chapter list to map "
                         "the time to")
    best = min(ident, key=lambda c: abs(c["start"] - seconds))
    return f"{ident.index(best) + 1}={clock(seconds)}"


def identify_argv(prefix: list[str], work_dir: str, audio: str,
                  mode: str, value: str) -> list[str]:
    extra = ["identify"]
    if mode == "asin":
        extra += ["--asin", value]
    else:
        extra += ["--chapters-json", value]
    return job_argv(prefix, work_dir, *extra, audio=audio)


def align_argv(prefix: list[str], work_dir: str, audio: str,
               min_conf: float, near: str | None = None) -> list[str]:
    extra = ["align", "--min-confidence", str(min_conf)]
    if near:
        extra += ["--near", near]
    return job_argv(prefix, work_dir, *extra, audio=audio)


def emit_argv(prefix: list[str], work_dir: str, audio: str,
              min_conf: float, embed: bool, overrides: dict[str, str]) -> list[str]:
    extra = ["emit", "--min-confidence", str(min_conf),
             "--chapters-txt", os.path.join(work_dir, "chapters.txt"),
             "--abs-json", os.path.join(work_dir, "chapters.json")]
    for flag, value in overrides.items():
        if value:
            extra += [flag, value]
    if embed:
        extra.append("--embed")
    return job_argv(prefix, work_dir, *extra, audio=audio)


def whisper_argv(prefix: list[str], work_dir: str, audio: str, cmd: str,
                 model: str = "base", start=None, end=None) -> list[str]:
    extra = [cmd, "--model", model, "--pin", "-y"]
    if start is not None:
        extra += ["--start", clock(start)]
    if end is not None:
        extra += ["--end", clock(end)]
    return job_argv(prefix, work_dir, *extra, audio=audio)


def same_source(mode: str, value: str, label: str) -> bool:
    if mode == "asin":
        return label == f"Audnexus {value}"
    if mode == "json":
        return bool(value) and label == value
    return os.path.basename(label) == "chapters.embedded.json"
