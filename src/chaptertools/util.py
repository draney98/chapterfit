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
]


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
