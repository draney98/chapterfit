"""Chapter output writers: ffmetadata, ABS JSON, embed, split."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass

from chaptertools.util import die

__all__ = [
    "Placed",
    "ffmetadata",
    "abs_json",
    "embed",
    "read_tags",
    "safe_name",
    "split",
]

TAG_KEYS = ("title", "artist", "album", "date", "genre", "comment")


@dataclass
class Placed:
    title: str
    start: float
    snapped: bool
    delta: float


def read_tags(path: str) -> dict:
    """Global tags of a media file via ffprobe: {title, artist, album, ...}."""
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format_tags", "-of", "json", path],
                       capture_output=True, text=True)
    if r.returncode:
        return {}
    try:
        tags = json.loads(r.stdout).get("format", {}).get("tags", {})
    except ValueError:
        return {}
    return {k.strip().lower(): v for k, v in tags.items() if v.strip()}


def merge_tags(original: dict, overrides: dict) -> dict:
    """Field-wise: override wins, else the original tag, else absent."""
    out = {}
    for k in TAG_KEYS:
        v = (overrides.get(k) or "").strip() or original.get(k)
        if v and str(v).strip():
            out[k] = str(v).strip()
    return out


def ffmetadata(placed: list[Placed], local_dur: float,
               tags: dict | None = None) -> str:
    lines = [";FFMETADATA1"]
    for k in TAG_KEYS:
        v = (tags or {}).get(k)
        if v:
            lines.append(f"{k}={v}")
    lines.append("")
    for i, p in enumerate(placed):
        end = placed[i + 1].start if i + 1 < len(placed) else local_dur
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={int(round(p.start * 1000))}",
            f"END={int(round(end * 1000))}",
            f"title={p.title}",
            "",
        ]
    return "\n".join(lines)


def abs_json(placed: list[Placed], local_dur: float) -> str:
    out = []
    for i, p in enumerate(placed):
        end = placed[i + 1].start if i + 1 < len(placed) else local_dur
        out.append({
            "id": i,
            "start": round(p.start, 3),
            "end": round(end, 3),
            "title": p.title,
        })
    return json.dumps(out, indent=2)


def embed(src: str, meta_path: str, dest: str, verbose: bool = False):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", src, "-i", meta_path,
           "-map", "0:a", "-map_metadata", "1", "-map_chapters", "1",
           "-c", "copy", dest]
    if verbose:
        print(f"  $ {' '.join(cmd)}", file=__import__("sys").stderr)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"ffmpeg embed failed:\n{r.stderr.strip()[-800:]}")


def safe_name(s: str) -> str:
    s = re.sub(r"[/\\:*?\"<>|]", "-", s).strip()
    return re.sub(r"\s+", " ", s)[:120] or "Chapter"


def split(src: str, placed: list[Placed], local_dur: float, outdir: str,
          verbose: bool = False):
    os.makedirs(outdir, exist_ok=True)
    ext = os.path.splitext(src)[1] or ".mp3"
    width = max(2, len(str(len(placed))))
    for i, p in enumerate(placed):
        end = placed[i + 1].start if i + 1 < len(placed) else local_dur
        name = f"{str(i + 1).zfill(width)} - {safe_name(p.title)}{ext}"
        dest = os.path.join(outdir, name)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{p.start:.3f}", "-to", f"{end:.3f}",
               "-i", src, "-c", "copy",
               "-metadata", f"title={p.title}",
               "-metadata", f"track={i + 1}/{len(placed)}",
               dest]
        if verbose:
            print(f"  $ {' '.join(cmd)}", file=__import__("sys").stderr)
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            die(f"ffmpeg split failed on chapter {i + 1}:\n"
                f"{r.stderr.strip()[-500:]}")
        print(f"    wrote {name}")
