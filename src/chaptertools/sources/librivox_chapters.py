#!/usr/bin/env python3
"""
librivox_chapters.py - build chapter marks for a concatenated LibriVox recording.

LibriVox never publishes a single long file: every project ships as one mp3 per
section. So if you have a LibriVox book as one big mp3, someone glued the
sections together. That is good news, because it means the chapter boundaries
are not something to *detect* - they are the cumulative sum of the published
section runtimes, which LibriVox gives you exactly.

The only wrinkle is re-encoding drift. If whoever concatenated the files
transcoded them, each section can come out a few milliseconds off, and the
error accumulates over 20 hours. This corrects for that by scaling the
cumulative timeline so the last section lands on the true end of your file,
then optionally nudging each boundary onto the nearest real silence.

Reuses chapterize.py (must sit in the same directory) for silence detection
and for writing chapters out.

Examples
--------
  librivox_chapters.py book.mp3                       # search by tags/path
  librivox_chapters.py book.mp3 --title "The Time Machine"
  librivox_chapters.py book.mp3 --lv-id 52            # exact project id
  librivox_chapters.py book.mp3 --lv-id 52 --embed
  librivox_chapters.py book.mp3 --lv-id 52 --split parts/
  librivox_chapters.py book.mp3 --sections-json cached.json --abs-json ch.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from chaptertools.silence import chapterize
from chaptertools.sources import find_asin

LV_API = "https://librivox.org/api/feed/audiobooks"
USER_AGENT = "librivox_chapters.py/1.0"


# --------------------------------------------------------------------------

def die(msg: str):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def hms(sec: float) -> str:
    h, rem = divmod(abs(sec), 3600)
    m, s = divmod(rem, 60)
    out = f"{int(h)}:{int(m):02d}:{s:06.3f}"
    return ("-" + out) if sec < 0 else out


def parse_time(v) -> float | None:
    """LibriVox gives playtime as raw seconds or as HH:MM:SS."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    v = str(v).strip()
    if not v:
        return None
    if ":" in v:
        parts = [float(p) for p in v.split(":")]
        total = 0.0
        for p in parts:
            total = total * 60 + p
        return total
    try:
        return float(v)
    except ValueError:
        return None


def http_json(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# --------------------------------------------------------------------------

@dataclass
class Section:
    number: int
    title: str
    duration: float
    reader: str | None = None


@dataclass
class Project:
    lv_id: str
    title: str
    authors: list[str]
    total: float | None
    sections: list[Section]
    url: str | None = None
    archive: str | None = None

    @property
    def sum_sections(self) -> float:
        return sum(s.duration for s in self.sections)


def parse_project(b: dict) -> Project:
    authors = []
    for a in (b.get("authors") or []):
        name = " ".join(x for x in [a.get("first_name"), a.get("last_name")] if x)
        if name.strip():
            authors.append(name.strip())

    sections = []
    for s in (b.get("sections") or []):
        d = parse_time(s.get("playtime"))
        if d is None or d <= 0:
            continue
        try:
            num = int(s.get("section_number") or len(sections) + 1)
        except (TypeError, ValueError):
            num = len(sections) + 1
        readers = s.get("readers") or []
        reader = None
        if readers and isinstance(readers[0], dict):
            reader = readers[0].get("display_name")
        sections.append(Section(
            number=num,
            title=(s.get("title") or f"Section {num}").strip(),
            duration=d,
            reader=reader,
        ))
    sections.sort(key=lambda s: s.number)

    return Project(
        lv_id=str(b.get("id", "")),
        title=(b.get("title") or "").strip(),
        authors=authors,
        total=parse_time(b.get("totaltimesecs") or b.get("totaltime")),
        sections=sections,
        url=b.get("url_librivox"),
        archive=b.get("url_iarchive"),
    )


def lv_query(params: dict, verbose: bool) -> list[Project]:
    params = dict(params)
    params.update({"format": "json", "extended": "1"})
    url = f"{LV_API}/?" + urllib.parse.urlencode(params)
    if verbose:
        print(f"  GET {url}", file=sys.stderr)
    try:
        payload = http_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        die(f"LibriVox HTTP {e.code}")
    except urllib.error.URLError as e:
        die(f"could not reach LibriVox: {e.reason}")

    books = payload.get("books")
    if not books:
        return []
    if isinstance(books, dict):        # single result comes back unwrapped
        books = [books]
    return [parse_project(b) for b in books]


def search(title: str | None, author: str | None, lv_id: str | None,
           verbose: bool) -> list[Project]:
    if lv_id:
        return lv_query({"id": lv_id}, verbose)
    out = []
    if title:
        # ^ anchors the match at the start of the title in LibriVox's API
        out = lv_query({"title": f"^{title}"}, verbose)
        if not out:
            out = lv_query({"title": title}, verbose)
    if not out and author:
        out = lv_query({"author": author}, verbose)
    return out


# --------------------------------------------------------------------------

def rank(projects: list[Project], local_dur: float, title: str | None):
    def key(p: Project):
        total = p.total or p.sum_sections
        rt = abs(total - local_dur) / max(local_dur, 1.0) if total else 1.0
        tsim = 0.0
        if title and find_asin:
            tsim = find_asin.sim(p.title, title)
        return (rt - 0.25 * tsim)
    projects.sort(key=key)


def build_chapters(p: Project, local_dur: float, scale_fit: bool):
    """Cumulative section runtimes -> chapter start times, drift-corrected."""
    starts, t = [], 0.0
    for s in p.sections:
        starts.append(t)
        t += s.duration
    published_total = t

    scale = 1.0
    if scale_fit and published_total > 0:
        scale = local_dur / published_total

    return [st * scale for st in starts], published_total, scale


def project_book(p: Project):
    """Represent LibriVox sections in chapterize's shared timeline model."""
    chapters, t = [], 0.0
    for section in p.sections:
        chapters.append(chapterize.Chapter(
            section.title, t, section.duration))
        t += section.duration
    return chapterize.AudnexBook(
        asin="", chapters=chapters, runtime=t, title=p.title,
        author=", ".join(p.authors) if p.authors else None)


def alignment_stats(al, n_silences: int, duration: float, tolerance: float):
    stats = chapterize.match_stats(
        al.matched, al.total, n_silences, duration, tolerance)
    ratio = al.matched / al.total if al.total else 0.0
    confident = (
        al.matched >= 2
        and ratio >= 0.60
        and stats["enrichment"] >= 2.0
        and (stats["z"] >= 3.0 or (al.total <= 3 and ratio == 1.0))
    )
    return stats, ratio, confident


def align_project(p: Project, silences, local_dur: float, args):
    book = project_book(p)
    mode = "manual" if args.offset is not None else "auto"
    al = chapterize.solve_alignment(
        book, silences, local_dur, mode,
        args.tolerance, args.max_offset,
        allow_scale=not args.no_scale, verbose=args.verbose,
        hint=args.offset_hint, hint_window=args.offset_window,
        manual_offset=args.offset)
    stats, ratio, confident = alignment_stats(
        al, len(silences), local_dur, args.tolerance)
    return book, al, stats, ratio, confident


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Derive chapter marks for a concatenated LibriVox recording.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("--lv-id", help="LibriVox project id (skips searching)")
    ap.add_argument("--title", help="override the guessed title")
    ap.add_argument("--author", help="override the guessed author")
    ap.add_argument("--sections-json", metavar="FILE",
                    help="use a saved LibriVox API response instead of the network")
    ap.add_argument("--pick", type=int, default=1, metavar="N",
                    help="use the Nth ranked project (default 1)")
    ap.add_argument("--list", action="store_true",
                    help="just list matching projects and stop")

    ap.add_argument("--no-align", action="store_true",
                    help="skip silence alignment and use whole-runtime scaling")
    ap.add_argument("--no-scale", action="store_true",
                    help="do not fit timing drift")
    ap.add_argument("--offset", type=float, metavar="SECONDS",
                    help="use this fixed timeline offset instead of searching")
    ap.add_argument("--offset-hint", type=float, metavar="SECONDS",
                    help="roughly where the timeline offset should be")
    ap.add_argument("--offset-window", type=float, default=15.0,
                    help="search distance around --offset-hint (default 15)")
    ap.add_argument("--max-offset", type=float, default=120.0,
                    help="largest automatic offset to consider (default 120)")
    ap.add_argument("--tolerance", type=float, default=4.0,
                    help="distance from a silence that counts as a match (default 4)")
    ap.add_argument("--verify-top", type=int, default=5,
                    help="candidate projects to verify against silences (default 5)")
    ap.add_argument("--snap", action="store_true",
                    help="nudge each boundary onto the nearest silence")
    ap.add_argument("--snap-window", type=float, default=8.0)
    ap.add_argument("--noise", type=float, default=-40.0)
    ap.add_argument("--min-silence", type=float, default=1.0)
    ap.add_argument("--break-min", type=float, metavar="SECONDS",
                    help="only align against silences at least this long")
    ap.add_argument("--break-max", type=float, metavar="SECONDS",
                    help="only align against silences no longer than this")
    ap.add_argument("--silence-cache", default=None,
                    help="directory path, or 'sidecar' to write "
                         "<file>.silences.json next to the audio")
    ap.add_argument("--no-silence-cache", action="store_true")

    ap.add_argument("--ffmetadata", metavar="FILE")
    ap.add_argument("--abs-json", metavar="FILE")
    ap.add_argument("--embed", nargs="?", const="", metavar="DEST")
    ap.add_argument("--split", metavar="DIR")
    ap.add_argument("--report", metavar="FILE",
                    help="write a machine-readable JSON result")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.audio):
        die(f"no such file: {args.audio}")

    local_dur = chapterize.probe_duration(args.audio)

    # --- guess title/author ------------------------------------------------
    title, author = args.title, args.author
    if not title and find_asin:
        lf = find_asin.probe(args.audio)
        title, author = title or lf.title, author or lf.author
    if not title:
        title = os.path.splitext(os.path.basename(args.audio))[0]

    print(f"file     : {os.path.basename(args.audio)}  ({hms(local_dur)})")
    print(f"looking  : {title!r}" + (f" by {author!r}" if author else ""))

    # --- find the project --------------------------------------------------
    if args.sections_json:
        with open(args.sections_json) as f:
            payload = json.load(f)
        books = payload.get("books", payload)
        if isinstance(books, dict):
            books = [books]
        projects = [parse_project(b) for b in books]
    else:
        projects = search(title, author, args.lv_id, args.verbose)

    projects = [p for p in projects if p.sections]
    if not projects:
        die("no LibriVox project found with section data. "
            "Try --title, or --lv-id from the librivox.org URL.")

    rank(projects, local_dur, title)

    if args.list:
        print(f"\n{len(projects)} candidate project(s):")
        for i, p in enumerate(projects[:10], 1):
            total = p.total or p.sum_sections
            print(f"  {i:>2}. id={p.lv_id:<6} {hms(total):>12} "
                  f"(Δ {(total-local_dur)/60:+6.1f}m)  "
                  f"{len(p.sections):>3} sections  {p.title[:44]}")
            if p.authors:
                print(f"      {'':<6} {'':>12} {'':>9}  by {', '.join(p.authors)[:52]}")
        return

    # Alignment and snapping share one cached silence scan.  Alignment uses
    # an optional duration band; snapping still sees every detected silence.
    silences = []
    match_silences = []
    if not args.no_align or args.snap:
        print(f"detecting silences (noise {args.noise}dB, "
              f"min {args.min_silence}s)...")
        sil_cache = False if args.no_silence_cache else (
            args.silence_cache or chapterize.DEFAULT_CACHE)
        silences = chapterize.detect_silences(
            args.audio, args.noise, args.min_silence, args.verbose, sil_cache)
        match_silences = chapterize.filter_by_duration(
            silences, args.break_min, args.break_max)
        if len(match_silences) == len(silences):
            print(f"  {len(silences)} found")
        else:
            print(f"  {len(silences)} found, {len(match_silences)} in the "
                  "selected chapter-break band")

    # Verify likely projects using their actual section layout.  Runtime/title
    # ranking remains the fallback when no candidate aligns confidently.
    evaluations = {}
    if not args.no_align and match_silences:
        for candidate in projects[:max(1, args.verify_top)]:
            evaluations[id(candidate)] = align_project(
                candidate, match_silences, local_dur, args)
        confident = [
            candidate for candidate in projects
            if evaluations.get(id(candidate), (None,) * 5)[4]
        ]
        confident.sort(key=lambda candidate: (
            -evaluations[id(candidate)][2]["z"],
            -evaluations[id(candidate)][3]))
        if confident:
            confident_ids = {id(candidate) for candidate in confident}
            projects = confident + [
                candidate for candidate in projects
                if id(candidate) not in confident_ids]

    if len(projects) > 1:
        print(f"\n{len(projects)} candidate project(s):")
        for i, candidate in enumerate(projects[:10], 1):
            total = candidate.total or candidate.sum_sections
            ev = evaluations.get(id(candidate))
            match = ""
            if ev:
                _book, al, _stats, ratio, confident = ev
                match = (f"  match {al.matched}/{al.total}"
                         + (" ✓" if confident else " ?"))
            print(f"  {i:>2}. id={candidate.lv_id:<6} {hms(total):>12} "
                  f"(Δ {(total-local_dur)/60:+6.1f}m)  "
                  f"{len(candidate.sections):>3} sections  "
                  f"{candidate.title[:36]}{match}")
            if candidate.authors:
                print(f"      {'':<6} {'':>12} {'':>9}  "
                      f"by {', '.join(candidate.authors)[:52]}")

    if args.pick < 1 or args.pick > len(projects):
        die(f"--pick {args.pick} out of range (1..{len(projects)})")
    p = projects[args.pick - 1]

    # --- build the timeline ------------------------------------------------
    fallback_starts, published_total, fallback_scale = build_chapters(
        p, local_dur, not args.no_scale)
    book = project_book(p)
    al = stats = None
    ratio = 0.0
    confident = False
    if not args.no_align:
        ev = evaluations.get(id(p))
        if ev is None:
            ev = align_project(p, match_silences, local_dur, args)
        book, al, stats, ratio, confident = ev

    use_alignment = al is not None and (confident or args.offset is not None)
    if use_alignment:
        starts = [
            0.0 if i == 0 else al.apply(ch.start)
            for i, ch in enumerate(book.chapters)
        ]
        scale = al.scale
    else:
        starts = fallback_starts
        scale = fallback_scale

    print(f"\nproject  : {p.title} (id {p.lv_id})")
    if p.authors:
        print(f"author   : {', '.join(p.authors)}")
    print(f"sections : {len(p.sections)}, published total {hms(published_total)}")
    drift = local_dur - published_total
    print(f"drift    : {hms(drift)} over the whole book")
    if al is not None:
        print(f"align    : {al.method}, offset {al.offset:+.3f}s"
              + (f", scale {al.scale:.6f}" if al.scale != 1.0 else ""))
        print(f"           {al.matched}/{al.total} boundaries matched "
              f"({ratio:.0%}); chance {stats['expected']:.1f}, "
              f"enrichment {stats['enrichment']:.1f}x, z={stats['z']:.1f}")
        for note in al.notes:
            print(f"  ! {note}")
    if use_alignment:
        print("           using silence alignment")
    elif args.no_align:
        print(f"           alignment disabled; using whole-runtime scale {scale:.6f}")
    else:
        print("           alignment not confident; using whole-runtime "
              f"scale {scale:.6f}")

    if abs(drift) / max(published_total, 1) > 0.05:
        print("  ! more than 5% off. This is probably a different recording - "
              "check the candidate list above.")

    placed: list[chapterize.Placed] = []
    sil_mids = [s.mid for s in silences]
    for i, (s, st) in enumerate(zip(p.sections, starts)):
        t, snapped, delta = st, False, 0.0
        if args.snap and i > 0 and sil_mids:
            best, bestd = None, args.snap_window
            for m in sil_mids:
                d = abs(m - t)
                if d < bestd:
                    best, bestd = m, d
            if best is not None:
                delta, t, snapped = best - t, best, True
        t = max(0.0, min(t, local_dur))
        placed.append(chapterize.Placed(s.title, t, snapped, delta))

    cleaned = []
    for x in placed:
        if cleaned and x.start < cleaned[-1].start + 2.0:
            continue
        cleaned.append(x)
    if cleaned:
        cleaned[0].start = 0.0
    placed = cleaned

    snapped_n = sum(1 for x in placed if x.snapped)
    print(f"placed   : {len(placed)} chapters"
          + (f", {snapped_n} snapped to silence" if args.snap else ""))
    if args.snap and placed:
        moves = [abs(x.delta) for x in placed if x.snapped]
        if moves:
            print(f"           mean snap distance {sum(moves)/len(moves):.2f}s, "
                  f"max {max(moves):.2f}s")
            if max(moves) > 5:
                print("  ! a large snap suggests the section order differs from "
                      "LibriVox's. Try again without --snap and compare.")

    print()
    print(f"  {'#':>3}  {'start':>12}  {'moved':>8}  title")
    for i, x in enumerate(placed, 1):
        mark = f"{x.delta:+.2f}s" if x.snapped else "-"
        print(f"  {i:>3}  {hms(x.start):>12}  {mark:>8}  {x.title[:52]}")
    print()

    # --- outputs -----------------------------------------------------------
    meta = chapterize.ffmetadata(placed, local_dur, book, audio=args.audio)
    wrote = False

    if args.ffmetadata:
        with open(args.ffmetadata, "w") as f:
            f.write(meta)
        print(f"wrote {args.ffmetadata}")
        wrote = True

    if args.abs_json:
        with open(args.abs_json, "w") as f:
            f.write(chapterize.abs_json(placed, local_dur))
        print(f"wrote {args.abs_json}")
        wrote = True

    if args.embed is not None:
        dest = args.embed
        if not dest:
            root, ext = os.path.splitext(args.audio)
            dest = f"{root}.chapters{ext}"
        if os.path.abspath(dest) == os.path.abspath(args.audio):
            die("--embed destination must differ from the source")
        tmp = dest + ".ffmeta.tmp"
        with open(tmp, "w") as f:
            f.write(meta)
        try:
            chapterize.embed(args.audio, tmp, dest, args.verbose)
        finally:
            os.unlink(tmp)
        print(f"wrote {dest}")
        wrote = True

    if args.split:
        print(f"splitting into {args.split}/ ...")
        chapterize.split(args.audio, placed, local_dur, args.split, args.verbose)
        wrote = True

    if args.report:
        with open(args.report, "w") as f:
            json.dump({
                "source": "librivox",
                "audio": os.path.abspath(args.audio),
                "duration_sec": round(local_dur, 3),
                "librivox_id": p.lv_id,
                "title": p.title,
                "author": ", ".join(p.authors) if p.authors else None,
                "url": p.url,
                "sections": len(p.sections),
                "published_total_sec": round(published_total, 3),
                "drift_sec": round(drift, 3),
                "drift_ratio": round(abs(drift) / max(published_total, 1), 5),
                "scale": round(scale, 6),
                "timeline_source": (
                    "silence_alignment" if use_alignment else "runtime_scale"),
                "align": ({
                    "method": al.method,
                    "accepted": use_alignment,
                    "confident": confident,
                    "offset_sec": round(al.offset, 3),
                    "scale": round(al.scale, 6),
                    "matched": al.matched,
                    "total": al.total,
                    "match_ratio": round(ratio, 4),
                    "mean_error_sec": (
                        round(al.mean_err, 3) if al.matched else None),
                    "expected_by_chance": round(stats["expected"], 3),
                    "enrichment": round(stats["enrichment"], 3),
                    "z": round(stats["z"], 3),
                    "notes": al.notes,
                } if al is not None else None),
                "snapped": snapped_n,
                "chapters": [
                    {"index": i, "title": x.title,
                     "start_sec": round(x.start, 3),
                     "snapped": x.snapped, "moved_sec": round(x.delta, 3)}
                    for i, x in enumerate(placed)
                ],
            }, f, indent=2)
        print(f"wrote {args.report}")
        wrote = True

    if not wrote:
        print("(preview only - pass --embed, --split, --ffmetadata or --abs-json)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
