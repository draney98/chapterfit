#!/usr/bin/env python3
"""
find_asin.py - work out which Audible ASIN matches a local audiobook file.

The hard part is not searching Audible, it's picking the right edition. A
popular novel can have a dozen: different narrators, abridged versions,
dramatizations, regional releases. They share a title and author and differ
only in runtime and chapter layout.

So this ranks candidates on evidence taken from the file itself:

  1. Reads embedded tags and the folder/file name for a title and author.
  2. Searches Audible's public catalog endpoint (no account needed).
  3. Scores each result on runtime proximity to your actual file, plus title
     and author similarity.
  4. With --verify, does the decisive test: pulls each candidate's chapter
     offsets from Audnexus and checks how well they line up against the
     silences in your file. The right edition matches almost every boundary;
     a wrong one matches noise.

Lives next to chapterize.py and reuses its silence detection and alignment.

Examples
--------
  find_asin.py book.mp3
  find_asin.py book.mp3 --title "The Lathe of Heaven" --author "Ursula K. Le Guin"
  find_asin.py book.mp3 --verify              # slow, but decides it
  find_asin.py book.mp3 --verify --top 5 --json out.json
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from chaptertools.silence import chapterize

AUDIBLE_API = "https://api.audible.com/1.0/catalog/products"
REGION_HOST = {
    "us": "api.audible.com",
    "uk": "api.audible.co.uk",
    "ca": "api.audible.ca",
    "au": "api.audible.com.au",
    "de": "api.audible.de",
    "fr": "api.audible.fr",
    "it": "api.audible.it",
    "es": "api.audible.es",
    "in": "api.audible.in",
    "jp": "api.audible.co.jp",
}
USER_AGENT = "find_asin.py/1.0"


# --------------------------------------------------------------------------
# local file inspection
# --------------------------------------------------------------------------

@dataclass
class LocalFile:
    path: str
    duration: float
    tag_title: str | None = None
    tag_author: str | None = None
    tag_album: str | None = None
    guess_title: str | None = None
    guess_author: str | None = None

    @property
    def title(self) -> str | None:
        return self.tag_album or self.tag_title or self.guess_title

    @property
    def author(self) -> str | None:
        return self.tag_author or self.guess_author


def probe(path: str) -> LocalFile:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", path],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"error: ffprobe failed on {path}", file=sys.stderr)
        sys.exit(1)
    fmt = json.loads(r.stdout).get("format", {})
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}

    lf = LocalFile(path=path, duration=float(fmt.get("duration", 0)))
    lf.tag_title = tags.get("title")
    lf.tag_author = tags.get("artist") or tags.get("album_artist") or tags.get("author")
    lf.tag_album = tags.get("album")

    # Author/Title/file.mp3 is the Audiobookshelf layout, so lean on it
    ap = os.path.abspath(path)
    title_dir = os.path.dirname(ap)
    author_dir = os.path.dirname(title_dir)
    parts = ap.split(os.sep)
    if len(parts) >= 3:
        maybe_author = clean_name(parts[-3])
        if maybe_author and not _looks_like_plumbing(maybe_author, author_dir):
            lf.guess_author = maybe_author
        maybe_title = clean_name(parts[-2])
        if maybe_title and not _looks_like_plumbing(maybe_title, title_dir):
            lf.guess_title = maybe_title
    if not lf.guess_title:
        lf.guess_title = clean_name(os.path.splitext(os.path.basename(path))[0])
    if lf.guess_title and lf.guess_author:
        lf.guess_title = strip_author(lf.guess_title, lf.guess_author)
    return lf


PLUMBING = {
    "tmp", "var", "usr", "opt", "mnt", "srv", "home", "root", "media", "run",
    "books", "book", "audiobooks", "audiobook", "audio", "music", "downloads",
    "download", "desktop", "documents", "library", "libraries", "data",
    "files", "new", "temp", "incoming", "unsorted", "misc", "output", "input",
}


def _looks_like_plumbing(name: str, dirpath: str | None = None) -> bool:
    """Filter out filesystem scaffolding that is not an author or title."""
    if dirpath:
        real = os.path.abspath(dirpath).rstrip(os.sep)
        home = os.path.abspath(os.path.expanduser("~")).rstrip(os.sep)
        # the home directory, anything above it, or a filesystem root
        if real == home or real == os.path.dirname(home) or real.count(os.sep) <= 1:
            return True
    n = name.strip().lower()
    return (not n) or n in PLUMBING or len(n) < 3


def clean_name(s: str) -> str:
    if not s:
        return ""
    s = s.replace("_", " ")
    s = re.sub(r"\s*-\s*Top \d+.*$", " ", s, flags=re.I)   # collection suffixes
    s = re.sub(r"[\[\(].*?[\]\)]", " ", s)                 # bracketed junk
    s = re.sub(r"\b(\w{2,})\s+s\b", r"\1's", s)            # Ender s -> Ender's
    s = re.sub(r"\bunabridged\b|\babridged\b", " ", s, flags=re.I)
    s = re.sub(r"\b(mp3|m4b|m4a|64k|128k|vbr|cbr)\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" -_.")
    # Only a *trailing* year is metadata; a leading one may be the title (1984).
    s = re.sub(r"\s+\b(1[5-9]|20)\d{2}\b\s*$", "", s)
    return s.strip(" -_.")


def strip_author(title: str, author: str) -> str:
    """Filenames often tack the author onto the title. Remove it if so."""
    if not title or not author:
        return title
    norm = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    a = norm(author)
    if not a:
        return title
    # hyphens are used as a separator as often as spaces are
    words = [w for w in re.split(r"[\s\-]+", title) if w]
    a_tokens = {norm(w) for w in re.split(r"[\s\-]+", author) if norm(w)}
    for cut in range(min(len(words) - 1, 6), 0, -1):
        tail_words = words[-cut:]
        tail = norm(" ".join(tail_words))
        if not tail:
            continue
        t_tokens = {norm(w) for w in tail_words if norm(w)}
        if (tail == a or t_tokens == a_tokens or
                difflib.SequenceMatcher(None, tail, a).ratio() > 0.92):
            trimmed = " ".join(words[:-cut]).strip(" -_.,")
            if trimmed:
                return trimmed
    return title


# --------------------------------------------------------------------------
# Audible catalog search
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    asin: str
    title: str
    subtitle: str | None
    authors: list[str]
    narrators: list[str]
    runtime_min: int | None
    release: str | None
    publisher: str | None
    language: str | None
    # scoring
    runtime_score: float = 0.0
    text_score: float = 0.0
    score: float = 0.0
    # verification
    verified: bool = False
    matched: int = 0
    total: int = 0
    mean_err: float | None = None
    offset: float | None = None
    note: str = ""

    @property
    def runtime_s(self) -> float | None:
        return self.runtime_min * 60.0 if self.runtime_min else None

    @property
    def match_ratio(self) -> float:
        return self.matched / self.total if self.total else 0.0


def http_json(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def search_audible(title: str | None, author: str | None, keywords: str | None,
                   region: str, num: int, verbose: bool) -> list[Candidate]:
    host = REGION_HOST.get(region, REGION_HOST["us"])
    params = {
        "num_results": str(min(num, 50)),
        "products_sort_by": "Relevance",
        "response_groups": "contributors,product_desc,product_attrs",
    }
    if keywords:
        params["keywords"] = keywords
    else:
        if title:
            params["title"] = title
        if author:
            params["author"] = author

    url = f"https://{host}/1.0/catalog/products?" + urllib.parse.urlencode(params)
    if verbose:
        print(f"  GET {url}", file=sys.stderr)

    try:
        payload = http_json(url)
    except urllib.error.HTTPError as e:
        print(f"error: Audible search HTTP {e.code}: "
              f"{e.read().decode('utf-8', 'replace')[:300]}", file=sys.stderr)
        return []
    except urllib.error.URLError as e:
        print(f"error: could not reach Audible: {e.reason}", file=sys.stderr)
        return []

    out = []
    for p in payload.get("products", []):
        out.append(Candidate(
            asin=p.get("asin", ""),
            title=p.get("title", ""),
            subtitle=p.get("subtitle"),
            authors=[a.get("name", "") for a in (p.get("authors") or [])],
            narrators=[n.get("name", "") for n in (p.get("narrators") or [])],
            runtime_min=p.get("runtime_length_min"),
            release=(p.get("release_date") or "")[:10] or None,
            publisher=p.get("publisher_name"),
            language=p.get("language"),
        ))
    return [c for c in out if c.asin]


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def sim(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def score_candidates(cands: list[Candidate], lf: LocalFile):
    for c in cands:
        # Runtime is the strongest single signal. Audible's figure includes a
        # brand intro/outro your rip may lack, so allow a small bias.
        if c.runtime_s and lf.duration:
            diff = abs(c.runtime_s - lf.duration)
            rel = diff / max(c.runtime_s, 1.0)
            if rel <= 0.005:
                c.runtime_score = 1.0
            elif rel <= 0.02:
                c.runtime_score = 0.85
            elif rel <= 0.05:
                c.runtime_score = 0.5
            elif rel <= 0.15:
                c.runtime_score = 0.15
            else:
                c.runtime_score = 0.0
        else:
            c.runtime_score = 0.25  # unknown runtime, don't punish outright

        t = max(sim(c.title, lf.title), sim(c.title, lf.guess_title))
        a = 0.0
        for name in c.authors:
            a = max(a, sim(name, lf.author), sim(name, lf.guess_author))
        c.text_score = 0.65 * t + 0.35 * a
        c.score = 0.6 * c.runtime_score + 0.4 * c.text_score

    cands.sort(key=lambda c: -c.score)


# --------------------------------------------------------------------------
# verification via chapter alignment
# --------------------------------------------------------------------------

def verify(cands: list[Candidate], lf: LocalFile, region: str,
           noise: float, min_silence: float, tol: float, max_offset: float,
           verbose: bool, sil_cache=None, hint=None, hint_window=15.0):
    print(f"detecting silences (noise {noise}dB, min {min_silence}s)...")
    silences = chapterize.detect_silences(lf.path, noise, min_silence,
                                          verbose, sil_cache)
    print(f"  {len(silences)} silences found")
    if not silences:
        print("  ! no silences; verification cannot discriminate")
        return

    for c in cands:
        url = (f"{chapterize.AUDNEX_BASE}/books/{c.asin}/chapters"
               f"?region={region}")
        try:
            payload = http_json(url)
            book = chapterize.parse_audnex(payload, c.asin)
        except urllib.error.HTTPError as e:
            c.note = f"no chapter data (HTTP {e.code})"
            continue
        except Exception as e:  # noqa: BLE001
            c.note = f"chapter fetch failed: {type(e).__name__}"
            continue

        if len(book.chapters) < 2:
            c.note = "only one chapter listed"
            continue

        al = chapterize.solve_alignment(
            book, silences, lf.duration, "auto", tol, max_offset,
            allow_scale=True, verbose=False,
            hint=hint, hint_window=hint_window)
        c.verified = True
        c.matched, c.total = al.matched, al.total
        c.mean_err = al.mean_err if al.matched else None
        c.offset = al.offset
        print(f"  {c.asin}  {c.matched:>3}/{c.total:<3} boundaries  "
              f"offset {al.offset:+8.2f}s  {c.title[:44]}")

    # verified evidence outranks the heuristic score
    cands.sort(key=lambda c: (
        -(c.match_ratio if c.verified else -1),
        c.mean_err if c.mean_err is not None else 1e9,
        -c.score,
    ))


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

def hhmm(seconds: float | None) -> str:
    if not seconds:
        return "?"
    h, m = divmod(int(round(seconds / 60)), 60)
    return f"{h}h{m:02d}m"


def report(cands: list[Candidate], lf: LocalFile, top: int, verified_run: bool):
    print()
    print(f"local file : {os.path.basename(lf.path)}  ({hhmm(lf.duration)})")
    if lf.title:
        print(f"guessed    : {lf.title!r}"
              + (f" by {lf.author!r}" if lf.author else ""))
    print()

    if not cands:
        print("no candidates found.")
        return

    hdr = f"  {'#':>2}  {'ASIN':<12} {'runtime':>8} {'Δ':>8}"
    hdr += f" {'match':>7}" if verified_run else f" {'score':>6}"
    hdr += "  title / narrator"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for i, c in enumerate(cands[:top], 1):
        delta = (c.runtime_s - lf.duration) if c.runtime_s else None
        dstr = f"{delta/60:+.0f}m" if delta is not None else "?"
        if verified_run:
            metric = (f"{c.matched}/{c.total}" if c.verified else "-")
        else:
            metric = f"{c.score:.2f}"
        who = ", ".join(c.narrators) or "?"
        print(f"  {i:>2}  {c.asin:<12} {hhmm(c.runtime_s):>8} {dstr:>8} "
              f"{metric:>7}  {c.title[:46]}")
        print(f"      {'':<12} {'':>8} {'':>8} {'':>7}  read by {who[:44]}"
              + (f"  [{c.note}]" if c.note else ""))

    print()
    best = cands[0]
    if verified_run and best.verified and best.match_ratio >= 0.7:
        runner = cands[1] if len(cands) > 1 else None
        gap = ""
        if runner and runner.verified:
            gap = f" (next best {runner.matched}/{runner.total})"
        print(f"best match : {best.asin} - {best.matched}/{best.total} chapter "
              f"boundaries land on a silence{gap}")
        print(f"             ./chapterize.py '{lf.path}' --asin {best.asin} --embed")
    elif verified_run:
        print("no candidate aligned convincingly. The file may be a different "
              "edition, a fan recording, or LibriVox rather than Audible.")
    else:
        print(f"top guess  : {best.asin}  (heuristic only)")
        print("             re-run with --verify to test candidates against the "
              "actual audio")


def to_json(cands: list[Candidate], lf: LocalFile) -> str:
    return json.dumps({
        "file": lf.path,
        "duration_sec": round(lf.duration, 3),
        "guessed_title": lf.title,
        "guessed_author": lf.author,
        "candidates": [{
            "asin": c.asin, "title": c.title, "subtitle": c.subtitle,
            "authors": c.authors, "narrators": c.narrators,
            "runtime_min": c.runtime_min, "release": c.release,
            "publisher": c.publisher, "language": c.language,
            "score": round(c.score, 4),
            "verified": c.verified, "matched": c.matched, "total": c.total,
            "match_ratio": round(c.match_ratio, 4),
            "mean_error_sec": round(c.mean_err, 3) if c.mean_err else None,
            "offset_sec": round(c.offset, 3) if c.offset is not None else None,
            "note": c.note,
        } for c in cands],
    }, indent=2)


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Identify the Audible ASIN for a local audiobook file.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("--title", help="override the guessed title")
    ap.add_argument("--author", help="override the guessed author")
    ap.add_argument("--keywords", help="free-text search instead of title/author")
    ap.add_argument("--region", default="us",
                    help="Audible region: us, uk, ca, au, de, fr, jp (default us)")
    ap.add_argument("--num", type=int, default=20,
                    help="how many search results to pull (max 50, default 20)")
    ap.add_argument("--top", type=int, default=8,
                    help="how many candidates to display (default 8)")
    ap.add_argument("--verify", action="store_true",
                    help="test candidates against the file's silences (slow, decisive)")
    ap.add_argument("--verify-top", type=int, default=6,
                    help="how many candidates to verify (default 6)")
    ap.add_argument("--noise", type=float, default=-40.0)
    ap.add_argument("--silence-cache", default=None,
                    help="directory path, or 'sidecar' to write "
                         "<file>.silences.json next to the audio")
    ap.add_argument("--no-silence-cache", action="store_true")
    ap.add_argument("--min-silence", type=float, default=1.5)
    ap.add_argument("--tolerance", type=float, default=4.0)
    ap.add_argument("--max-offset", type=float, default=120.0)
    ap.add_argument("--offset-hint", type=float,
                    help="roughly where the offset is, in seconds")
    ap.add_argument("--offset-window", type=float, default=15.0)
    ap.add_argument("--json", metavar="FILE", help="write full results as JSON")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.audio):
        print(f"error: no such file: {args.audio}", file=sys.stderr)
        sys.exit(1)

    lf = probe(args.audio)
    if args.title:
        lf.tag_album, lf.guess_title = args.title, args.title
    if args.author:
        lf.tag_author, lf.guess_author = args.author, args.author

    if not (args.keywords or lf.title):
        print("error: could not guess a title; pass --title or --keywords",
              file=sys.stderr)
        sys.exit(1)

    print(f"searching Audible ({args.region})...")
    cands = search_audible(args.title or lf.title, args.author or lf.author,
                           args.keywords, args.region, args.num, args.verbose)

    # A title+author search that comes back empty often succeeds as keywords.
    if not cands and not args.keywords:
        kw = " ".join(x for x in [lf.title, lf.author] if x)
        print(f"  nothing; retrying as keywords: {kw!r}")
        cands = search_audible(None, None, kw, args.region, args.num, args.verbose)

    print(f"  {len(cands)} results")
    score_candidates(cands, lf)

    if args.verify and cands:
        print()
        sil_cache = False if args.no_silence_cache else (
            args.silence_cache or chapterize.DEFAULT_CACHE)
        verify(cands[:args.verify_top], lf, args.region, args.noise,
               args.min_silence, args.tolerance, args.max_offset,
               args.verbose, sil_cache, args.offset_hint,
               args.offset_window)
        rest = cands[args.verify_top:]
        cands = cands[:args.verify_top] + rest

    report(cands, lf, args.top, args.verify)

    if args.json:
        with open(args.json, "w") as f:
            f.write(to_json(cands, lf))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
