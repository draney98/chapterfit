#!/usr/bin/env python3
"""
consensus_chapters.py - statistical sequence matcher for audiobook chapters.

Unlike the Audible and LibriVox routes, which each snap published starts onto
silences independently, this treats chapter placement as a global sequence
alignment:

  1. Load complete candidate timelines from Audible and/or LibriVox.
  2. Detect silences and estimate which ones look like deliberate breaks.
  3. For each complete timeline, generate affine timing hypotheses and run a
     monotonic dynamic-programming matcher over all chapters at once.
  4. Soft-score the competing source's boundaries as supporting evidence when
     chapter counts differ, without merging incompatible structures.
  5. Pick whichever complete timeline scores best.

Examples
--------
  consensus_chapters.py book.mp3 --title "Ubik" --author "Philip K. Dick"
  consensus_chapters.py book.mp3 --asin B002V1OF70 --lv-id 1234 --embed
  consensus_chapters.py book.mp3 --chapters-json aud.json --sections-json lv.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, field

from chaptertools import audnex
from chaptertools.silence import chapterize
from chaptertools.sources import find_asin
from chaptertools.sources import librivox_chapters as lv

USER_AGENT = "consensus_chapters.py/1.0"


# ==========================================================================
# models
# ==========================================================================

@dataclass
class SourceChapter:
    title: str
    start: float
    length: float


@dataclass
class SourceTimeline:
    """One complete published chapter structure. Never average these."""
    source: str                 # "audible" | "librivox" | "json"
    identity: str               # ASIN, LV id, or file name
    title: str | None
    author: str | None
    chapters: list[SourceChapter]
    runtime: float | None = None
    prior: float = 1.0          # soft preference for this source family

    @property
    def published_total(self) -> float:
        if self.runtime is not None:
            return self.runtime
        if not self.chapters:
            return 0.0
        last = self.chapters[-1]
        return last.start + max(last.length, 0.0)


@dataclass
class SilencePrior:
    """Empirical P(chapter break | silence duration) for this recording."""
    pause_mean_log: float
    break_mean_log: float
    pause_std: float
    break_std: float
    break_weight: float

    def break_score(self, duration: float) -> float:
        """Higher is better.  Approx log-odds of being a deliberate break."""
        if duration <= 0:
            return -8.0
        x = math.log(max(duration, 1e-3))
        # Guarded two-component comparison on log-duration.
        z_pause = (x - self.pause_mean_log) / max(self.pause_std, 0.15)
        z_break = (x - self.break_mean_log) / max(self.break_std, 0.15)
        # Soft preference for longer silences even when the mixture is weak.
        length_bonus = min(2.5, max(0.0, (duration - 1.5) / 3.0))
        return -0.5 * z_break * z_break + 0.35 * z_pause * z_pause + length_bonus


@dataclass
class TimingModel:
    offset: float = 0.0
    scale: float = 1.0
    method: str = "identity"

    def apply(self, t: float) -> float:
        return t * self.scale + self.offset


@dataclass
class BoundDecision:
    index: int
    title: str
    published_start: float
    expected_length: float
    predicted: float
    placed: float
    silence_mid: float | None
    silence_dur: float | None
    timing_err: float
    length_err: float | None
    confidence: float
    snapped: bool
    virtual: bool
    support_from_other: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class MatchResult:
    timeline: SourceTimeline
    model: TimingModel
    decisions: list[BoundDecision]
    score: float
    components: dict
    matched: int
    total: int
    mean_timing_err: float
    mean_length_err: float | None
    confident_boundaries: int

    @property
    def match_ratio(self) -> float:
        return self.matched / self.total if self.total else 0.0


# ==========================================================================
# helpers
# ==========================================================================

def die(msg: str, code: int = 1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def hms(sec: float | None) -> str:
    if sec is None:
        return "?"
    neg, sec = sec < 0, abs(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    out = f"{int(h)}:{int(m):02d}:{s:06.3f}"
    return ("-" + out) if neg else out


def huber(x: float, delta: float = 4.0) -> float:
    ax = abs(x)
    if ax <= delta:
        return 0.5 * ax * ax
    return delta * (ax - 0.5 * delta)


def chapter_lengths(chapters: list[SourceChapter]) -> list[float]:
    out = []
    for i, ch in enumerate(chapters):
        if i + 1 < len(chapters):
            delta = chapters[i + 1].start - ch.start
            if ch.length and ch.length > 0:
                # Prefer published length unless nested metadata overlaps.
                if abs(ch.length - delta) <= max(5.0, delta * 0.01):
                    out.append(ch.length)
                else:
                    out.append(delta)
            else:
                out.append(delta)
        else:
            out.append(ch.length if ch.length and ch.length > 0 else 0.0)
    return out


def book_from_timeline(tl: SourceTimeline) -> chapterize.AudnexBook:
    return chapterize.AudnexBook(
        asin=tl.identity if tl.source == "audible" else "",
        chapters=[chapterize.Chapter(c.title, c.start, c.length)
                  for c in tl.chapters],
        runtime=tl.runtime,
        title=tl.title,
        author=tl.author,
    )


# ==========================================================================
# silence prior
# ==========================================================================

def fit_silence_prior(silences: list[chapterize.Silence]) -> SilencePrior:
    """Guarded two-component model on log silence duration."""
    durs = sorted(s.dur for s in silences if s.dur > 0)
    if not durs:
        return SilencePrior(-1.0, 1.0, 0.4, 0.5, 0.2)

    logs = [math.log(d) for d in durs]
    # Seed: short = below median, long = top quartile-ish.
    med = logs[len(logs) // 2]
    hi = logs[int(len(logs) * 0.75)]
    pause = [x for x in logs if x <= med]
    brk = [x for x in logs if x >= hi] or logs[-max(1, len(logs) // 10):]
    if not pause:
        pause = logs[:max(1, len(logs) // 2)]

    def mean(xs):
        return sum(xs) / len(xs)

    def std(xs, m):
        if len(xs) < 2:
            return 0.35
        return max(0.15, (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5)

    pm, bm = mean(pause), mean(brk)
    # Ensure the break component sits above the pause component.
    if bm <= pm:
        bm = pm + 0.4
    return SilencePrior(
        pause_mean_log=pm,
        break_mean_log=bm,
        pause_std=std(pause, pm),
        break_std=std(brk, bm),
        break_weight=min(0.45, max(0.08, len(brk) / max(len(logs), 1))),
    )


# ==========================================================================
# hypothesis generation
# ==========================================================================

def seed_timing(tl: SourceTimeline, silences: list[chapterize.Silence],
                local_dur: float, tol: float, max_offset: float,
                allow_scale: bool, hint: float | None,
                hint_window: float) -> TimingModel:
    book = book_from_timeline(tl)
    al = chapterize.solve_alignment(
        book, silences, local_dur, "auto", tol, max_offset,
        allow_scale=allow_scale, verbose=False,
        hint=hint, hint_window=hint_window)
    return TimingModel(al.offset, al.scale, al.method)


def extra_hypotheses(tl: SourceTimeline, sil_mids: list[float],
                     local_dur: float, max_offset: float,
                     allow_scale: bool) -> list[TimingModel]:
    """Additional affine seeds from length gaps and end anchors."""
    out = [TimingModel(0.0, 1.0, "identity")]
    published = tl.published_total
    if published > 0 and local_dur > 0:
        out.append(TimingModel(0.0, local_dur / published, "runtime-scale"))
        out.append(TimingModel(local_dur - published, 1.0, "end-align"))

    lengths = chapter_lengths(tl.chapters)
    mids = sorted(sil_mids)
    if len(mids) >= 2 and len(tl.chapters) >= 3:
        # Sample a few interior chapters and vote offsets from matching gaps.
        votes = []
        step = max(1, len(tl.chapters) // 8)
        for i in range(1, len(tl.chapters) - 1, step):
            expected = lengths[i]
            if expected < 20:
                continue
            for left in mids[::max(1, len(mids) // 40)]:
                target = left + expected
                # nearest mid to target
                best = min(mids, key=lambda m: abs(m - target))
                if abs(best - target) > max(6.0, expected * 0.01):
                    continue
                off = ((left - tl.chapters[i].start) +
                       (best - tl.chapters[i + 1].start)) / 2.0
                if abs(off) <= max_offset:
                    votes.append(off)
        if votes:
            votes.sort()
            mid = votes[len(votes) // 2]
            out.append(TimingModel(mid, 1.0, "length-median"))
            if allow_scale and published > 0:
                out.append(TimingModel(mid, local_dur / published,
                                       "length-median+scale"))
    # Deduplicate near-identical seeds.
    uniq = []
    for m in out:
        if any(abs(m.offset - u.offset) < 0.25 and abs(m.scale - u.scale) < 1e-4
               for u in uniq):
            continue
        if not (0.95 <= m.scale <= 1.05) and m.method != "runtime-scale":
            # Keep runtime-scale even when large; reject wild others.
            if not (0.90 <= m.scale <= 1.10):
                continue
        uniq.append(m)
    return uniq


# ==========================================================================
# dynamic programming matcher
# ==========================================================================

def _support_score(predicted: float, support_times: list[float],
                   window: float) -> float:
    if not support_times:
        return 0.0
    best = min(abs(predicted - t) for t in support_times)
    if best > window:
        return 0.0
    return max(0.0, 1.0 - best / window)


def match_timeline(tl: SourceTimeline, silences: list[chapterize.Silence],
                   prior: SilencePrior, model: TimingModel,
                   local_dur: float, support_times: list[float] | None = None,
                   support_window: float = 12.0,
                   conf_threshold: float = 0.55) -> MatchResult:
    """Monotonic DP: assign each chapter start to a silence or a virtual slot."""
    n = len(tl.chapters)
    mids = [s.mid for s in silences]
    durs = [s.dur for s in silences]
    lengths = chapter_lengths(tl.chapters)
    support_times = support_times or []

    # Candidate silence indices per chapter, plus a virtual "no silence" slot.
    # Virtual is encoded as index = len(silences).
    n_sil = len(silences)
    VIRTUAL = n_sil
    cand: list[list[int]] = []
    preds = [model.apply(ch.start) for ch in tl.chapters]
    preds[0] = 0.0

    search = max(45.0, support_window * 3)
    for i, pred in enumerate(preds):
        opts = []
        if i == 0:
            opts = [VIRTUAL]  # chapter 1 pinned to file start
        else:
            for j, mid in enumerate(mids):
                if abs(mid - pred) <= search:
                    opts.append(j)
            opts.append(VIRTUAL)
            # Always keep a few strongest nearby breaks even if far from pred.
            if mids:
                ranked = sorted(range(n_sil),
                                key=lambda j: (
                                    -prior.break_score(durs[j]),
                                    abs(mids[j] - pred)))
                for j in ranked[:4]:
                    if j not in opts:
                        opts.append(j)
        cand.append(sorted(set(opts)))

    # DP tables: cost[i][k] = best cost for chapter i using cand[i][k]
    INF = 1e18
    cost = [[INF] * len(opts) for opts in cand]
    back = [[(-1, -1)] * len(opts) for opts in cand]
    # Secondary-best for confidence margins.
    second = [[INF] * len(opts) for opts in cand]

    def place_of(i: int, j: int) -> float:
        if i == 0:
            return 0.0
        if j == VIRTUAL:
            return max(0.0, min(local_dur, preds[i]))
        return mids[j]

    def emit_cost(i: int, j: int) -> tuple[float, dict]:
        placed = place_of(i, j)
        timing = placed - preds[i] if i > 0 else 0.0
        parts = {
            "timing": huber(timing, 5.0) / 8.0,
            "silence": 0.0,
            "virtual": 0.0,
            "support": 0.0,
            "scale": 0.0,
        }
        if i == 0:
            return 0.0, parts

        if j == VIRTUAL:
            parts["virtual"] = 4.5
        else:
            # Prefer silences that look like breaks; punish weak ones.
            bscore = prior.break_score(durs[j])
            parts["silence"] = max(-2.5, min(3.5, 1.2 - bscore))

        support = _support_score(placed, support_times, support_window)
        parts["support"] = -1.4 * support
        parts["scale"] = 8.0 * abs(model.scale - 1.0)
        total = sum(parts.values())
        return total, parts

    def transition_cost(i_prev: int, j_prev: int, i: int, j: int) -> float:
        if i == 0:
            return 0.0
        prev = place_of(i_prev, j_prev)
        cur = place_of(i, j)
        if cur < prev + 1.5:
            return 40.0  # hard-ish monotonicity / min chapter length
        expected = lengths[i_prev]
        if expected <= 0:
            return 0.0
        length_err = (cur - prev) - expected
        # Robust length residual. Scale tolerance with chapter size.
        delta = max(6.0, min(25.0, expected * 0.03))
        c = huber(length_err, delta) / 25.0
        # Skipping many silences between assigned breaks is mildly costly.
        if j_prev != VIRTUAL and j != VIRTUAL and j > j_prev:
            skipped = j - j_prev - 1
            c += min(2.5, 0.08 * skipped)
        return c

    # Initialize chapter 0
    for k, j in enumerate(cand[0]):
        c, _ = emit_cost(0, j)
        cost[0][k] = c

    for i in range(1, n):
        for k, j in enumerate(cand[i]):
            emit, _ = emit_cost(i, j)
            best_c, best_prev, sec_c = INF, (-1, -1), INF
            for pk, pj in enumerate(cand[i - 1]):
                if cost[i - 1][pk] >= INF / 2:
                    continue
                total = cost[i - 1][pk] + emit + transition_cost(i - 1, pj, i, j)
                if total < best_c:
                    sec_c = best_c
                    best_c, best_prev = total, (i - 1, pk)
                elif total < sec_c:
                    sec_c = total
            cost[i][k] = best_c
            back[i][k] = best_prev
            second[i][k] = sec_c

    # Pick best terminal state
    last = cost[-1]
    best_k = min(range(len(last)), key=lambda k: last[k])
    path = []
    i, k = n - 1, best_k
    while i >= 0 and k >= 0:
        path.append((i, cand[i][k], k))
        i, k = back[i][k]
    path.reverse()

    decisions: list[BoundDecision] = []
    length_errs = []
    timing_errs = []
    matched = 0
    confident_n = 0

    for idx, (i, j, k) in enumerate(path):
        placed = place_of(i, j)
        pred = preds[i]
        timing_err = placed - pred
        virtual = (j == VIRTUAL) or i == 0
        sil_mid = None if (j == VIRTUAL or i == 0) else mids[j]
        sil_dur = None if (j == VIRTUAL or i == 0) else durs[j]

        length_err = None
        if idx > 0:
            prev_placed = decisions[-1].placed
            expected = lengths[i - 1]
            if expected > 0:
                length_err = (placed - prev_placed) - expected
                length_errs.append(abs(length_err))

        # Confidence from local DP margin and silence quality.
        alt = second[i][k]
        best = cost[i][k]
        margin = 0.0 if alt >= INF / 2 else max(0.0, alt - best)
        conf = 1.0 / (1.0 + math.exp(-(margin - 1.0)))
        if i == 0:
            conf = 1.0
        elif virtual:
            conf *= 0.35
        else:
            conf = min(1.0, conf + 0.08 * max(0.0, prior.break_score(sil_dur or 0)))
            if abs(timing_err) <= 3.0:
                matched += 1
                timing_errs.append(abs(timing_err))
            else:
                timing_errs.append(abs(timing_err))

        support = _support_score(placed, support_times, support_window)
        conf = min(1.0, conf + 0.12 * support)
        snapped = (not virtual) and conf >= conf_threshold and i > 0
        if not snapped and i > 0:
            # Weak boundaries keep the model prediction, not a dubious silence.
            placed = max(0.0, min(local_dur, pred))
            sil_mid = sil_mid  # still report nearest considered silence
        if conf >= conf_threshold:
            confident_n += 1

        notes = []
        if virtual and i > 0:
            notes.append("no plausible silence; used predicted time")
        if support >= 0.5:
            notes.append("supported by competing source")
        if length_err is not None and abs(length_err) > 20:
            notes.append(f"length residual {length_err:+.1f}s")

        decisions.append(BoundDecision(
            index=i,
            title=tl.chapters[i].title,
            published_start=tl.chapters[i].start,
            expected_length=lengths[i],
            predicted=pred,
            placed=placed,
            silence_mid=sil_mid,
            silence_dur=sil_dur,
            timing_err=timing_err,
            length_err=length_err,
            confidence=conf,
            snapped=snapped,
            virtual=virtual and i > 0,
            support_from_other=support,
            notes=notes,
        ))

    # Enforce monotonic cleaned placement for output.
    cleaned = []
    for d in decisions:
        if cleaned and d.placed < cleaned[-1].placed + 2.0:
            d = BoundDecision(**{**d.__dict__, "placed": cleaned[-1].placed + 2.0,
                                 "notes": d.notes + ["adjusted for monotonicity"]})
        cleaned.append(d)
    if cleaned:
        cleaned[0].placed = 0.0
    decisions = cleaned

    raw_cost = cost[-1][best_k]
    # Mild complexity term so a 90-part table of contents does not beat a
    # well-matched 20-chapter edition merely by having more virtual slots.
    # Keep it small: match quality must dominate.
    complexity = 0.03 * max(0, n - 12)
    prior_bonus = -math.log(max(tl.prior, 1e-3))
    # Penalize timelines that could not land on silences at all.
    miss_ratio = 1.0 - (matched / max(n - 1, 1))
    miss_penalty = 6.0 * miss_ratio
    score = raw_cost / max(n, 1) + complexity + prior_bonus + miss_penalty

    components = {
        "raw_cost": round(raw_cost, 3),
        "normalized_cost": round(raw_cost / max(n, 1), 3),
        "complexity_penalty": round(complexity, 3),
        "source_prior_penalty": round(prior_bonus, 3),
        "miss_penalty": round(miss_penalty, 3),
        "scale_penalty": round(8.0 * abs(model.scale - 1.0), 3),
        "matched_ratio": round(matched / max(n - 1, 1), 4),
        "mean_confidence": round(
            sum(d.confidence for d in decisions) / max(n, 1), 4),
    }

    return MatchResult(
        timeline=tl,
        model=model,
        decisions=decisions,
        score=score,
        components=components,
        matched=matched,
        total=max(n - 1, 0),
        mean_timing_err=(sum(timing_errs) / len(timing_errs)) if timing_errs else 0.0,
        mean_length_err=(sum(length_errs) / len(length_errs)) if length_errs else None,
        confident_boundaries=confident_n,
    )


def evaluate_timeline(tl: SourceTimeline, silences: list[chapterize.Silence],
                      prior: SilencePrior, local_dur: float,
                      tol: float, max_offset: float, allow_scale: bool,
                      hint: float | None, hint_window: float,
                      support_times: list[float] | None = None,
                      conf_threshold: float = 0.55) -> MatchResult:
    seeds = [seed_timing(tl, silences, local_dur, tol, max_offset,
                         allow_scale, hint, hint_window)]
    seeds += extra_hypotheses(tl, [s.mid for s in silences], local_dur,
                              max_offset, allow_scale)
    best = None
    for model in seeds:
        if hint is not None and abs(model.offset - hint) > hint_window + 30:
            continue
        result = match_timeline(tl, silences, prior, model, local_dur,
                                support_times=support_times,
                                conf_threshold=conf_threshold)
        if best is None or result.score < best.score:
            best = result
    assert best is not None
    return best


# ==========================================================================
# source loading
# ==========================================================================

def timeline_from_audnex(book: chapterize.AudnexBook, identity: str,
                         prior: float = 1.0) -> SourceTimeline:
    chapters = []
    for i, ch in enumerate(book.chapters):
        if i + 1 < len(book.chapters):
            length = ch.length if ch.length else (
                book.chapters[i + 1].start - ch.start)
        else:
            length = ch.length or 0.0
        chapters.append(SourceChapter(ch.title, ch.start, length or 0.0))
    return SourceTimeline(
        source="audible",
        identity=identity,
        title=book.title,
        author=book.author,
        chapters=chapters,
        runtime=book.runtime,
        prior=prior,
    )


def timeline_from_lv(project) -> SourceTimeline:
    book = lv.project_book(project)
    return SourceTimeline(
        source="librivox",
        identity=str(project.lv_id),
        title=project.title,
        author=", ".join(project.authors) if project.authors else None,
        chapters=[SourceChapter(c.title, c.start, c.length or 0.0)
                  for c in book.chapters],
        runtime=book.runtime,
        prior=1.0,
    )


def fetch_audnex_book(asin: str, region: str) -> chapterize.AudnexBook | None:
    """Soft variant: None when Audnexus has nothing (consensus goes on)."""
    try:
        payload = audnex.fetch_chapters_payload(asin, region,
                                                user_agent=USER_AGENT)
        book = chapterize.parse_audnex(payload, asin)
    except (audnex.AudnexError, ValueError, KeyError, TypeError):
        return None
    title, author = audnex.fetch_book_meta(asin, region, USER_AGENT)
    book.title = title or book.title
    book.author = author or book.author
    return book


def load_audible_timelines(args, local_dur: float) -> list[SourceTimeline]:
    out = []
    if args.chapters_json:
        with open(args.chapters_json) as f:
            book = chapterize.parse_audnex(json.load(f), args.asin or "local")
        out.append(timeline_from_audnex(book, args.asin or args.chapters_json))
        return out

    asins = []
    if args.asin:
        asins = [args.asin]
    elif find_asin is not None:
        title = args.title
        author = args.author
        if not title:
            return out
        print(f"searching Audible for {title!r}"
              + (f" by {author!r}" if author else "") + "...")
        cands = find_asin.search_audible(
            title, author, None, args.region, args.top, args.verbose)
        if not cands and author:
            cands = find_asin.search_audible(
                None, None, f"{title} {author}", args.region,
                args.top, args.verbose)
        lf = type("LF", (), {"duration": local_dur,
                             "title": title, "author": author,
                             "guess_title": title, "guess_author": author})()
        find_asin.score_candidates(cands, lf)
        asins = [c.asin for c in cands[:args.top]]
        print(f"  {len(asins)} Audible candidate(s)")

    for asin in asins:
        book = fetch_audnex_book(asin, args.region)
        if book is None or len(book.chapters) < 2:
            print(f"  {asin}: no usable chapter data")
            continue
        # Prefer editions whose runtime is close to the local file.
        prior = 1.0
        if book.runtime and local_dur:
            rel = abs(book.runtime - local_dur) / max(book.runtime, local_dur, 1.0)
            # Runtime is a strong edition signal before silence matching.
            prior = 1.0 / (1.0 + 12.0 * rel)
        out.append(timeline_from_audnex(book, asin, prior=prior))
        label = book.title or asin
        print(f"  {asin}: {len(book.chapters)} chapters, "
              f"runtime {hms(book.runtime)}  ({label})")
    return out


def load_librivox_timelines(args, local_dur: float) -> list[SourceTimeline]:
    if lv is None:
        return []
    out = []
    if args.sections_json:
        with open(args.sections_json) as f:
            payload = json.load(f)
        books = payload.get("books", payload)
        if isinstance(books, dict):
            books = [books]
        projects = [lv.parse_project(b) for b in books if b]
        projects = [p for p in projects if p.sections]
        for p in projects[:args.top]:
            out.append(timeline_from_lv(p))
        return out

    if args.lv_id:
        projects = lv.search(None, None, args.lv_id, args.verbose)
    elif args.title:
        print(f"searching LibriVox for {args.title!r}"
              + (f" by {args.author!r}" if args.author else "") + "...")
        projects = lv.search(args.title, args.author, None, args.verbose)
    else:
        return []

    projects = [p for p in projects if p.sections]
    if not projects:
        print("  no LibriVox projects with section data")
        return []
    lv.rank(projects, local_dur, args.title)
    for p in projects[:args.top]:
        out.append(timeline_from_lv(p))
        total = p.total or p.sum_sections
        print(f"  lv:{p.lv_id}: {len(p.sections)} sections, "
              f"runtime {hms(total)}  ({p.title})")
    return out


def soft_support_times(result: MatchResult | None) -> list[float]:
    if result is None:
        return []
    return [d.placed for d in result.decisions if d.index > 0]


# ==========================================================================
# selection / reporting
# ==========================================================================

def select_best(results: list[MatchResult],
                local_dur: float | None = None) -> MatchResult:
    def runtime_skew(r: MatchResult) -> float:
        rt = r.timeline.runtime or r.timeline.published_total
        if not local_dur or not rt:
            return 0.5
        return abs(rt - local_dur) / max(rt, local_dur, 1.0)

    def utility(r: MatchResult) -> tuple:
        # Absolute matches beat ratio: 5/68 is better evidence than 3/36 when
        # both ratios are low. Runtime agreement separates same-author series.
        skew = runtime_skew(r)
        return (
            -r.matched,
            round(skew, 4),
            -r.components["matched_ratio"],
            -r.confident_boundaries,
            r.score,
        )

    return min(results, key=utility)


def result_to_placed(result: MatchResult) -> list[chapterize.Placed]:
    out = []
    for d in result.decisions:
        out.append(chapterize.Placed(
            title=d.title,
            start=d.placed,
            snapped=d.snapped,
            delta=(d.placed - d.predicted) if d.snapped else 0.0,
        ))
    return out


def print_result(result: MatchResult, alternatives: list[MatchResult]):
    tl = result.timeline
    print(f"\nselected : {tl.source} {tl.identity}")
    if tl.title:
        print(f"title    : {tl.title}"
              + (f" - {tl.author}" if tl.author else ""))
    print(f"model    : {result.model.method}, offset "
          f"{result.model.offset:+.3f}s, scale {result.model.scale:.6f}")
    print(f"score    : {result.score:.3f}  "
          f"(norm {result.components['normalized_cost']:.3f}, "
          f"matched {result.matched}/{result.total}, "
          f"confident {result.confident_boundaries}/{len(result.decisions)})")
    if result.mean_length_err is not None:
        print(f"errors   : timing mean {result.mean_timing_err:.2f}s, "
              f"length mean {result.mean_length_err:.2f}s")
    else:
        print(f"errors   : timing mean {result.mean_timing_err:.2f}s")

    if alternatives:
        print("\nalternatives:")
        for alt in alternatives[:6]:
            mark = " " if alt is not result else "*"
            print(f"  {mark} {alt.timeline.source:<9} "
                  f"{alt.timeline.identity:<12} score {alt.score:7.3f}  "
                  f"match {alt.matched}/{alt.total}  "
                  f"{(alt.timeline.title or '')[:36]}")

    print(f"\n  {'#':>3} {'placed':>12} {'pred':>12} {'conf':>5} "
          f"{'terr':>7} {'lenΔ':>7}  title")
    print("  " + "-" * 78)
    for d in result.decisions:
        flag = " " if d.confidence >= 0.55 else "?"
        len_s = f"{d.length_err:+.1f}" if d.length_err is not None else "-"
        print(f" {flag}{d.index+1:>3} {hms(d.placed):>12} {hms(d.predicted):>12} "
              f"{d.confidence:5.2f} {d.timing_err:+6.1f}s {len_s:>7}  "
              f"{d.title[:34]}")
    weak = [d for d in result.decisions if d.index > 0 and d.confidence < 0.55]
    if weak:
        print(f"\n  {len(weak)} boundary(ies) below confidence threshold "
              f"(kept as model predictions, not snapped)")


def report_json(result: MatchResult, alternatives: list[MatchResult],
                audio: str, local_dur: float) -> dict:
    return {
        "source": "consensus",
        "audio": os.path.abspath(audio),
        "duration_sec": round(local_dur, 3),
        "selected": {
            "source": result.timeline.source,
            "identity": result.timeline.identity,
            "title": result.timeline.title,
            "author": result.timeline.author,
        },
        "align": {
            "method": f"consensus/{result.model.method}",
            "offset_sec": round(result.model.offset, 3),
            "scale": round(result.model.scale, 6),
            "matched": result.matched,
            "total": result.total,
            "match_ratio": round(result.match_ratio, 4),
            "mean_error_sec": round(result.mean_timing_err, 3),
            "mean_length_error_sec": (
                round(result.mean_length_err, 3)
                if result.mean_length_err is not None else None),
            "score": round(result.score, 4),
            "components": result.components,
            "confident_boundaries": result.confident_boundaries,
            "notes": [],
        },
        "alternatives": [{
            "source": a.timeline.source,
            "identity": a.timeline.identity,
            "title": a.timeline.title,
            "score": round(a.score, 4),
            "matched": a.matched,
            "total": a.total,
            "match_ratio": round(a.match_ratio, 4),
            "offset_sec": round(a.model.offset, 3),
            "scale": round(a.model.scale, 6),
        } for a in alternatives if a is not result],
        "chapters": [{
            "index": d.index,
            "title": d.title,
            "start_sec": round(d.placed, 3),
            "predicted_sec": round(d.predicted, 3),
            "published_sec": round(d.published_start, 3),
            "expected_length_sec": round(d.expected_length, 3),
            "silence_mid_sec": (
                round(d.silence_mid, 3) if d.silence_mid is not None else None),
            "silence_dur_sec": (
                round(d.silence_dur, 3) if d.silence_dur is not None else None),
            "timing_error_sec": round(d.timing_err, 3),
            "length_error_sec": (
                round(d.length_err, 3) if d.length_err is not None else None),
            "confidence": round(d.confidence, 4),
            "snapped": d.snapped,
            "virtual": d.virtual,
            "support_from_other": round(d.support_from_other, 4),
            "notes": d.notes,
        } for d in result.decisions],
    }


# ==========================================================================
# main
# ==========================================================================

def main():
    ap = argparse.ArgumentParser(
        description="Statistically align Audible/LibriVox chapter timelines "
                    "to local silences with a global sequence matcher.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("--title")
    ap.add_argument("--author")
    ap.add_argument("--asin")
    ap.add_argument("--lv-id")
    ap.add_argument("--region", default="us")
    ap.add_argument("--chapters-json", help="offline Audnexus/Audible chapter JSON")
    ap.add_argument("--sections-json", help="offline LibriVox API response")
    ap.add_argument("--top", type=int, default=5,
                    help="candidate timelines per source (default 5)")
    ap.add_argument("--sources", choices=["auto", "audible", "librivox", "both"],
                    default="both",
                    help="which published sources to load (default both)")

    ap.add_argument("--noise", type=float, default=-40.0)
    ap.add_argument("--min-silence", type=float, default=1.0)
    ap.add_argument("--break-min", type=float)
    ap.add_argument("--break-max", type=float)
    ap.add_argument("--silence-cache", default=None)
    ap.add_argument("--no-silence-cache", action="store_true")
    ap.add_argument("--tolerance", type=float, default=4.0)
    ap.add_argument("--max-offset", type=float, default=120.0)
    ap.add_argument("--offset-hint", type=float)
    ap.add_argument("--offset-window", type=float, default=15.0)
    ap.add_argument("--no-scale", action="store_true")
    ap.add_argument("--confidence", type=float, default=0.55,
                    help="snap boundaries at or above this confidence (default 0.55)")
    ap.add_argument("--min-score-gap", type=float, default=0.05,
                    help="require this score margin over the runner-up (default 0.05)")

    ap.add_argument("--ffmetadata", metavar="FILE")
    ap.add_argument("--abs-json", metavar="FILE")
    ap.add_argument("--embed", nargs="?", const="", metavar="DEST")
    ap.add_argument("--split", metavar="DIR")
    ap.add_argument("--report", metavar="FILE")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if not os.path.isfile(args.audio):
        die(f"no such file: {args.audio}")

    local_dur = chapterize.probe_duration(args.audio)

    title, author = args.title, args.author
    if find_asin is not None and (not title or not author):
        try:
            lf = find_asin.probe(args.audio)
            title = title or lf.title
            author = author or lf.author
        except SystemExit:
            pass
    args.title, args.author = title, author

    print(f"file     : {os.path.basename(args.audio)}  ({hms(local_dur)})")
    print(f"looking  : {title!r}" + (f" by {author!r}" if author else ""))

    # --- silences ----------------------------------------------------------
    print(f"scanning for silences (noise {args.noise}dB, "
          f"min {args.min_silence}s)...")
    sil_cache = False if args.no_silence_cache else (
        args.silence_cache or chapterize.DEFAULT_CACHE)
    all_silences = chapterize.detect_silences(
        args.audio, args.noise, args.min_silence, args.verbose, sil_cache)
    silences = chapterize.filter_by_duration(
        all_silences, args.break_min, args.break_max)
    if len(silences) == len(all_silences):
        print(f"silences : {len(silences)}")
    else:
        print(f"silences : {len(all_silences)} found, {len(silences)} in band")
    if len(silences) < 2:
        die("not enough silences to run the consensus matcher; "
            "try --noise -30 --min-silence 0.8")

    prior = fit_silence_prior(silences)
    print(f"prior    : pause~{math.exp(prior.pause_mean_log):.2f}s, "
          f"break~{math.exp(prior.break_mean_log):.2f}s "
          f"(w={prior.break_weight:.2f})")

    # --- load timelines ----------------------------------------------------
    timelines: list[SourceTimeline] = []
    want_audible = args.sources in ("auto", "audible", "both")
    want_lv = args.sources in ("auto", "librivox", "both")
    if args.asin or args.chapters_json:
        want_audible = True
    if args.lv_id or args.sections_json:
        want_lv = True

    if want_audible:
        print("\n--- Audible timelines ---")
        timelines += load_audible_timelines(args, local_dur)
    if want_lv:
        print("\n--- LibriVox timelines ---")
        timelines += load_librivox_timelines(args, local_dur)

    if not timelines:
        die("no published timelines found. Pass --asin/--lv-id or --title.")

    # --- first pass without cross support ----------------------------------
    print(f"\nevaluating {len(timelines)} timeline(s)...")
    first_pass: list[MatchResult] = []
    for tl in timelines:
        result = evaluate_timeline(
            tl, silences, prior, local_dur,
            args.tolerance, args.max_offset, not args.no_scale,
            args.offset_hint, args.offset_window,
            support_times=None, conf_threshold=args.confidence)
        first_pass.append(result)
        print(f"  {tl.source:<9} {tl.identity:<12} score {result.score:7.3f}  "
              f"match {result.matched}/{result.total}  "
              f"off {result.model.offset:+.1f}s")

    # Soft support: each source family borrows the other family's best path.
    best_audible = min((r for r in first_pass if r.timeline.source == "audible"),
                       key=lambda r: r.score, default=None)
    best_lv = min((r for r in first_pass if r.timeline.source == "librivox"),
                  key=lambda r: r.score, default=None)

    results: list[MatchResult] = []
    for tl, seed in zip(timelines, first_pass):
        if tl.source == "audible":
            support = soft_support_times(best_lv)
        elif tl.source == "librivox":
            support = soft_support_times(best_audible)
        else:
            support = []
        if not support:
            results.append(seed)
            continue
        refined = evaluate_timeline(
            tl, silences, prior, local_dur,
            args.tolerance, args.max_offset, not args.no_scale,
            args.offset_hint, args.offset_window,
            support_times=support, conf_threshold=args.confidence)
        # Keep the better of supported vs unsupported.
        results.append(refined if refined.score <= seed.score else seed)

    best = select_best(results, local_dur)
    ranked = sorted(results, key=lambda r: (
        -r.matched,
        abs((r.timeline.runtime or r.timeline.published_total or local_dur)
            - local_dur) / max(local_dur, 1.0),
        r.score,
    ))
    alternatives = ranked

    # Require a mild margin unless only one candidate exists.
    if len(ranked) > 1:
        runner = next(r for r in ranked if r is not best)
        gap = runner.score - best.score
        if best.match_ratio < 0.5 and best.matched < 6:
            print(f"\n  ! weak consensus: only {best.matched}/{best.total} "
                  f"boundaries matched "
                  f"(runner-up {runner.timeline.identity} "
                  f"score Δ {gap:+.3f})")

    print_result(best, alternatives)

    placed = result_to_placed(best)
    book = book_from_timeline(best.timeline)
    meta = chapterize.ffmetadata(placed, local_dur, book, audio=args.audio)
    wrote = False

    if args.ffmetadata:
        with open(args.ffmetadata, "w") as f:
            f.write(meta)
        print(f"\nwrote {args.ffmetadata}")
        wrote = True
    if args.abs_json:
        with open(args.abs_json, "w") as f:
            f.write(chapterize.abs_json(placed, local_dur))
        print(f"wrote {args.abs_json}")
        wrote = True
    if args.embed is not None:
        dest = args.embed or (
            os.path.splitext(args.audio)[0] + ".chapters"
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
        chapterize.split(args.audio, placed, local_dur, args.split, args.verbose)
        wrote = True
    if args.report:
        with open(args.report, "w") as f:
            json.dump(report_json(best, alternatives, args.audio, local_dur),
                      f, indent=2)
        print(f"wrote {args.report}")
        wrote = True

    if not wrote:
        print("\n(preview only - pass --embed, --split, --abs-json, "
              "--ffmetadata or --report)")

    # Non-zero when the chosen model is clearly unusable, so wrappers can
    # fall through cleanly.
    if best.match_ratio < 0.35 and best.confident_boundaries < 3:
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
