#!/usr/bin/env python3
"""
chapterize.py - align Audnexus (Audible) chapter data to a local audiobook file
using ffmpeg silence detection.

Audnexus timestamps come from Audible's own edition of the book. A local rip is
almost never a byte-for-byte match: it may be missing the "This is Audible"
brand intro, may have a different opening-credits track, and may drift slightly
if it was re-encoded. This tool corrects for that by:

  1. Fetching chapter offsets from https://api.audnex.us for an ASIN.
  2. Detecting silences in the local file with ffmpeg's silencedetect filter.
  3. Solving for the offset (and optionally a small time scale) that best lines
     the two up, by voting over every (silence, boundary) pairing.
  4. Snapping each chapter boundary onto the nearest real silence.

Only the standard library plus ffmpeg/ffprobe are required.

Implementation lives in chaptertools/; this module remains the public import
surface for sibling scripts (abchapters, chapterdebug, consensus, ...).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Editable / src layout support when not installed.
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from chaptertools.audnex import (  # noqa: E402
    AUDNEX_BASE,
    USER_AGENT,
    AudnexBook,
    Chapter,
    fetch_audnex,
    http_json,
    parse_audnex,
)
from chaptertools.output import (  # noqa: E402
    Placed,
    abs_json,
    embed,
    read_tags,
    safe_name,
    split,
)
from chaptertools.output import ffmetadata as _ffmetadata_core
from chaptertools.silence import (  # noqa: E402
    DEFAULT_CACHE,
    FALLBACK_CACHE,
    SIDECAR,
    Alignment,
    Silence,
    detect_silences,
    filter_by_duration,
    match_stats,
    place_chapters,
    probe_duration,
    silence_cache_path,
    solve_alignment,
)
from chaptertools.silence.alignment import _linfit, _score  # noqa: E402
from chaptertools.util import die, need  # noqa: E402


def hms(seconds: float) -> str:
    """3661.5 -> '1:01:01.500' (chapterize historically used '?' for None)."""
    if seconds is None:
        return "?"
    neg = seconds < 0
    seconds = abs(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    out = f"{int(h)}:{int(m):02d}:{s:06.3f}"
    return ("-" + out) if neg else out

# Re-export private helpers used by tests / wrappers.
__all__ = [
    "AUDNEX_BASE", "USER_AGENT", "Chapter", "AudnexBook", "Silence",
    "Alignment", "Placed", "parse_audnex", "http_json", "fetch_audnex",
    "detect_silences", "filter_by_duration", "match_stats", "solve_alignment",
    "place_chapters", "ffmetadata", "abs_json", "embed", "split", "safe_name",
    "probe_duration", "hms", "die", "need", "_score", "_linfit",
    "DEFAULT_CACHE", "FALLBACK_CACHE", "SIDECAR", "silence_cache_path",
]


def ffmetadata(placed, local_dur, book, audio=None) -> str:
    """Compatibility wrapper: chapterize historically passed AudnexBook."""
    tags = read_tags(audio) if audio else {}
    if getattr(book, "title", None):
        tags["title"] = book.title
    if getattr(book, "author", None):
        tags["artist"] = book.author
    return _ffmetadata_core(placed, local_dur, tags=tags)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Align Audnexus chapter data to a local audiobook using "
                    "ffmpeg silence detection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("audio", help="the audiobook file (one long mp3/m4a/m4b)")

    src = ap.add_argument_group("chapter source")
    src.add_argument("--asin", help="Audible ASIN, e.g. B002V1OF70")
    src.add_argument("--region", default="us",
                     help="Audible region: us, uk, ca, au, de, fr, jp... (default us)")
    src.add_argument("--chapters-json", metavar="FILE",
                     help="read chapter JSON from a file instead of the network")
    src.add_argument("--cache-dir", default=os.path.expanduser("~/.cache/chapterize"),
                     help="where to cache Audnexus responses (--cache-dir '' to disable)")

    det = ap.add_argument_group("silence detection")
    det.add_argument("--noise", type=float, default=-40.0,
                     help="silence threshold in dB, lower is stricter (default -40)")
    det.add_argument("--min-silence", type=float, default=1.5,
                     help="minimum silence length in seconds (default 1.5)")
    det.add_argument("--silence-cache", default=DEFAULT_CACHE,
                     help="where to cache silence scans: a directory path, or "
                          "'sidecar' to write <file>.silences.json next to the "
                          "audio (default: sidecar)")
    det.add_argument("--no-silence-cache", action="store_true",
                     help="always rescan, ignore and do not write the cache")
    det.add_argument("--break-min", type=float, metavar="SECONDS",
                     help="only match against silences at least this long. "
                          "Detect permissively, then match selectively - "
                          "this filters the same scan, no rescan.")
    det.add_argument("--break-max", type=float, metavar="SECONDS",
                     help="only match against silences no longer than this")

    aln = ap.add_argument_group("alignment")
    aln.add_argument("--offset", type=float, metavar="SECONDS",
                     help="shift the published timeline by this many "
                          "seconds and do not search. Positive means the "
                          "book starts later in your file, e.g. 30 when "
                          "your rip has 30s of extra intro.")
    aln.add_argument("--offset-hint", type=float, metavar="SECONDS",
                     help="roughly where the offset is; the search is "
                          "confined to a window around it. Use this when "
                          "the file has so many silences that a blind "
                          "search finds coincidences.")
    aln.add_argument("--offset-window", type=float, default=15.0,
                     help="how far either side of --offset-hint to search "
                          "(default 15)")
    aln.add_argument("--offset-mode", choices=["auto", "intro", "none"], default="auto",
                     help="auto = solve for the offset; intro = just subtract "
                          "brandIntroDurationMs; none = trust Audnexus (default auto)")
    aln.add_argument("--max-offset", type=float, default=120.0,
                     help="largest offset auto mode will consider, seconds (default 120)")
    aln.add_argument("--tolerance", type=float, default=4.0,
                     help="a boundary counts as matched within this many seconds "
                          "(default 4)")
    aln.add_argument("--snap-window", type=float, default=15.0,
                     help="snap a boundary to a silence within this window (default 15)")
    aln.add_argument("--no-snap", action="store_true",
                     help="do not snap; use the shifted Audnexus times as-is")
    aln.add_argument("--no-scale", action="store_true",
                     help="do not fit a time scale, offset only")
    aln.add_argument("--min-chapter", type=float, default=2.0,
                     help="drop chapters shorter than this many seconds (default 2)")

    out = ap.add_argument_group("output")
    out.add_argument("--ffmetadata", metavar="FILE",
                     help="write an ffmetadata chapter file")
    out.add_argument("--abs-json", metavar="FILE",
                     help="write Audiobookshelf-shaped chapter JSON")
    out.add_argument("--embed", nargs="?", const="", metavar="DEST",
                     help="write a copy with chapters embedded (no re-encode). "
                          "Defaults to <name>.chapters<ext>")
    out.add_argument("--split", metavar="DIR",
                     help="cut the file into one file per chapter")
    out.add_argument("--report", metavar="FILE",
                     help="write a machine-readable JSON result (for wrappers)")
    out.add_argument("-v", "--verbose", action="store_true")

    args = ap.parse_args()

    if not args.asin and not args.chapters_json:
        ap.error("one of --asin or --chapters-json is required")
    if not os.path.isfile(args.audio):
        die(f"no such file: {args.audio}")

    need("ffmpeg")
    need("ffprobe")

    # 1. chapter data
    if args.chapters_json:
        with open(args.chapters_json) as f:
            book = parse_audnex(json.load(f), args.asin or "local")
        print(f"chapters : {len(book.chapters)} from {args.chapters_json}")
    else:
        book = fetch_audnex(args.asin, args.region, args.cache_dir or None)
        label = book.title or args.asin
        print(f"book     : {label}" + (f" - {book.author}" if book.author else ""))
        print(f"chapters : {len(book.chapters)} from Audnexus "
              f"({args.asin}, {args.region})")
    if book.is_accurate is False:
        print("  ! Audnexus flags this chapter data as not accurate")

    # 2. local file
    local_dur = probe_duration(args.audio)
    print(f"file     : {os.path.basename(args.audio)}  ({hms(local_dur)})")
    if book.runtime:
        print(f"audible  : {hms(book.runtime)}  "
              f"(diff {hms(local_dur - book.runtime)})")

    # 3. silences
    print(f"scanning for silences (noise {args.noise}dB, min {args.min_silence}s)...")
    sil_cache = False if args.no_silence_cache else args.silence_cache
    silences = detect_silences(args.audio, args.noise, args.min_silence,
                               args.verbose, sil_cache)
    all_silences = silences
    if args.break_min is not None or args.break_max is not None:
        silences = filter_by_duration(silences, args.break_min, args.break_max)
        print(f"silences : {len(all_silences)} found, "
              f"{len(silences)} in the {args.break_min or 0}-"
              f"{args.break_max if args.break_max else 'inf'}s band")
    else:
        print(f"silences : {len(silences)} found")
    if len(silences) < len(book.chapters) - 1:
        print("  ! fewer silences than chapter gaps - consider --noise -35 "
              "or --min-silence 1.0")

    # 4. align
    mode = "manual" if args.offset is not None else args.offset_mode
    al = solve_alignment(book, silences, local_dur, mode,
                         args.tolerance, args.max_offset,
                         not args.no_scale, args.verbose,
                         hint=args.offset_hint,
                         hint_window=args.offset_window,
                         manual_offset=args.offset)
    print(f"align    : {al.method}")
    print(f"           offset {al.offset:+.3f}s"
          + (f", scale {al.scale:.5f}" if al.scale != 1.0 else ""))
    print(f"           {al.matched}/{al.total} boundaries near a silence"
          + (f", mean error {al.mean_err:.2f}s" if al.matched else ""))
    if al.total and silences:
        st = match_stats(al.matched, al.total, len(silences),
                         local_dur, args.tolerance)
        print(f"           chance would give {st['expected']:.1f}; "
              f"this is {st['enrichment']:.1f}x that (z={st['z']:.1f})")
        if st["enrichment"] < 1.5 and al.matched:
            print("  ! barely above chance - this match count means little")
    for n in al.notes:
        print(f"  ! {n}")
    if al.total and al.matched / al.total < 0.5:
        print("  ! weak alignment. The rip may be a different edition, or the "
              "narration may run through chapter breaks without a pause.")

    # 5. place
    placed = place_chapters(book, silences, al, local_dur,
                            not args.no_snap, args.snap_window, args.min_chapter)
    dropped = len(book.chapters) - len(placed)
    snapped = sum(1 for p in placed if p.snapped)
    print(f"placed   : {len(placed)} chapters, {snapped} snapped to silence"
          + (f", {dropped} dropped as too short" if dropped else ""))

    print()
    print(f"  {'#':>3}  {'start':>12}  {'moved':>8}  title")
    for i, p in enumerate(placed):
        mark = f"{p.delta:+.2f}s" if p.snapped else "-"
        print(f"  {i+1:>3}  {hms(p.start):>12}  {mark:>8}  {p.title[:52]}")
    print()

    # 6. outputs
    wrote_any = False

    meta_text = ffmetadata(placed, local_dur, book, audio=args.audio)

    if args.ffmetadata:
        with open(args.ffmetadata, "w") as f:
            f.write(meta_text)
        print(f"wrote {args.ffmetadata}")
        wrote_any = True

    if args.abs_json:
        with open(args.abs_json, "w") as f:
            f.write(abs_json(placed, local_dur))
        print(f"wrote {args.abs_json}")
        wrote_any = True

    if args.embed is not None:
        dest = args.embed
        if not dest:
            root, ext = os.path.splitext(args.audio)
            dest = f"{root}.chapters{ext}"
        if os.path.abspath(dest) == os.path.abspath(args.audio):
            die("--embed destination must differ from the source file")
        tmp = dest + ".ffmeta.tmp"
        with open(tmp, "w") as f:
            f.write(meta_text)
        try:
            embed(args.audio, tmp, dest, args.verbose)
        finally:
            os.unlink(tmp)
        print(f"wrote {dest}")
        wrote_any = True

    if args.split:
        print(f"splitting into {args.split}/ ...")
        split(args.audio, placed, local_dur, args.split, args.verbose)
        wrote_any = True

    if args.report:
        with open(args.report, "w") as f:
            json.dump({
                "source": "audible",
                "audio": os.path.abspath(args.audio),
                "duration_sec": round(local_dur, 3),
                "asin": args.asin,
                "region": args.region,
                "title": book.title,
                "author": book.author,
                "audible_runtime_sec": round(book.runtime, 3) if book.runtime else None,
                "silences_found": len(silences),
                "align": {
                    "method": al.method,
                    "offset_sec": round(al.offset, 3),
                    "scale": round(al.scale, 6),
                    "matched": al.matched,
                    "total": al.total,
                    "match_ratio": round(al.matched / al.total, 4) if al.total else 0.0,
                    "mean_error_sec": round(al.mean_err, 3) if al.matched else None,
                    "notes": al.notes,
                },
                "chapters": [
                    {"index": i, "title": p.title, "start_sec": round(p.start, 3),
                     "snapped": p.snapped, "moved_sec": round(p.delta, 3)}
                    for i, p in enumerate(placed)
                ],
            }, f, indent=2)
        print(f"wrote {args.report}")

    if not wrote_any:
        print("(preview only - pass --embed, --split, --ffmetadata or --abs-json "
              "to write something)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
