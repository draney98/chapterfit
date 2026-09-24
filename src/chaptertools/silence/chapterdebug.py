#!/usr/bin/env python3
"""
chapterdebug.py - see why an alignment failed, then fix it by hand.

Three modes, meant to be used in that order.

  inspect   Pull the chapter list for an ASIN (or a LibriVox project, or a
            local JSON) and show, for every chapter, where the published
            timeline says the break is, where the automatic alignment puts
            it, and which silence in your file is nearest. Also lists the
            long silences that no chapter claims - those are usually the
            real breaks, and seeing them next to the predictions makes the
            failure mode obvious.

  clips     Cut a few seconds of audio around each predicted break so you
            can listen and find where the chapters actually start.

  anchor    You tell it the true position of two or three chapters and it
            solves for the rest. Two anchors pin down both unknowns
            (a constant offset and a time scale) exactly, so this works
            even when the automatic search finds nothing.

Examples
--------
  chapterdebug.py inspect book.mp3 --asin B002V1OF70
  chapterdebug.py inspect book.mp3 --chapters-json ch.json --timeline
  chapterdebug.py clips book.mp3 --asin B002V1OF70 --out /tmp/clips
  chapterdebug.py anchor book.mp3 --asin B002V1OF70 \
        --anchor 2=0:04:31 --anchor 14=3:58:02
  chapterdebug.py anchor book.mp3 --asin B002V1OF70 \
        --anchor 2=271 --anchor 9=8102 --anchor 14=14282 --piecewise --embed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

from chaptertools.silence import chapterize


# ==========================================================================
# small helpers
# ==========================================================================

def hms(sec: float | None) -> str:
    if sec is None:
        return "?"
    neg, sec = sec < 0, abs(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    out = f"{int(h)}:{int(m):02d}:{s:06.3f}"
    return ("-" + out) if neg else out


def parse_time(v: str) -> float:
    """'1:23:45.5', '83:45', '5025.5' -> seconds."""
    v = str(v).strip()
    if not v:
        raise ValueError("empty time")
    if ":" in v:
        total = 0.0
        for part in v.split(":"):
            total = total * 60 + float(part or 0)
        return total
    return float(v)


def die(msg: str):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# ==========================================================================
# loading a chapter list from any of the sources
# ==========================================================================

def load_chapters(args) -> tuple[list, str]:
    """Returns (chapters, description). Chapters are chapterize.Chapter."""
    if args.chapters_json:
        with open(args.chapters_json) as f:
            book = chapterize.parse_audnex(json.load(f), "local")
        return book.chapters, f"{args.chapters_json} ({len(book.chapters)} chapters)"

    if args.lv_sections_json or args.lv_id:
        from chaptertools.sources import librivox_chapters as lv
        if args.lv_sections_json:
            with open(args.lv_sections_json) as f:
                payload = json.load(f)
            books = payload.get("books", payload)
            if isinstance(books, dict):
                books = [books]
            projects = [lv.parse_project(b) for b in books]
        else:
            projects = lv.lv_query({"id": args.lv_id}, args.verbose)
        projects = [p for p in projects if p.sections]
        if not projects:
            die("no LibriVox project with section data")
        p = projects[0]
        chapters, t = [], 0.0
        for s in p.sections:
            chapters.append(chapterize.Chapter(s.title, t, s.duration))
            t += s.duration
        return chapters, f"LibriVox {p.lv_id} - {p.title} ({len(chapters)} sections)"

    if args.asin:
        url = (f"{chapterize.AUDNEX_BASE}/books/{args.asin}/chapters"
               f"?region={args.region}")
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "chapterdebug.py/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                payload = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            die(f"Audnexus HTTP {e.code} for {args.asin}")
        except urllib.error.URLError as e:
            die(f"could not reach Audnexus: {e.reason}")
        book = chapterize.parse_audnex(payload, args.asin)
        if args.save_chapters:
            with open(args.save_chapters, "w") as f:
                json.dump(payload, f, indent=1)
            print(f"saved chapter data to {args.save_chapters}")
        return book.chapters, (f"Audnexus {args.asin} "
                               f"({len(book.chapters)} chapters, "
                               f"intro {book.brand_intro:.1f}s)")

    die("need one of --asin, --lv-id, --chapters-json or --lv-sections-json")


def get_silences(path: str, args, n_chapters: int = 0) -> list:
    """Detect silences, auto-tuning the threshold when the default finds nothing.

    The -40dB default suits clean digital audio. Tape transfers have a noise
    floor well above it, and silencedetect then returns almost nothing - which
    used to produce a full table of meaningless results. Probe instead.
    """
    cache = False if args.no_silence_cache else args.silence_cache
    explicit = args.noise is not None
    noise = args.noise if explicit else -40.0
    min_sil = args.min_silence

    silences = chapterize.detect_silences(path, noise, min_sil,
                                          args.verbose, cache)
    floor = max(3, int(n_chapters * 0.25)) if n_chapters else 3
    if len(silences) >= floor:
        args.noise = noise
        return silences

    print(f"\n  only {len(silences)} silence(s) at {noise}dB - too few to work "
          f"with.")

    if explicit:
        print(f"  You set --noise {noise} explicitly, so leaving it alone.\n"
              f"  Run:  chapterdebug.py tape {_q(path)} "
              f"--expect-chapters {n_chapters or 50}")
        args.noise = noise
        return silences

    # probe a sample rather than rescanning the whole file each time
    dur = chapterize.probe_duration(path)
    sample = min(10 * 60.0, dur)
    start = max(0.0, (dur - sample) * 0.4)
    print(f"  probing thresholds on {hms(sample)} of audio...")
    counts = {}
    for db in (-50, -45, -40, -35, -30, -25, -20):
        counts[db] = count_silences(path, start, sample, db, min_sil)
        if args.verbose:
            print(f"    {db}dB -> {counts[db]}")
    rec = recommend_threshold(counts)

    if rec is None:
        print("  no threshold produced silences. This file may be heavily "
              "compressed\n  or noise-gated; there is nothing to align against.")
        args.noise = noise
        return silences

    print(f"  -> using --noise {rec}. Pass it explicitly to skip this probe.")
    args.noise = float(rec)
    silences = chapterize.detect_silences(path, float(rec), min_sil,
                                          args.verbose, cache)
    print(f"  {len(silences)} silences at {rec}dB")
    if len(silences) < floor:
        print(f"  still under {floor}. Try a smaller --min-silence, or run\n"
              f"  chapterdebug.py tape {_q(path)} "
              f"--expect-chapters {n_chapters or 50}")
    return silences


def silences_usable(silences, n_chapters: int) -> bool:
    return len(silences) >= max(3, int(n_chapters * 0.25))


def warn_unusable(silences, n_chapters: int, path: str, noise, min_sil):
    print(f"""
{'=' * 78}
  STOP - only {len(silences)} silence(s) detected across the whole file.

  Nothing below can be verified against the audio. The 'nearest silence'
  column will show the same one or two entries for every chapter, because
  those are the only ones that exist. Expected times are still printed and
  are still usable for navigation, but no delta means anything.

  Fix detection first:
      chapterdebug.py tape {_q(path)} --expect-chapters {n_chapters}
  then re-run with the --noise and --min-silence it recommends.
{'=' * 78}""")


def nearest(t: float, mids: list[float]) -> tuple[float | None, float]:
    best, bestd = None, float("inf")
    for m in mids:
        d = abs(m - t)
        if d < bestd:
            best, bestd = m, d
    return best, bestd


def parse_anchors(specs: list[str], chapters: list):
    """'2=4:48' or '@13.798=4:48' -> [(published_sec, actual_sec)], labels."""
    pairs: list[tuple[float, float]] = []
    labels: list[str] = []
    idx: list[int | None] = []
    for spec in specs:
        if "=" not in spec:
            die(f"anchor {spec!r} should look like CHAPTER=TIME or @PUB=TIME")
        left, right = spec.split("=", 1)
        left = left.strip()
        try:
            actual = parse_time(right)
        except ValueError:
            die(f"could not read the time in anchor {spec!r}")
        if left.startswith("@"):
            published = parse_time(left[1:])
            labels.append(f"published {hms(published)}")
            idx.append(None)
        else:
            try:
                n = int(left)
            except ValueError:
                die(f"{left!r} is not a chapter number")
            if not 1 <= n <= len(chapters):
                die(f"chapter {n} out of range (1..{len(chapters)})")
            published = chapters[n - 1].start
            labels.append(f"chapter {n} ({chapters[n-1].title[:28]})")
            idx.append(n)
        pairs.append((published, actual))
    return pairs, labels, idx


def make_mapper(pairs, piecewise: bool, offset_only: bool):
    """Returns (map_fn, scale, offset, residuals) for a set of anchors."""
    if offset_only:
        scale = 1.0
        offset = sum(a - p for p, a in pairs) / len(pairs)
    else:
        scale, offset = fit_linear(pairs)
    residuals = [a - (scale * p + offset) for p, a in pairs]

    if piecewise and len(pairs) >= 2:
        fn = lambda t: piecewise_map(t, pairs)
        residuals = [0.0 for _ in pairs]
    else:
        fn = lambda t: scale * t + offset
    return fn, scale, offset, residuals


def render_chapter_table(chapters, estimates, actuals, mids, tolerance,
                         anchor_idx, silences, dur, label="expected",
                         usable=True, top_rank=None, top_n=15,
                         band=None, side_factor=1.8, side_mids=None):
    side_mids = side_mids or set()
    """One table shared by inspect and anchor so they cannot drift apart.

      published  what the API says
      actual     what you measured, blank unless this row is an anchor
      expected   what the current model predicts
      delta      nearest silence minus expected - independent evidence,
                 since your anchors say nothing about where silences are
    """
    top_rank = top_rank or {}
    print(f"\n  {'#':>3} {'A':>1} {'published':>12} {'actual':>12} "
          f"{label:>12} {'nearest sil':>12} {'len':>6} {'fit':>5} "
          f"{'top':>6} {'delta':>9}  title")
    print("  " + "-" * 120)

    hits = 0
    checkable = 0
    rows = []
    for i, ch in enumerate(chapters):
        est = estimates[i]
        act = actuals.get(i)
        amark = "A" if (i + 1) in anchor_idx or act is not None else " "

        if i == 0:
            print(f"  {i+1:>3} {amark:>1} {hms(ch.start):>12} "
                  f"{hms(act) if act is not None else '-':>12} "
                  f"{hms(est):>12} {'(pinned)':>12} {'':>6} {'':>5} "
                  f"{'':>6} {'-':>9}  {ch.title[:34]}")
            continue

        pool = ([x for x in mids if round(x, 3) not in side_mids]
                if side_mids else mids)
        m, d = nearest(est, pool) if (pool and usable) else (None, float("inf"))
        checkable += 1
        hit = d <= tolerance
        if hit:
            hits += 1
        flag = " " if (hit or not usable) else "?"
        delta_s = f"{m - est:+.2f}s" if m is not None else "-"
        rank_s = fmt_rank(top_rank.get(round(m, 3)) if m is not None else None,
                          top_n)
        sil = next((x for x in silences
                    if m is not None and abs(x.mid - m) < 1e-6), None)
        len_s = f"{sil.dur:.2f}s" if sil else ""
        fit_s = classify_silence(sil.dur, band, side_factor) if sil else ""
        if sil and side_mids and round(sil.mid, 3) in side_mids:
            fit_s = "SIDE"
        print(f" {flag}{i+1:>3} {amark:>1} {hms(ch.start):>12} "
              f"{hms(act) if act is not None else '-':>12} "
              f"{hms(est):>12} {hms(m) if m is not None else '-':>12} "
              f"{len_s:>6} {fit_s:>5} {rank_s:>6} {delta_s:>9}  "
              f"{ch.title[:34]}")
        rows.append((i, est, m, d))

    if band:
        lo, hi, mean, _ = band
        print(f"\n  fit: '==' means the silence matches the "
              f"{mean:.2f}s chapter-break signature\n"
              f"       ({lo:.2f}-{hi:.2f}s); 'SIDE' means it is far longer and "
              f"is probably a tape change.")
    if top_rank:
        hit_top = sum(1 for r in rows
                      if r[2] is not None
                      and (top_rank.get(round(r[2], 3)) or 10**9) <= top_n
                      and r[3] <= tolerance)
        print(f"\n  top = the silence's rank by length in this file "
              f"(#1 = longest). A star marks\n        the top {top_n}, the "
              f"strongest chapter-break candidates; {hit_top} expected "
              f"position(s)\n        landed on one.")
    print(f"\n  published = from the chapter data.  actual = what you measured.")
    print(f"  {label} = the model's estimate.  delta = nearest silence minus "
          f"{label}.")
    if anchor_idx or actuals:
        print("  rows marked A are anchors; every other row is derived from them.")

    if not usable:
        print("\n  silence columns are blank: the scan found too little to "
              "compare against.")
        return rows
    if checkable and silences:
        st = chapterize.match_stats(hits, checkable, len(silences), dur, tolerance)
        print(f"\n  {hits}/{checkable} estimates land within {tolerance:.0f}s of "
              f"a silence; chance would give {st['expected']:.1f} "
              f"({st['enrichment']:.1f}x, z={st['z']:.1f})")
        if st["z"] < 3 and checkable > 4:
            print("  ! not confirmed by the audio - treat these positions as "
                  "provisional")
    return rows


def suggest_next_anchors(chapters, estimates, pairs, dur, n: int = 3):
    """Which chapters are worth measuring next?

    Anchors are only useful where there is currently no evidence. Find the
    widest stretches of the book between existing anchors and propose the
    chapter nearest the middle of each.
    """
    if not chapters:
        return []
    anchored = sorted(p for p, _ in pairs)
    edges = [0.0] + anchored + [chapters[-1].start]
    gaps = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        if hi - lo > 1e-6:
            gaps.append((hi - lo, (lo + hi) / 2.0))
    gaps.sort(reverse=True)

    used = set()
    out = []
    for _, mid in gaps:
        best, bestd = None, float("inf")
        for j, ch in enumerate(chapters):
            if j == 0 or j in used:
                continue
            if any(abs(ch.start - p) < 1e-6 for p, _ in pairs):
                continue
            d = abs(ch.start - mid)
            if d < bestd:
                best, bestd = j, d
        if best is not None:
            used.add(best)
            out.append((best + 1, chapters[best], estimates[best]))
        if len(out) >= n:
            break
    return out


def anchor_signature(pairs, silences, window: float = 6.0,
                     exclude=None):
    """The silence at each anchored position, chosen by prominence.

    Picking the *closest* silence is wrong on a dense file. With a silence
    every 28 seconds, a six-second window nearly always contains a sentence
    pause, and the band then describes pauses rather than chapter breaks. A
    3.1s gap two seconds away is the break; the 1.6s gap half a second away
    is not. Score length against distance and take the winner.
    """
    hits = []
    for pub, act in pairs:
        best, best_score, nearest_s, nearest_d = None, -1.0, None, window
        for s in silences:
            if exclude and round(s.mid, 3) in exclude:
                continue
            d = abs(s.mid - act)
            if d > window:
                continue
            if d < nearest_d:
                nearest_s, nearest_d = s, d
            score = s.dur / (1.0 + d / max(window, 0.1))
            if score > best_score:
                best, best_score = s, score
        if best is not None:
            hits.append((pub, act, best, best.mid - act, nearest_s))
    return hits


def signature_band(hits, tol: float, min_samples: int = 2, override=None):
    """(low, high, centre, spread) duration band for a chapter break."""
    if override:
        return (max(0.0, override - tol), override + tol, override, 0.0)
    if len(hits) < min_samples:
        return None
    durs = sorted(h[2].dur for h in hits)
    # median, not mean: one bad anchor should not drag the band
    mid = durs[len(durs) // 2] if len(durs) % 2 else \
        (durs[len(durs) // 2 - 1] + durs[len(durs) // 2]) / 2
    spread = max(abs(d - mid) for d in durs)
    half = max(tol, spread)
    return (max(0.0, mid - half), mid + half, mid, spread)


def validate_band(band, chapters, estimates, silences, dur, tolerance):
    """How many expected positions land on a silence inside the band?"""
    if not band:
        return None
    lo, hi, _, _ = band
    fits = [s.mid for s in silences if lo <= s.dur <= hi]
    if not fits:
        return None
    hits = 0
    for i in range(1, len(chapters)):
        _, d = nearest(estimates[i], fits)
        if d <= tolerance:
            hits += 1
    total = max(1, len(chapters) - 1)
    st = chapterize.match_stats(hits, total, len(fits), dur, tolerance)
    return {"hits": hits, "total": total, "n_fits": len(fits), **st}


def sweep_bands(chapters, estimates, silences, dur, tolerance, tol):
    """If the anchors gave a poor band, is some other duration better?"""
    durs = sorted({round(s.dur, 1) for s in silences})
    best = None
    for centre in durs:
        band = (max(0.0, centre - tol), centre + tol, centre, 0.0)
        v = validate_band(band, chapters, estimates, silences, dur, tolerance)
        if not v or v["n_fits"] < 3:
            continue
        if best is None or v["z"] > best[1]["z"]:
            best = (centre, v)
    return best


def classify_silence(dur: float, band, side_factor: float) -> str:
    """'==' fits the chapter-break signature, 'SIDE' is far longer."""
    if not band:
        return ""
    lo, hi, mean, _ = band
    if mean > 0 and dur >= mean * side_factor:
        return "SIDE"
    if lo <= dur <= hi:
        return "=="
    return ""


def print_signature(hits, band, why, detail, silences, side_factor,
                    top_rank, top_n):
    print(f"\n--- chapter-break signature ---")
    if not hits:
        print("  no silence found near any anchored position - either the "
              "anchors are\n  off, or the breaks there were trimmed out.")
        return
    for pub, act, s_, off, nearest_s in hits:
        print(f"  anchor at {hms(act):>13} -> silence {s_.dur:>5.2f}s "
              f"{fmt_rank(top_rank.get(round(s_.mid, 3)), top_n):>7} "
              f"({off:+.2f}s from the anchor)")
        if nearest_s is not None and abs(nearest_s.mid - s_.mid) > 1e-6:
            print(f"{'':>24}  closest was {nearest_s.dur:.2f}s at "
                  f"{nearest_s.mid - act:+.2f}s, passed over as too short")
    if not band:
        print(f"  {why}")
        return

    lo, hi, centre, spread = band
    print(f"\n  break length: {why}")
    print(f"  band {lo:.2f}-{hi:.2f}s")
    if detail:
        v, rejected = detail
        print(f"  {v['hits']}/{v['total']} expected positions land on a "
              f"matching silence; chance {v['expected']:.1f} "
              f"({v['enrichment']:.1f}x, z={v['z']:.1f})")
        for c, sv in rejected:
            print(f"    rejected {c:.2f}s: {sv['hits']}/{sv['total']}, "
                  f"{sv['enrichment']:.1f}x, z={sv['z']:.1f}")
        if v["z"] < 3:
            print(f"""
  ! No observed break length is clearly confirmed. Your anchor times are
    probably a second or two off, so each one picked up a pause instead of
    the break. Re-measure them, or force a length with --break-len once you
    can see the real one in the table.""")

    sel = band_selectivity(band, silences)
    fits = int(sel * len(silences))
    print(f"\n  {fits} of {len(silences)} silences match ({sel*100:.0f}%)")
    if sel > 0.35:
        print(f"  ! that band is not selective - it matches most of the file "
              f"and will not\n    discriminate. Narrow it with --sig-tol, or "
              f"set --break-len directly.")

    sides = [x for x in silences
             if centre > 0 and x.dur >= centre * side_factor]
    if sides:
        print(f"\n  {len(sides)} silences are at least {side_factor:.1f}x "
              f"the signature - likely tape\n  side changes, not chapters:")
        for x in sorted(sides, key=lambda y: y.mid)[:12]:
            print(f"      {hms(x.mid):>13} {x.dur:>6.2f}s "
                  f"{fmt_rank(top_rank.get(round(x.mid, 3)), top_n):>7}")
        if len(sides) > 12:
            print(f"      ... and {len(sides) - 12} more")
        print("  A chapter should not land on one of those.")


def choose_band(hits, tol, chapters, estimates, silences, dur, tolerance,
                override=None):
    """Pick the break length that the audio actually supports.

    Averaging the anchor observations fails when one anchor landed on a
    pause: the band widens to cover both populations and stops selecting
    anything. Instead, treat each observed duration as a hypothesis and keep
    whichever one best predicts the remaining chapters.
    """
    if override:
        return ((max(0.0, override - tol), override + tol, override, 0.0),
                "forced by --break-len", None)
    if not hits:
        return None, "no anchor landed on a silence", None

    observed = sorted({round(h[2].dur, 2) for h in hits})
    if not (chapters and estimates and dur):
        durs = sorted(h[2].dur for h in hits)
        mid = durs[len(durs) // 2]
        spread = max(abs(d - mid) for d in durs)
        half = max(tol, spread)
        return ((max(0.0, mid - half), mid + half, mid, spread),
                "median of the anchors", None)

    scored = []
    for centre in observed:
        band = (max(0.0, centre - tol), centre + tol, centre, 0.0)
        v = validate_band(band, chapters, estimates, silences, dur, tolerance)
        if v and v["n_fits"] >= 2:
            scored.append((v["z"], centre, band, v))
    if not scored:
        durs = sorted(h[2].dur for h in hits)
        mid = durs[len(durs) // 2]
        return ((max(0.0, mid - tol), mid + tol, mid, 0.0),
                "median of the anchors (none validated)", None)

    scored.sort(key=lambda x: -x[0])
    z, centre, band, v = scored[0]
    why = f"{centre:.2f}s - the anchor observation the audio best supports"
    return band, why, (v, [(c, sv) for _, c, _, sv in scored[1:]])


def band_selectivity(band, silences) -> float:
    if not band or not silences:
        return 1.0
    lo, hi, _, _ = band
    return sum(1 for s in silences if lo <= s.dur <= hi) / len(silences)


def signature_context(est: float, silences, band, n: int,
                      exclude=None):
    """Nearest signature-matching silences before and after a position."""
    if not band:
        return [], []
    lo, hi, _, _ = band
    fits = sorted([s for s in silences
                   if lo <= s.dur <= hi
                   and not (exclude and round(s.mid, 3) in exclude)],
                  key=lambda x: x.mid)
    before = [s for s in fits if s.mid < est][-n:]
    after = [s for s in fits if s.mid >= est][:n]
    return before, after


def duration_rank_map(silences) -> dict:
    """Midpoint -> rank by length, 1 = longest silence in the file.

    An absolute rank says more than a percentile: on a file with 2238
    silences, p98 is still the 45th longest. '#45' tells you where you
    actually stand in the ordering.
    """
    order = sorted(silences, key=lambda x: -x.dur)
    return {round(s_.mid, 3): i + 1 for i, s_ in enumerate(order)}


def fmt_rank(rank, top_n: int) -> str:
    """'#3*' for a top-N silence, '#45' otherwise."""
    if not rank:
        return ""
    return f"#{rank}*" if rank <= top_n else f"#{rank}"


def top_rank_map(silences, n: int) -> dict:
    """Midpoint -> rank for the n longest silences in the file.

    The longest gaps are the strongest structural candidates: a producer
    inserts a deliberate pause at a chapter break, and nothing else in a
    recording is that long. Flagging them in the tables shows at a glance
    whether an expected position coincides with one.
    """
    longest = sorted(silences, key=lambda x: -x.dur)[:max(0, n)]
    return {round(s_.mid, 3): i + 1 for i, s_ in enumerate(longest)}


def silence_percentile(silences) -> dict:
    """Map each silence (by midpoint) to its duration percentile."""
    if not silences:
        return {}
    order = sorted(silences, key=lambda s: s.dur)
    n = len(order)
    return {round(s.mid, 3): int(100 * i / max(n - 1, 1))
            for i, s in enumerate(order)}


def print_major_silences(silences, dur, top: int, pct: dict):
    if not silences:
        return
    longest = sorted(silences, key=lambda s: -s.dur)[:top]
    print(f"\n--- the {len(longest)} longest "
          f"silence{'' if len(longest)==1 else 's'} in the file ---")
    print(f"  {'top':>4} {'position':>13} {'length':>8} {'pctile':>7}")
    for i, s in enumerate(longest, 1):
        print(f"  {'#' + str(i):>4} {hms(s.mid):>13} {s.dur:>7.2f}s "
              f"{'p' + str(pct.get(round(s.mid,3), 0)):>7}")
    print("\n  These are the strongest structural candidates in the recording. "
          "If none\n  of them coincides with an expected chapter position, the "
          "offset is wrong.")


def find_tape_period(silences, dur, min_min=18.0, max_min=70.0,
                     tol=90.0, long_pct=85, min_sides=3):
    """Infer the tape side length rather than assuming one.

    Side changes recur at a roughly fixed interval, but the interval depends
    on the cassette the publisher used - C90s (45 min a side) are at least as
    common for commercial audiobooks as C60s - and a side is never quite
    full, so the real period sits below the nominal figure. Vote on the
    period implied by every pair of long silences, then fit a phase.
    """
    if not silences:
        return None
    durs = sorted(x.dur for x in silences)
    cut = durs[min(len(durs) - 1, int(len(durs) * long_pct / 100))]
    longs = sorted([x for x in silences if x.dur >= cut], key=lambda x: x.mid)
    if len(longs) < 2:
        return None

    lo_p, hi_p = min_min * 60.0, max_min * 60.0

    # every pair implies a period, if the gap between them is n whole sides
    votes = []
    for i in range(len(longs)):
        for j in range(i + 1, len(longs)):
            gap = longs[j].mid - longs[i].mid
            for n in range(1, 12):
                p = gap / n
                if lo_p <= p <= hi_p:
                    votes.append(p)
    if not votes:
        return None

    best = None
    for p in sorted(set(round(v, 1) for v in votes)):
        n_sides = int(round(dur / p))
        if n_sides < min_sides:
            continue
        # phase: try aligning the grid to each long silence
        for seed in longs:
            k0 = round(seed.mid / p)
            phase = seed.mid - k0 * p
            if not (-p <= phase <= p):
                continue
            matched, errs, picks = 0, 0.0, []
            for k in range(1, n_sides):
                target = k * p + phase
                if not (0 < target < dur):
                    continue
                hit, d, best_sc = None, tol, -1.0
                for x in longs:
                    dd = abs(x.mid - target)
                    if dd > tol:
                        continue
                    sc = x.dur / (1.0 + dd / max(tol, 0.1))
                    if sc > best_sc:
                        hit, d, best_sc = x, dd, sc
                if hit is not None:
                    matched += 1
                    errs += d
                    picks.append((k, hit, hit.mid - target))
            expected = max(1, n_sides - 1)
            if matched < min_sides:
                continue
            # a real side change is markedly longer than an ordinary silence;
            # without this, any three points fit some period
            med = sorted(x.dur for x in silences)[len(silences) // 2]
            pick_mean = sum(h.dur for _, h, _ in picks) / len(picks)
            if med > 0 and pick_mean < med * 1.5:
                continue
            score = (matched / expected, -(errs / max(matched, 1)))
            if best is None or score > best["score"]:
                best = {"score": score, "period": p, "phase": phase,
                        "sides": n_sides, "matched": matched,
                        "expected": expected, "picks": picks,
                        "mean_err": errs / max(matched, 1),
                        "long_cut": cut, "n_long": len(longs)}
    return best


def print_tape_analysis(res, dur, top_rank, top_n):
    print(f"\n--- tape side detection ---")
    if not res:
        print("  no repeating interval found among the long silences.\n"
              "  Either this is not a cassette transfer, or the side gaps were\n"
              "  trimmed when the sides were joined.")
        return set()
    p = res["period"]
    print(f"  inferred side length : {hms(p)}  "
          f"({p/60:.1f} min)")
    if 15 * 60 <= p <= 70 * 60:
        nominal = min((60, 90, 120), key=lambda n: abs(p - n * 30))
        print(f"  closest standard tape: C{nominal} "
              f"({nominal // 2} min nominal per side, "
              f"{100 * p / (nominal * 30):.0f}% used)")
    print(f"  sides                : ~{res['sides']}  "
          f"(~{res['sides']/2:.0f} cassettes)")
    print(f"  grid points matched  : {res['matched']}/{res['expected']}, "
          f"mean error {res['mean_err']:.1f}s")
    print(f"  measured against the {res['n_long']} silences of "
          f"{res['long_cut']:.2f}s or longer\n")
    print(f"  {'side':>5} {'position':>13} {'length':>8} {'rank':>7} "
          f"{'off-grid':>9}")
    marked = set()
    for k, s, off in res["picks"]:
        marked.add(round(s.mid, 3))
        print(f"  {k:>5} {hms(s.mid):>13} {s.dur:>7.2f}s "
              f"{fmt_rank(top_rank.get(round(s.mid, 3)), top_n):>7} "
              f"{off:>+8.1f}s")
    if res["matched"] < res["expected"]:
        print(f"\n  {res['expected'] - res['matched']} grid point(s) had no long "
              f"silence - those joins were probably trimmed.")
    print("\n  These are side changes, not chapters. They are excluded from "
          "the break\n  signature and from snapping.")
    return marked


def major_silences(silences, major_pct: int) -> list:
    """Silences in the top duration percentile band, in time order."""
    if not silences:
        return []
    durs = sorted(x.dur for x in silences)
    cut = durs[min(len(durs) - 1, int(len(durs) * major_pct / 100))]
    return sorted([x for x in silences if x.dur >= cut], key=lambda x: x.mid)


def major_context(est: float, majors: list, n: int, pct: dict):
    """The n major silences before and the n after a position."""
    before = [s for s in majors if s.mid < est][-n:]
    after = [s for s in majors if s.mid >= est][:n]
    return before, after


def print_anchor_targets(nxt, majors, pct, n_context: int, major_pct: int,
                         top_rank=None, top_n: int = 15,
                         silences=None, band=None, exclude=None):
    """The suggested next anchors, each with the major silences around it."""
    print(f"\n--- measure these next ---\n")
    print("  Go to each expected time, find where the chapter really starts,")
    print("  then add it as another anchor.\n")
    for num, ch, est in nxt:
        print(f"    ch {num:<4} expected {hms(est):>13}   \"{ch.title[:44]}\"")
        if not majors:
            print("             (no major silences detected to navigate by)\n")
            continue
        if band and silences:
            before, after = signature_context(est, silences, band, n_context,
                                              exclude)
            source = "signature"
        else:
            before, after = major_context(est, majors, n_context, pct)
            source = "longest"
        if not before and not after:
            print(f"             no silence above p{major_pct} anywhere near this "
                  f"position\n")
            continue
        top_rank = top_rank or {}
        for s in before:
            print(f"        before {hms(s.mid):>13} {s.dur:>6.2f}s "
                  f"{'p' + str(pct.get(round(s.mid, 3), 0)):>5} "
                  f"{fmt_rank(top_rank.get(round(s.mid, 3)), top_n):>6} "
                  f"{s.mid - est:>+9.1f}s")
        print(f"        {'-' * 46}")
        for s in after:
            print(f"        after  {hms(s.mid):>13} {s.dur:>6.2f}s "
                  f"{'p' + str(pct.get(round(s.mid, 3), 0)):>5} "
                  f"{fmt_rank(top_rank.get(round(s.mid, 3)), top_n):>6} "
                  f"{s.mid - est:>+9.1f}s")
        print()
    if band:
        lo, hi, mean, _ = band
        print(f"  'before'/'after' are the nearest silences matching the "
              f"{mean:.2f}s chapter-break\n  signature ({lo:.2f}-{hi:.2f}s), "
              f"not simply the longest.\n"
              f"  The # is rank by length in the whole file; a star marks the "
              f"top {top_n}.\n"
              f"  One of them is usually the real break; jump to each and "
              f"listen.")
        return
    print(f"  'before'/'after' are the nearest silences above p{major_pct} in "
          f"length.\n"
          f"  The # is rank by length in the whole file; a star marks the top "
          f"{top_n}.\n"
          f"  One of them is usually the real break; jump to each and listen.")


# ==========================================================================
# mode: inspect
# ==========================================================================

def mode_inspect(args):
    chapters, desc = load_chapters(args)
    dur = chapterize.probe_duration(args.audio)
    silences = get_silences(args.audio, args, len(chapters))
    mids = [s.mid for s in silences]

    print(f"\nfile      : {os.path.basename(args.audio)}  ({hms(dur)})")
    print(f"chapters  : {desc}")
    published_end = chapters[-1].start + (chapters[-1].length or 0)
    print(f"published : ends at {hms(published_end)}  "
          f"(your file is {hms(dur - published_end)} different)")
    print(f"silences  : {len(silences)} at "
          f"noise={args.noise}dB min={args.min_silence}s")

    if silences:
        longest = sorted(silences, key=lambda s: -s.dur)[:5]
        print(f"            longest: " + ", ".join(
            f"{hms(s.mid)} ({s.dur:.1f}s)" for s in longest))

    if not silences:
        print("\nNo silences at all. Nothing can align against this file until "
              "detection finds something.\n"
              "Try:  --noise -30 --min-silence 1.0")
        return

    # Where do the chapters land? Anchors win over the automatic search.
    book = chapterize.AudnexBook(asin="", chapters=chapters)
    anchor_idx: set[int] = set()
    if args.anchor:
        pairs, labels, idx = parse_anchors(args.anchor, chapters)
        anchor_idx = {n for n in idx if n}
        mapfn, scale, offset, residuals = make_mapper(
            pairs, args.piecewise, args.offset_only)
        print(f"\nanchors   : {len(pairs)} supplied")
        for (p_, a_), lab in zip(pairs, labels):
            print(f"    {lab:<40} {hms(p_)} -> {hms(a_)}  ({a_-p_:+.2f}s)")
        mode_desc = ("piecewise between anchors" if args.piecewise and len(pairs) > 1
                     else f"scale {scale:.6f}, offset {offset:+.3f}s")
        print(f"fit       : from anchors - {mode_desc}")
        if len(pairs) >= 2 and not args.piecewise:
            print(f"            residuals: "
                  + ", ".join(f"{r:+.2f}s" for r in residuals))
        al = None
    else:
        al = chapterize.solve_alignment(book, silences, dur, "auto",
                                        args.tolerance, args.max_offset,
                                        allow_scale=True, verbose=False,
                                        hint=args.offset_hint,
                                        hint_window=args.offset_window)
        mapfn = al.apply
        print(f"\nauto-fit  : {al.method}")
        print(f"            offset {al.offset:+.3f}s"
              + (f", scale {al.scale:.6f}" if al.scale != 1.0 else "")
              + f"  -> {al.matched}/{al.total} matched")
        for n in al.notes:
            print(f"  ! {n}")

    # per-chapter table
    estimates = [0.0 if i == 0 else mapfn(ch.start)
                 for i, ch in enumerate(chapters)]
    actuals = {}
    if args.anchor:
        for (p_, a_), n in zip(pairs, idx):
            if n:
                actuals[n - 1] = a_
            else:
                for j, ch in enumerate(chapters):
                    if abs(ch.start - p_) < 0.51:
                        actuals[j] = a_
                        break

    usable = silences_usable(silences, len(chapters))
    if not usable:
        warn_unusable(silences, len(chapters), args.audio,
                      args.noise, args.min_silence)
    ranks = duration_rank_map(silences) if usable else {}
    tape_res = (find_tape_period(silences, dur, args.side_min, args.side_max,
                                 args.side_tol, args.side_pct)
                if (usable and args.tape) else None)
    side_mids = set()
    if tape_res is not None or args.tape:
        side_mids = print_tape_analysis(tape_res, dur, ranks, args.major)
    sig_hits = (anchor_signature(pairs, silences, args.sig_window, side_mids)
                if (usable and args.anchor) else [])
    band, band_why, band_detail = choose_band(
        sig_hits, args.sig_tol, chapters, estimates, silences, dur,
        args.tolerance, args.break_len)
    rows = render_chapter_table(
        chapters, estimates, actuals, mids, args.tolerance,
        anchor_idx, silences, dur,
        label="expected" if args.anchor else "predicted", usable=usable,
        top_rank=ranks, top_n=args.major, band=band,
        side_factor=args.side_factor, side_mids=side_mids)
    if usable and args.anchor:
        print_signature(sig_hits, band, band_why, band_detail, silences,
                        args.side_factor, ranks, args.major)

    claimed = {round(m, 3) for _, _, m, d in rows
               if m is not None and d <= args.tolerance}
    unmatched = [i + 1 for i, _, _, d in rows if d > args.tolerance]
    gaps = [d for _, _, _, d in rows]

    pct = silence_percentile(silences)
    if args.major and silences and usable:
        print_major_silences(silences, dur, args.major, pct)

    if args.anchor:
        nxt = suggest_next_anchors(chapters, estimates, pairs, dur, 3)
        if nxt:
            majors = (major_silences(silences, args.major_pct)
                      if usable else [])
            print_anchor_targets(nxt, majors, pct, args.context,
                                 args.major_pct, ranks, args.major,
                                 silences, band, side_mids)

    print()
    if unmatched:
        print(f"  {len(unmatched)} chapter(s) with no silence within "
              f"{args.tolerance}s: {', '.join(map(str, unmatched[:20]))}"
              + (" ..." if len(unmatched) > 20 else ""))

    # silences nobody claimed - these are the interesting ones
    orphans = [s for s in silences if round(s.mid, 3) not in claimed]
    orphans.sort(key=lambda s: -s.dur)
    if orphans:
        print(f"\n  {len(orphans)} silence(s) not claimed by any chapter. "
              f"Longest first - these are candidate real breaks:")
        for s in orphans[:args.show_orphans]:
            print(f"      {hms(s.mid):>13}  {s.dur:>5.1f}s long")
        if len(orphans) > args.show_orphans:
            print(f"      ... and {len(orphans) - args.show_orphans} more")

    if args.timeline:
        draw_timeline(chapters, al, silences, dur)

    # the punchline
    print("\n" + "-" * 78)
    matched_n = len(gaps) - len(unmatched)
    ratio = (matched_n / len(gaps)) if gaps else 0
    if ratio >= 0.7:
        print("Alignment looks sound. chapterize.py should handle this directly.")
    elif len(silences) < len(chapters) / 2:
        print(f"Only {len(silences)} silences for {len(chapters)} chapters. "
              "Detection is too strict:\n"
              "  try --noise -32 --min-silence 1.0 and inspect again.")
    elif len(silences) > len(chapters) * 6:
        print(f"{len(silences)} silences for {len(chapters)} chapters - this is "
              "catching sentence pauses.\n"
              "  try --noise -45 --min-silence 2.5 and inspect again.")
    else:
        print("Silence count looks reasonable but the offsets do not line up.\n"
              "That usually means a wrong edition, or a structural difference\n"
              "(a foreword present in one and not the other).\n\n"
              "Next step: pick two chapters from the table above, find where\n"
              "they really start (chapterdebug.py clips ... helps), then:\n\n"
              f"  chapterdebug.py anchor {args.audio} <source flags> \\\n"
              f"        --anchor 2=<time> --anchor {len(chapters)}=<time>")


def draw_timeline(chapters, al, silences, dur, width: int = 74):
    def col(t):
        return max(0, min(width - 1, int(t / max(dur, 1) * (width - 1))))

    pred_row = [" "] * width
    sil_row = [" "] * width
    for i, ch in enumerate(chapters):
        pred_row[col(0.0 if i == 0 else al.apply(ch.start))] = "|"
    for s in silences:
        c = col(s.mid)
        sil_row[c] = "#" if s.dur >= 3 else "."

    print(f"\n  0{' ' * (width - 10)}{hms(dur)[:8]}")
    print("  predicted  " + "".join(pred_row))
    print("  silences   " + "".join(sil_row))
    print("             (# = silence 3s or longer, . = shorter)")


# ==========================================================================
# mode: clips
# ==========================================================================

def mode_clips(args):
    chapters, desc = load_chapters(args)
    dur = chapterize.probe_duration(args.audio)
    silences = get_silences(args.audio, args, len(chapters))
    book = chapterize.AudnexBook(asin="", chapters=chapters)
    al = chapterize.solve_alignment(book, silences, dur, "auto",
                                    args.tolerance, args.max_offset,
                                    allow_scale=True, verbose=False,
                                    hint=args.offset_hint,
                                    hint_window=args.offset_window)

    os.makedirs(args.out, exist_ok=True)
    pad = args.pad

    if args.at:
        targets = [(0, parse_time(t)) for t in args.at]
    else:
        want = args.which or list(range(1, min(len(chapters), 6) + 1))
        targets = [(n, al.apply(chapters[n - 1].start)) for n in want
                   if 1 <= n <= len(chapters)]

    print(f"\nwriting {len(targets)} clip(s) of {pad*2:.0f}s to {args.out}/")
    for n, t in targets:
        start = max(0.0, t - pad)
        name = (f"at_{hms(t).replace(':', '-')}.mp3" if n == 0
                else f"ch{n:03d}_{hms(t).replace(':', '-')}.mp3")
        dest = os.path.join(args.out, name)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-ss", f"{start:.3f}", "-t", f"{pad*2:.3f}",
               "-i", args.audio, "-c", "copy", dest]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  failed at {hms(t)}: {r.stderr.strip()[-200:]}")
            continue
        label = "" if n == 0 else f"  (chapter {n}: {chapters[n-1].title[:34]})"
        print(f"  {name}{label}")

    print(f"""
Listen to each clip. The predicted break sits {pad:.0f}s in, so if the chapter
actually starts earlier or later you will hear it. Note the true position in
the whole file - if a clip starts at 1:20:15 and the real break is 4 seconds
into it, the true position is 1:20:19.

Then feed two or three of those back in:

  chapterdebug.py anchor {args.audio} <source flags> \\
        --anchor <chapter>=<true time> --anchor <chapter>=<true time>
""")


# ==========================================================================
# mode: anchor
# ==========================================================================

def fit_linear(pairs: list[tuple[float, float]]):
    """Least squares actual = scale * published + offset."""
    n = len(pairs)
    if n == 1:
        p, a = pairs[0]
        return 1.0, a - p
    sx = sum(p for p, _ in pairs)
    sy = sum(a for _, a in pairs)
    sxx = sum(p * p for p, _ in pairs)
    sxy = sum(p * a for p, a in pairs)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:
        die("anchors are at the same published time; they must differ")
    scale = (n * sxy - sx * sy) / denom
    offset = (sy - scale * sx) / n
    return scale, offset


def piecewise_map(t: float, pairs: list[tuple[float, float]]) -> float:
    """Linear interpolation between anchors, extrapolating past the ends."""
    pairs = sorted(pairs)
    if len(pairs) == 1:
        return t + (pairs[0][1] - pairs[0][0])
    if t <= pairs[0][0]:
        (p0, a0), (p1, a1) = pairs[0], pairs[1]
    elif t >= pairs[-1][0]:
        (p0, a0), (p1, a1) = pairs[-2], pairs[-1]
    else:
        for i in range(len(pairs) - 1):
            if pairs[i][0] <= t <= pairs[i + 1][0]:
                (p0, a0), (p1, a1) = pairs[i], pairs[i + 1]
                break
    span = p1 - p0
    if abs(span) < 1e-9:
        return a0
    return a0 + (t - p0) * (a1 - a0) / span


def mode_anchor(args):
    chapters, desc = load_chapters(args)
    dur = chapterize.probe_duration(args.audio)
    silences = get_silences(args.audio, args, len(chapters))
    mids = [s.mid for s in silences]

    # --- parse the anchors ---------------------------------------------
    if not args.anchor:
        die("need at least one --anchor")
    pairs, labels, anchor_nums = parse_anchors(args.anchor, chapters)
    anchor_idx = {n for n in anchor_nums if n}

    print(f"\nfile      : {os.path.basename(args.audio)}  ({hms(dur)})")
    print(f"chapters  : {desc}")
    print(f"silences  : {len(silences)}")
    print(f"\nanchors   :")
    for (p, a), lab in zip(pairs, labels):
        print(f"    {lab:<44} {hms(p)} -> {hms(a)}   ({a - p:+.2f}s)")

    if len(pairs) == 1:
        print("\n  one anchor fixes the offset only; scale is assumed 1.0.")
        print("  add a second anchor near the end of the book to catch drift.")

    # --- fit ------------------------------------------------------------
    scale, offset = fit_linear(pairs)
    print(f"\nlinear fit: actual = {scale:.6f} x published {offset:+.3f}s")
    if len(pairs) >= 2:
        drift_per_hour = (scale - 1.0) * 3600
        print(f"            drift {drift_per_hour:+.1f}s per hour of runtime")
    if not (0.9 <= scale <= 1.1):
        print(f"  ! scale {scale:.4f} is implausible. Check the anchor times - "
              "one is probably mistyped.")

    residuals = [a - (scale * p + offset) for p, a in pairs]

    # --- leverage check --------------------------------------------------
    # Scale is only as trustworthy as the distance between the anchors. Two
    # points close together extrapolate any small measurement error across
    # the whole book.
    if len(pairs) >= 2:
        span = max(p for p, _ in pairs) - min(p for p, _ in pairs)
        coverage = span / dur if dur else 0.0
        leverage = (dur / span) if span > 0 else float("inf")
        print(f"\n  anchors span {hms(span)} of a {hms(dur)} file "
              f"({coverage*100:.1f}% coverage, {leverage:.1f}x leverage)")

        if coverage < args.min_coverage:
            err = args.assumed_error
            print(f"""  ! That is too little to fit a scale. A {err:.0f}s error in either anchor
    becomes {err * leverage / 60:.1f} min of drift by the end of the book. The scale
    below is extrapolated {leverage:.0f}x beyond the evidence for it.

    Either add an anchor near the end of the book, or ignore the scale
    with --offset-only and accept a constant shift.""")

            # does the fitted scale survive a runtime sanity check?
            pub_end = max(p for p, _ in pairs)
            if chapters and chapters[-1].length is not None:
                published_end = chapters[-1].start + chapters[-1].length
            else:
                published_end = pub_end
            fitted_end = scale * published_end + offset
            flat_end = 1.0 * published_end + (
                pairs[0][1] - pairs[0][0])
            print(f"\n    sanity check - where the published end lands:")
            print(f"      with the fitted scale : {hms(fitted_end)}  "
                  f"({fitted_end - dur:+.0f}s vs the file)")
            print(f"      with scale forced 1.0 : {hms(flat_end)}  "
                  f"({flat_end - dur:+.0f}s vs the file)")
            if abs(flat_end - dur) < abs(fitted_end - dur):
                print(f"      -> scale 1.0 fits the runtime better. The fitted "
                      f"scale is\n"
                      f"         probably an artefact of the short anchor span.")

    if args.offset_only and len(pairs) >= 1:
        scale = 1.0
        offset = sum(a - p for p, a in pairs) / len(pairs)
        residuals = [a - (scale * p + offset) for p, a in pairs]
        print(f"\n  --offset-only: scale held at 1.0, offset {offset:+.3f}s "
              f"(mean of the anchors)")
        if len(pairs) >= 2:
            print(f"     per-anchor residuals: "
                  + ", ".join(f"{r:+.1f}s" for r in residuals))
            print(f"     a non-zero spread here is real drift you are choosing "
                  f"not to model")

    if len(pairs) >= 3:
        worst = max(abs(r) for r in residuals)
        print(f"            residuals: " + ", ".join(f"{r:+.2f}s" for r in residuals))
        if worst > args.tolerance:
            print(f"  ! worst residual {worst:.2f}s exceeds tolerance. A single "
                  "straight line does not\n"
                  "    fit these anchors, which means the file has a structural "
                  "difference\n"
                  "    (a section present in one version and not the other). "
                  "Re-run with --piecewise.")

    use_piecewise = args.piecewise
    if use_piecewise and len(pairs) < 2:
        print("  (--piecewise needs at least 2 anchors; using the plain offset)")
        use_piecewise = False
    if use_piecewise:
        print("            using piecewise interpolation between anchors")

    # --- place ------------------------------------------------------------
    usable = silences_usable(silences, len(chapters))
    if not usable:
        warn_unusable(silences, len(chapters), args.audio,
                      args.noise, args.min_silence)
    ranks = duration_rank_map(silences) if usable else {}
    tape_res = (find_tape_period(silences, dur, args.side_min, args.side_max,
                                 args.side_tol, args.side_pct)
                if (usable and args.tape) else None)
    side_mids = set()
    if tape_res is not None or args.tape:
        side_mids = print_tape_analysis(tape_res, dur, ranks, args.major)
    placed: list[chapterize.Placed] = []
    estimates: list[float] = []
    near: list[tuple[float | None, float]] = []
    for i, ch in enumerate(chapters):
        if i == 0:
            t = 0.0
        elif use_piecewise:
            t = piecewise_map(ch.start, pairs)
        else:
            t = scale * ch.start + offset
        estimates.append(t)

        m, d = (None, float("inf"))
        if i > 0 and mids:
            usable_mids = [x for x in mids if round(x, 3) not in side_mids] \
                if side_mids else mids
            m, d = nearest(t, usable_mids)
        near.append((m, d))

        snapped, delta = False, 0.0
        if not args.no_snap and i > 0 and mids:
            if m is not None and d <= args.snap_window:
                delta, t, snapped = m - t, m, True
        t = max(0.0, min(t, dur))
        placed.append(chapterize.Placed(ch.title, t, snapped, delta))

    cleaned = []
    for p in placed:
        if cleaned and p.start < cleaned[-1].start + 2.0:
            continue
        cleaned.append(p)
    if cleaned:
        cleaned[0].start = 0.0
    placed = cleaned

    snapped_n = sum(1 for p in placed if p.snapped)
    print(f"\nplaced    : {len(placed)} chapters, {snapped_n} snapped to a silence")
    if snapped_n:
        moves = [abs(p.delta) for p in placed if p.snapped]
        print(f"            snap distance: mean {sum(moves)/len(moves):.2f}s, "
              f"max {max(moves):.2f}s")

    actuals = {}
    for (p_, a_), n in zip(pairs, anchor_nums):
        if n:
            actuals[n - 1] = a_
        else:
            # anchored by published time; attach to whichever chapter matches
            for j, ch in enumerate(chapters):
                if abs(ch.start - p_) < 0.51:
                    actuals[j] = a_
                    break

    sig_hits = (anchor_signature(pairs, silences, args.sig_window, side_mids)
                if usable else [])
    band, band_why, band_detail = choose_band(
        sig_hits, args.sig_tol, chapters, estimates, silences, dur,
        args.tolerance, args.break_len)
    render_chapter_table(chapters, estimates, actuals, mids, args.tolerance,
                         anchor_idx, silences, dur, label="expected",
                         usable=usable, top_rank=ranks,
                         top_n=args.major, band=band,
                         side_factor=args.side_factor,
                         side_mids=side_mids)
    if usable:
        print_signature(sig_hits, band, band_why, band_detail, silences,
                        args.side_factor, ranks, args.major)

    if snapped_n:
        moved = [(i + 1, p.delta) for i, p in enumerate(placed) if p.snapped]
        print(f"\n  snapping moved {len(moved)} chapter(s); largest "
              f"{max(abs(d) for _, d in moved):.2f}s. Final positions are in "
              f"the written output.")

    pct = silence_percentile(silences)
    if args.major and silences and usable:
        print_major_silences(silences, dur, args.major, pct)

    nxt = suggest_next_anchors(chapters, estimates, pairs, dur, args.suggest)
    if nxt:
        majors = major_silences(silences, args.major_pct) if usable else []
        print_anchor_targets(nxt, majors, pct, args.context,
                             args.major_pct, ranks, args.major,
                             silences, band, side_mids)
        existing = " ".join(f"--anchor {spec}" for spec in args.anchor)
        addition = " ".join(f"--anchor {n}=<time>" for n, _, _ in nxt)
        print(f"\n  chapterdebug.py anchor {_q(args.audio)} <source flags> \\")
        print(f"        {existing} \\")
        print(f"        {addition}")
        if len(pairs) >= 2:
            span = max(p for p, _ in pairs) - min(p for p, _ in pairs)
            print(f"\n  Anchors currently span {hms(span)} of {hms(dur)}. "
                  f"Each new anchor\n  outside that range cuts the "
                  f"extrapolation error proportionally.")

    # --- outputs ----------------------------------------------------------
    book = chapterize.AudnexBook(asin="", chapters=[], title=args.book_title)
    meta = chapterize.ffmetadata(placed, dur, book, audio=args.audio)
    wrote = False

    if args.ffmetadata:
        with open(args.ffmetadata, "w") as f:
            f.write(meta)
        print(f"\nwrote {args.ffmetadata}")
        wrote = True
    if args.abs_json:
        with open(args.abs_json, "w") as f:
            f.write(chapterize.abs_json(placed, dur))
        print(f"wrote {args.abs_json}")
        wrote = True
    if args.embed is not None:
        dest = args.embed or (os.path.splitext(args.audio)[0] + ".chapters"
                              + os.path.splitext(args.audio)[1])
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
        chapterize.split(args.audio, placed, dur, args.split, args.verbose)
        wrote = True
    if args.save_anchors:
        with open(args.save_anchors, "w") as f:
            json.dump({
                "audio": os.path.abspath(args.audio),
                "anchors": [{"published_sec": p, "actual_sec": a, "label": l}
                            for (p, a), l in zip(pairs, labels)],
                "fit": {"scale": scale, "offset_sec": offset,
                        "piecewise": use_piecewise,
                        "residuals_sec": residuals},
                "chapters": [{"index": i, "title": p.title,
                              "start_sec": round(p.start, 3),
                              "snapped": p.snapped}
                             for i, p in enumerate(placed)],
            }, f, indent=2)
        print(f"wrote {args.save_anchors}")
        wrote = True

    if not wrote:
        print("\n(preview only - add --embed, --split, --abs-json or --ffmetadata)")


# ==========================================================================
# mode: tape
# ==========================================================================

VOL_RE = re.compile(r"(mean_volume|max_volume):\s*(-?[\d.]+) dB")


def measure_levels(path: str, start: float, dur: float) -> dict:
    cmd = ["ffmpeg", "-hide_banner", "-nostats",
           "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", path,
           "-af", "volumedetect", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    out = {}
    for m in VOL_RE.finditer(r.stderr):
        out[m.group(1)] = float(m.group(2))
    return out


def count_silences(path: str, start: float, dur: float,
                   noise: float, min_dur: float) -> int:
    cmd = ["ffmpeg", "-hide_banner", "-nostats",
           "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", path,
           "-af", f"silencedetect=noise={noise}dB:d={min_dur}", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return len(SIL_START_RE.findall(r.stderr))


SIL_START_RE = re.compile(r"silence_start:")


def find_periodic(silences, total: float, side_min: float, side_max: float,
                  max_k: int = 24):
    """Which subset of silences looks like regularly spaced tape-side changes?"""
    if not silences:
        return None
    by_len = sorted(silences, key=lambda s: -s.dur)
    best = None
    for k in range(1, min(max_k, len(by_len)) + 1):
        picks = sorted(by_len[:k], key=lambda s: s.mid)
        bounds = [0.0] + [s.mid for s in picks] + [total]
        segs = [bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1)]
        mean = sum(segs) / len(segs)
        if not (side_min <= mean <= side_max):
            continue
        var = sum((x - mean) ** 2 for x in segs) / len(segs)
        cv = (var ** 0.5) / mean if mean else 9e9
        shortest = min(s.dur for s in picks)
        if best is None or cv < best["cv"]:
            best = {"k": k, "cv": cv, "mean": mean, "picks": picks,
                    "segments": segs, "min_gap_dur": shortest}
    return best


def _q(s: str) -> str:
    return f"'{s}'" if " " in str(s) else str(s)


def mode_tape(args):
    dur = chapterize.probe_duration(args.audio)
    sample = min(args.sample_min * 60.0, dur)
    start = max(0.0, (dur - sample) * args.sample_at)

    print(f"\nfile      : {os.path.basename(args.audio)}  ({hms(dur)})")
    print(f"sampling  : {hms(sample)} starting at {hms(start)}")

    lv = measure_levels(args.audio, start, sample)
    if lv:
        print(f"levels    : mean {lv.get('mean_volume', float('nan')):.1f} dB, "
              f"peak {lv.get('max_volume', float('nan')):.1f} dB")
        print("            (mean is roughly the speech level; the noise floor "
              "sits well below it)")

    # --- threshold sweep -------------------------------------------------
    print(f"\nthreshold sweep on the sample (min silence {args.min_silence}s):")
    print(f"  {'noise':>8}  {'silences':>9}   per hour")
    thresholds = [-55, -50, -45, -40, -35, -30, -25, -20]
    counts = {}
    for db in thresholds:
        n = count_silences(args.audio, start, sample, db, args.min_silence)
        counts[db] = n
        per_hour = n * 3600.0 / max(sample, 1)
        bar = "#" * min(40, n)
        print(f"  {db:>6}dB  {n:>9}   {per_hour:>7.1f}  {bar}")

    rec = recommend_threshold(counts)
    if rec is None:
        print("""
  Nothing stable found. Either the file never goes quiet at any threshold
  (heavily compressed or noise-reduced transfer), or it is silent throughout.
  Inspect manually before going further.""")
        return

    print(f"\nrecommended: --noise {rec}")
    print(f"             counts hold steady from {rec}dB upward, which means "
          f"{rec}dB is\n"
          f"             just above the noise floor without reaching into speech.")

    # --- duration sweep at the chosen threshold ---------------------------
    # Threshold gets you above the hiss; duration is what separates chapter
    # gaps from ordinary sentence pauses, and matters more on dense files.
    print(f"\nminimum-silence sweep at --noise {rec}:")
    print(f"  {'min-silence':>12}  {'silences':>9}  {'over the file':>14}  "
          f"{'one every':>11}")
    durations = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0]
    dsweep = {}
    for d in durations:
        n = count_silences(args.audio, start, sample, float(rec), d)
        dsweep[d] = n
        projected = n * dur / max(sample, 1)
        every = (dur / projected) if projected else float("inf")
        every_s = f"{every/60:.1f} min" if every >= 120 else f"{every:.0f} s"
        print(f"  {d:>10.1f}s  {n:>9}  {projected:>14.0f}  {every_s:>11}")

    best_d = args.min_silence
    if args.expect_chapters:
        target = args.expect_chapters
        scored = []
        for d, n in dsweep.items():
            projected = n * dur / max(sample, 1)
            if projected < target * 0.5:
                continue
            scored.append((abs(projected - target * 1.5), d, projected))
        if scored:
            scored.sort()
            _, best_d, proj = scored[0]
            print(f"\n  for {target} chapters, --min-silence {best_d} gives about "
                  f"{proj:.0f} silences")
            print(f"  (aiming a little above the chapter count leaves room for "
                  f"gaps the scan misses,\n"
                  f"   without burying the real breaks in sentence pauses)")
        else:
            print(f"\n  ! no duration yields even half of {target} silences. "
                  f"The gaps have been trimmed\n"
                  f"    out of this transfer - see the note below.")

    args.min_silence = best_d

    # --- full scan at the recommendation ---------------------------------
    print(f"\nfull scan at --noise {rec} --min-silence {args.min_silence} ...")
    cache = False if args.no_silence_cache else args.silence_cache
    silences = chapterize.detect_silences(args.audio, float(rec),
                                          args.min_silence, args.verbose, cache)
    print(f"  {len(silences)} silences over the whole file")
    if not silences:
        return

    durs = sorted((s.dur for s in silences), reverse=True)
    print(f"  longest {durs[0]:.1f}s, median {durs[len(durs)//2]:.1f}s")

    per_hour = len(silences) / max(dur / 3600, 1e-9)
    degapped = durs[0] < args.degap_threshold
    if degapped:
        print(f"""
  ! The longest silence in {hms(dur)} is only {durs[0]:.1f}s. An untouched
    cassette transfer keeps 5-15s of dead air at every side change, so those
    gaps have been trimmed out - either by the person who joined the sides,
    or by a noise gate during the transfer.

    Consequences:
      - Side detection cannot work. There is nothing left to detect.
      - The timeline now has a small step at every join. Roughly even
        trimming looks like a linear time scale, which the aligner can
        absorb, but expect residuals as large as one trimmed gap.
      - Raise --tolerance to 8-10s so those residuals still count as
        matches, rather than rejecting the correct edition.""")
    if per_hour > 100:
        print(f"""
  ! {per_hour:.0f} silences per hour is sentence-pause density, not chapter
    density. At this rate a wrong chapter offset matches a silence by
    coincidence roughly a third of the time, so match counts become noise.
    Use the duration sweep above to push this down before trusting any
    alignment score.""")

    # --- side-break hunt --------------------------------------------------
    per = find_periodic(silences, dur, args.side_min * 60.0, args.side_max * 60.0)
    if per is not None and per["cv"] > args.max_irregularity:
        print("\n--- tape side detection ---")
        print(f"  Best candidate spacing varies by {per['cv']*100:.0f}%, well "
              f"above the {args.max_irregularity*100:.0f}% ceiling.")
        print("  Long silences here are not evenly spaced, so they are not tape\n"
              "  side changes. This looks like a digital source, or sides that\n"
              "  were trimmed before being joined.")
        per = "rejected"
    else:
        print("\n--- tape side detection ---")
    if per == "rejected":
        per = None
    elif per is None:
        print(f"  No regularly spaced long silences between {args.side_min} and "
              f"{args.side_max} minutes apart.\n"
              f"  Either this is not a cassette transfer, or the sides were "
              f"trimmed before joining.\n"
              f"  Widen the window with --side-min / --side-max if you know the "
              f"tape length.")
    else:
        n_sides = per["k"] + 1
        print(f"  {n_sides} side(s) of about {hms(per['mean'])} each")
        print(f"  spacing regularity: {per['cv']*100:.1f}% variation "
              + ("(strong - these are almost certainly side changes)"
                 if per["cv"] < 0.10 else
                 "(weak - treat with suspicion)"))
        print(f"  shortest of the chosen gaps: {per['min_gap_dur']:.1f}s")
        print(f"\n  {'#':>3}  {'position':>13}  {'gap':>7}  {'side length':>13}")
        for i, s in enumerate(per["picks"], 1):
            print(f"  {i:>3}  {hms(s.mid):>13}  {s.dur:>6.1f}s  "
                  f"{hms(per['segments'][i-1]):>13}")
        print(f"  {'end':>3}  {hms(dur):>13}  {'':>7}  "
              f"{hms(per['segments'][-1]):>13}")

        if args.write_sides:
            with open(args.write_sides, "w") as f:
                json.dump({
                    "audio": os.path.abspath(args.audio),
                    "duration_sec": round(dur, 3),
                    "recommended_noise_db": rec,
                    "min_silence_sec": args.min_silence,
                    "sides": n_sides,
                    "mean_side_sec": round(per["mean"], 3),
                    "regularity_cv": round(per["cv"], 5),
                    "breaks_sec": [round(s.mid, 3) for s in per["picks"]],
                }, f, indent=2)
            print(f"\n  wrote {args.write_sides}")

        print(f"""
  Side breaks are NOT chapter breaks. They fall wherever the tape ran out,
  often mid-sentence. Do not let the aligner snap chapters onto them.

  What to do with them:
    - As anchors. Each side was probably digitized separately, so each can
      carry its own speed error. Anchor one chapter per side and use
      --piecewise rather than one straight line.
    - As a split, if no chapter data fits at all:
          chapterdebug.py anchor {args.audio} ... --split sides/
    - Exclude them from snapping by keeping --snap-window below
      {max(5, int(per['mean'] / 60)):d}s so a chapter cannot reach a side change.""")

    tol = 10.0 if degapped else 4.0
    print(f"""
--- suggested next step ---

  chapterdebug.py inspect {_q(args.audio)} --asin <ASIN> \\
        --noise {rec} --min-silence {args.min_silence} --tolerance {tol} --timeline

  Pass the SAME --noise and --min-silence to every other tool, including
  find_asin.py and abchapters.py. They each default to -40dB, and on this
  file that finds almost nothing, which makes every match score meaningless.

  If the runtime is far shorter than the Audible edition, the tape is an
  abridgement and no published chapter list will ever line up. A runtime
  within a few minutes means the content matches and it is only a tuning
  problem.""")


def recommend_threshold(counts: dict) -> int | None:
    """Pick the lowest threshold inside the stable plateau."""
    ths = sorted(counts)
    nonzero = [t for t in ths if counts[t] > 0]
    if not nonzero:
        return None
    # walk upward; the plateau is where the count stops changing much
    best_run, run = [], []
    for t in nonzero:
        if not run:
            run = [t]
            continue
        prev = counts[run[-1]]
        cur = counts[t]
        if prev and abs(cur - prev) / max(prev, 1) <= 0.25:
            run.append(t)
        else:
            if len(run) > len(best_run):
                best_run = run
            run = [t]
    if len(run) > len(best_run):
        best_run = run
    if not best_run:
        return nonzero[0]
    return best_run[0]


# ==========================================================================
# mode: profile
# ==========================================================================

def mode_profile(args):
    chapters, desc = load_chapters(args)
    dur = chapterize.probe_duration(args.audio)
    silences = get_silences(args.audio, args, len(chapters))

    print(f"\nfile      : {os.path.basename(args.audio)}  ({hms(dur)})")
    print(f"chapters  : {desc}")
    print(f"silences  : {len(silences)} at noise={args.noise}dB "
          f"min={args.min_silence}s")
    if len(silences) < 2:
        print("\nToo few silences to profile. Loosen detection first:\n"
              f"  chapterdebug.py tape {_q(args.audio)} "
              f"--expect-chapters {len(chapters)}")
        return

    durs = sorted(s.dur for s in silences)

    # --- duration histogram ----------------------------------------------
    print("\nsilence duration profile:")
    lo, hi = durs[0], durs[-1]
    nbins = min(24, max(8, int((hi - lo) / 0.25) + 1))
    width = (hi - lo) / nbins if hi > lo else 1.0
    bins = [0] * nbins
    for d in durs:
        bins[min(nbins - 1, int((d - lo) / width))] += 1
    peak = max(bins) or 1
    for i, c in enumerate(bins):
        a, b = lo + i * width, lo + (i + 1) * width
        bar = "#" * int(40 * c / peak)
        print(f"  {a:5.2f}-{b:5.2f}s  {c:>5}  {bar}")
    print(f"\n  median {durs[len(durs)//2]:.2f}s, "
          f"90th pct {durs[int(len(durs)*0.9)]:.2f}s, max {hi:.2f}s")

    # --- sweep the band ---------------------------------------------------
    # One scan, many slices. Each candidate threshold keeps the silences at
    # least that long, then the offset search runs against that subset only.
    print(f"\nband sweep - alignment quality when matching only against "
          f"silences >= each length:")
    print(f"  {'>= dur':>8} {'silences':>9} {'matched':>9} {'by chance':>10} "
          f"{'enrich':>8} {'z':>7} {'offset':>11} {'err':>7}")

    thresholds = sorted({round(d, 2) for d in durs})
    if len(thresholds) > args.max_bands:
        step = len(thresholds) / args.max_bands
        thresholds = [thresholds[int(i * step)] for i in range(args.max_bands)]

    book = chapterize.AudnexBook(asin="", chapters=chapters)
    results = []
    for th in thresholds:
        subset = chapterize.filter_by_duration(silences, th, args.break_max)
        if len(subset) < 2:
            continue
        al = chapterize.solve_alignment(
            book, subset, dur, "auto", args.tolerance, args.max_offset,
            allow_scale=not args.no_scale, verbose=False,
            hint=args.offset_hint, hint_window=args.offset_window)
        st = chapterize.match_stats(al.matched, al.total, len(subset),
                                    dur, args.tolerance)
        results.append((th, len(subset), al, st))
        print(f"  {th:>7.2f}s {len(subset):>9} {al.matched:>4}/{al.total:<4} "
              f"{st['expected']:>10.1f} {st['enrichment']:>7.1f}x "
              f"{st['z']:>7.1f} {al.offset:>+10.2f}s "
              f"{al.mean_err if al.matched else float('nan'):>6.2f}s")

    if not results:
        print("\n  no band had enough silences to test")
        return

    # Rank by significance, not raw match count. A band that keeps everything
    # will always match more chapters; the question is whether it beats chance.
    usable = [r for r in results if r[2].matched >= args.min_matches]
    pool = usable or results
    pool.sort(key=lambda r: (-r[3]["z"], r[2].mean_err))
    best_th, best_n, best_al, best_st = pool[0]

    print(f"\nbest band : silences >= {best_th:.2f}s  ({best_n} of "
          f"{len(silences)} kept)")
    print(f"            {best_al.matched}/{best_al.total} chapters matched, "
          f"chance would give {best_st['expected']:.1f}")
    print(f"            {best_st['enrichment']:.1f}x enrichment, "
          f"z={best_st['z']:.1f}, offset {best_al.offset:+.2f}s, "
          f"mean error {best_al.mean_err:.2f}s")

    if best_st["z"] < 3:
        print("""
  ! Even the best band is not clearly better than chance. Nothing here
    distinguishes chapter breaks from ordinary pauses. Either the edition
    is wrong, or this transfer has no consistent break length - which is
    common when silences were trimmed or a noise gate was used.""")
    else:
        strength = ("decisive" if best_st["z"] > 8 else
                    "solid" if best_st["z"] > 5 else "suggestive")
        print(f"            confidence: {strength}")

    # is there a distinct break population?
    kept = [s.dur for s in silences if s.dur >= best_th]
    if kept:
        spread = max(kept) - min(kept)
        print(f"\n  kept silences run {min(kept):.2f}-{max(kept):.2f}s "
              f"(spread {spread:.2f}s)")
        if spread < 1.0 and len(kept) >= 3:
            print("  A tight spread means these were inserted deliberately by the\n"
                  "  producer - a real chapter-break population, not the tail of\n"
                  "  the pause distribution.")

    print(f"""
--- use it ---

  chapterize.py {_q(args.audio)} --asin <ASIN> \\
        --noise {args.noise} --min-silence {args.min_silence} \\
        --break-min {best_th:.2f} --tolerance {args.tolerance:.0f}

  --break-min filters the cached scan rather than re-running detection, so
  trying other bands costs nothing.""")


# ==========================================================================

def add_source_args(ap):
    g = ap.add_argument_group("chapter source")
    g.add_argument("--asin")
    g.add_argument("--region", default="us")
    g.add_argument("--chapters-json")
    g.add_argument("--lv-id")
    g.add_argument("--lv-sections-json")
    g.add_argument("--save-chapters", metavar="FILE",
                   help="save the fetched chapter JSON for offline reuse")


def add_detect_args(ap):
    g = ap.add_argument_group("silence detection")
    g.add_argument("--noise", type=float, default=None,
                   help="silence threshold in dB. Omit to probe the "
                        "file and pick one automatically.")
    g.add_argument("--min-silence", type=float, default=1.5)
    g.add_argument("--tolerance", type=float, default=4.0)
    g.add_argument("--max-offset", type=float, default=120.0)
    g.add_argument("--offset-hint", type=float,
                   help="roughly where the offset is, in seconds; "
                        "confines the search around it")
    g.add_argument("--offset-window", type=float, default=15.0)
    g.add_argument("--silence-cache", default=None)
    g.add_argument("--no-silence-cache", action="store_true")


def main():
    ap = argparse.ArgumentParser(
        description="Diagnose and hand-correct audiobook chapter alignment.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    p = sub.add_parser("inspect", help="show predicted vs actual silences")
    p.add_argument("audio")
    add_source_args(p)
    add_detect_args(p)
    p.add_argument("--timeline", action="store_true",
                   help="draw an ASCII timeline of both")
    p.add_argument("--anchor", action="append", default=[], metavar="CH=TIME",
                   help="estimate every chapter from these known positions "
                        "instead of searching. Repeatable.")
    p.add_argument("--piecewise", action="store_true",
                   help="interpolate between anchors rather than one line")
    p.add_argument("--offset-only", action="store_true",
                   help="hold the scale at 1.0 when fitting anchors")
    p.add_argument("--tape", action="store_true",
                   help="treat this as a cassette transfer: infer the "
                        "side length from repeating long silences and "
                        "exclude those from chapter matching")
    p.add_argument("--side-min", type=float, default=18.0,
                   help="shortest side length to consider, minutes "
                        "(default 18)")
    p.add_argument("--side-max", type=float, default=70.0,
                   help="longest side length to consider, minutes "
                        "(default 70)")
    p.add_argument("--side-tol", type=float, default=90.0,
                   help="how far off the grid a side break may sit, "
                        "seconds (default 90)")
    p.add_argument("--side-pct", type=int, default=85,
                   help="only silences above this length percentile are "
                        "considered side-break candidates (default 85)")
    p.add_argument("--break-len", type=float, metavar="SECONDS",
                   help="force the chapter-break length instead of "
                        "deriving it from the anchors")
    p.add_argument("--sig-window", type=float, default=6.0,
                   help="how close a silence must be to an anchor to "
                        "count as that chapter's break (default 6s)")
    p.add_argument("--sig-tol", type=float, default=0.4,
                   help="half-width of the chapter-break duration band "
                        "(default 0.4s, widened by the spread)")
    p.add_argument("--side-factor", type=float, default=1.8,
                   help="a silence this many times the signature length "
                        "is flagged as a tape side change (default 1.8)")
    p.add_argument("--major-pct", type=int, default=90,
                   help="a silence counts as major if its length is in "
                        "the top (100-N) percent (default 90)")
    p.add_argument("--context", type=int, default=3,
                   help="major silences to show either side of each "
                        "suggested anchor (default 3)")
    p.add_argument("--major", type=int, default=15,
                   help="how many of the longest silences to list "
                        "(default 15, 0 to skip)")
    p.add_argument("--show-orphans", type=int, default=15,
                   help="how many unclaimed silences to list (default 15)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=mode_inspect)

    p = sub.add_parser("clips", help="cut audio around predicted breaks")
    p.add_argument("audio")
    add_source_args(p)
    add_detect_args(p)
    p.add_argument("--out", default="clips", help="output directory")
    p.add_argument("--pad", type=float, default=15.0,
                   help="seconds either side of the break (default 15)")
    p.add_argument("--which", type=int, nargs="*",
                   help="chapter numbers to clip (default: first 6)")
    p.add_argument("--at", nargs="*",
                   help="clip at these absolute times instead")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=mode_clips)

    p = sub.add_parser("anchor", help="solve the timeline from known positions")
    p.add_argument("audio")
    add_source_args(p)
    add_detect_args(p)
    p.add_argument("--anchor", action="append", default=[], metavar="CH=TIME",
                   help="a chapter number and its true position, e.g. 2=0:04:31. "
                        "Use @PUB=TIME to anchor a published time directly. "
                        "Repeatable; two is usually enough.")
    p.add_argument("--offset-only", action="store_true",
                   help="hold the scale at 1.0 and fit only a constant "
                        "shift. Use when the anchors are close together.")
    p.add_argument("--min-coverage", type=float, default=0.5,
                   help="warn if the anchors span less than this fraction "
                        "of the file (default 0.5)")
    p.add_argument("--assumed-error", type=float, default=2.0,
                   help="anchor precision in seconds, used to show what "
                        "the leverage costs you (default 2)")
    p.add_argument("--piecewise", action="store_true",
                   help="interpolate between anchors instead of one straight line")
    p.add_argument("--no-snap", action="store_true")
    p.add_argument("--snap-window", type=float, default=15.0)
    p.add_argument("--book-title", help="title to write into the metadata")
    p.add_argument("--ffmetadata", metavar="FILE")
    p.add_argument("--abs-json", metavar="FILE")
    p.add_argument("--embed", nargs="?", const="", metavar="DEST")
    p.add_argument("--split", metavar="DIR")
    p.add_argument("--tape", action="store_true",
                   help="treat this as a cassette transfer: infer the "
                        "side length from repeating long silences and "
                        "exclude those from chapter matching")
    p.add_argument("--side-min", type=float, default=18.0,
                   help="shortest side length to consider, minutes "
                        "(default 18)")
    p.add_argument("--side-max", type=float, default=70.0,
                   help="longest side length to consider, minutes "
                        "(default 70)")
    p.add_argument("--side-tol", type=float, default=90.0,
                   help="how far off the grid a side break may sit, "
                        "seconds (default 90)")
    p.add_argument("--side-pct", type=int, default=85,
                   help="only silences above this length percentile are "
                        "considered side-break candidates (default 85)")
    p.add_argument("--break-len", type=float, metavar="SECONDS",
                   help="force the chapter-break length instead of "
                        "deriving it from the anchors")
    p.add_argument("--sig-window", type=float, default=6.0,
                   help="how close a silence must be to an anchor to "
                        "count as that chapter's break (default 6s)")
    p.add_argument("--sig-tol", type=float, default=0.4,
                   help="half-width of the chapter-break duration band "
                        "(default 0.4s, widened by the spread)")
    p.add_argument("--side-factor", type=float, default=1.8,
                   help="a silence this many times the signature length "
                        "is flagged as a tape side change (default 1.8)")
    p.add_argument("--major-pct", type=int, default=90,
                   help="a silence counts as major if its length is in "
                        "the top (100-N) percent (default 90)")
    p.add_argument("--context", type=int, default=3,
                   help="major silences to show either side of each "
                        "suggested anchor (default 3)")
    p.add_argument("--major", type=int, default=15,
                   help="how many of the longest silences to list "
                        "(default 15, 0 to skip)")
    p.add_argument("--suggest", type=int, default=3,
                   help="how many next anchors to propose (default 3)")
    p.add_argument("--save-anchors", metavar="FILE",
                   help="save the anchors and fit as JSON")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=mode_anchor)

    p = sub.add_parser("tape", help="analyse a cassette transfer: noise floor, "
                                    "threshold sweep, tape side detection")
    p.add_argument("audio")
    p.add_argument("--min-silence", type=float, default=1.5)
    p.add_argument("--sample-min", type=float, default=20.0,
                   help="minutes of audio to sweep thresholds over (default 20)")
    p.add_argument("--sample-at", type=float, default=0.4,
                   help="where to take the sample, 0.0=start 1.0=end (default 0.4)")
    p.add_argument("--side-min", type=float, default=20.0,
                   help="shortest plausible tape side, minutes (default 20)")
    p.add_argument("--side-max", type=float, default=65.0,
                   help="longest plausible tape side, minutes (default 65)")
    p.add_argument("--expect-chapters", type=int,
                   help="how many chapters the book has; picks a --min-silence "
                        "that yields a workable silence count")
    p.add_argument("--degap-threshold", type=float, default=6.0,
                   help="warn if the longest silence is under this many seconds "
                        "(default 6), meaning side gaps were trimmed")
    p.add_argument("--max-irregularity", type=float, default=0.20,
                   help="reject a side pattern whose spacing varies more "
                        "than this fraction (default 0.20)")
    p.add_argument("--write-sides", metavar="FILE",
                   help="save the detected side breaks as JSON")
    p.add_argument("--silence-cache", default=None)
    p.add_argument("--no-silence-cache", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=mode_tape)

    p = sub.add_parser("profile", help="profile silence durations and find the "
                                       "band that best separates chapter breaks")
    p.add_argument("audio")
    add_source_args(p)
    add_detect_args(p)
    p.add_argument("--break-max", type=float,
                   help="ignore silences longer than this (e.g. to exclude "
                        "tape side changes)")
    p.add_argument("--max-bands", type=int, default=30,
                   help="how many duration thresholds to test (default 30)")
    p.add_argument("--min-matches", type=int, default=3,
                   help="a band must match at least this many chapters to be "
                        "considered (default 3)")
    p.add_argument("--no-scale", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=mode_profile)

    args = ap.parse_args()
    if not os.path.isfile(args.audio):
        die(f"no such file: {args.audio}")
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
