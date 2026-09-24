"""Shared CLI / time helpers for the chapter toolkit."""

from __future__ import annotations

import math
import os
import shutil
import sys

__all__ = [
    "clock",
    "hms",
    "parse_time",
    "die",
    "need",
    "q",
    "normalize_work_dir",
    "logsumexp",
    "module_argv",
    "package_env",
]


def module_argv(module: str, python: str | None = None,
                unbuffered: bool = False) -> list[str]:
    """argv that runs a chaptertools module as a program; pair it with
    package_env() so any interpreter (a whisper venv, say) can import it."""
    return [python or sys.executable, *(["-u"] if unbuffered else []),
            "-m", module]


def _package_parent() -> str:
    """A directory whose only job on PYTHONPATH is to expose chaptertools.
    The source tree's src/ as is; for an installed copy a private link, so a
    foreign interpreter never sees the rest of this site-packages."""
    import chaptertools
    pkg = os.path.dirname(os.path.abspath(chaptertools.__file__))
    parent = os.path.dirname(pkg)
    if os.path.basename(parent) not in ("site-packages", "dist-packages"):
        return parent
    import tempfile
    shim = os.path.join(tempfile.gettempdir(), f"chaptertools-path-{os.getuid()}")
    link = os.path.join(shim, "chaptertools")
    try:
        os.makedirs(shim, exist_ok=True)
        if os.path.realpath(link) != pkg:
            if os.path.lexists(link):
                os.unlink(link)
            os.symlink(pkg, link)
    except OSError:
        return parent
    return shim


def package_env(base: dict | None = None) -> dict:
    """Environment whose PYTHONPATH puts chaptertools first."""
    env = dict(os.environ if base is None else base)
    parts = [_package_parent()] + [p for p in env.get("PYTHONPATH", "")
                                   .split(os.pathsep) if p]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(parts))
    return env


def clock(t) -> str:
    """H:MM:SS for mpv's --start, which wants whole seconds."""
    t = max(0.0, t)
    h, rem = divmod(int(t), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}"


def hms(t) -> str:
    """Seconds to H:MM:SS.mmm. None -> '-' (chapterfit) or caller-specific."""
    if t is None:
        return "-"
    neg, t = t < 0, abs(t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return ("-" if neg else "") + f"{int(h)}:{int(m):02d}:{s:06.3f}"


def parse_time(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    v = str(v).strip()
    if ":" in v:
        total = 0.0
        for part in v.split(":"):
            total = total * 60 + float(part or 0)
        return total
    return float(v)


def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def need(binary: str, hint: str | None = None):
    if shutil.which(binary) is None:
        extra = hint or f"Install {binary}."
        if binary.startswith("ff"):
            extra = "Install ffmpeg."
        die(f"{binary} not found on PATH. {extra}" if hint is None
            else f"{binary} not found on PATH. {hint}")


def q(s: str) -> str:
    return f'"{s}"' if (" " in str(s) or "'" in str(s)) else str(s)


def normalize_work_dir(path: str) -> str:
    """Undo shell-escaped paths that were pasted as literal filesystem names."""
    if not path:
        return path
    fixed = str(path).replace("\\ ", " ")
    out = []
    i = 0
    while i < len(fixed):
        ch = fixed[i]
        if ch == "\\" and i + 1 < len(fixed):
            nxt = fixed[i + 1]
            if nxt in ("\\", "/") or (i == 0 and nxt.isalpha()):
                out.append(ch)
                i += 1
                continue
            out.append(nxt)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def logsumexp(vals):
    vals = [v for v in vals if v > -1e300]
    if not vals:
        return -math.inf
    m = max(vals)
    return m + math.log(sum(math.exp(v - m) for v in vals))
