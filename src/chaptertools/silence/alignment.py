"""Sacred silence-snap alignment (offset/scale vote)."""

from __future__ import annotations

from dataclasses import dataclass, field

from chaptertools.audnex import AudnexBook
from chaptertools.output import Placed
from chaptertools.silence.detection import Silence
from chaptertools.util import hms

__all__ = [
    "Alignment",
    "filter_by_duration",
    "match_stats",
    "_score",
    "_linfit",
    "solve_alignment",
    "place_chapters",
]

def filter_by_duration(silences: list["Silence"], lo: float | None,
                       hi: float | None) -> list["Silence"]:
    """Keep only silences whose length falls in [lo, hi].

    Chapter breaks in a produced audiobook are a deliberate, consistent length;
    sentence pauses are shorter and vary continuously. Slicing the scan by
    duration separates the two populations without rescanning the file.
    """
    out = silences
    if lo is not None:
        out = [s for s in out if s.dur >= lo]
    if hi is not None:
        out = [s for s in out if s.dur <= hi]
    return out


def match_stats(matched: int, total: int, n_silences: int, span: float,
                tol: float) -> dict:
    """How impressive is this match count, given how many silences there are?

    With S silences spread over span seconds, a boundary placed at random
    lands within tol of one with probability ~2*tol*S/span. Comparing the
    observed count against that baseline is the difference between a real
    alignment and a coincidence.
    """
    p = min(1.0, 2.0 * tol * n_silences / span) if span > 0 else 1.0
    expected = total * p
    enrichment = (matched / expected) if expected > 0 else float("inf")
    var = total * p * (1 - p)
    z = (matched - expected) / (var ** 0.5) if var > 0 else float("inf")
    return {"p_chance": p, "expected": expected,
            "enrichment": enrichment, "z": z}


@dataclass
class Alignment:
    offset: float = 0.0
    scale: float = 1.0
    matched: int = 0
    total: int = 0
    mean_err: float = 0.0
    method: str = "none"
    notes: list[str] = field(default_factory=list)

    def apply(self, t: float) -> float:
        return t * self.scale + self.offset


def _score(boundaries: list[float], sil_mids: list[float],
           offset: float, scale: float, tol: float):
    """How many boundaries land within tol of a silence, and the total error."""
    matched, err, pairs = 0, 0.0, []
    for b in boundaries:
        t = b * scale + offset
        # linear scan is fine: silence counts are in the hundreds
        best, bestd = None, tol
        for s in sil_mids:
            d = abs(s - t)
            if d < bestd:
                best, bestd = s, d
        if best is not None:
            matched += 1
            err += bestd
            pairs.append((b, best))
    return matched, (err / matched if matched else float("inf")), pairs


def _linfit(pairs: list[tuple[float, float]]):
    """Least squares s = scale*b + offset over matched pairs."""
    n = len(pairs)
    if n < 3:
        return None
    sx = sum(b for b, _ in pairs)
    sy = sum(s for _, s in pairs)
    sxx = sum(b * b for b, _ in pairs)
    sxy = sum(b * s for b, s in pairs)
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:
        return None
    scale = (n * sxy - sx * sy) / denom
    offset = (sy - scale * sx) / n
    return scale, offset


def solve_alignment(book: AudnexBook, silences: list[Silence], local_dur: float,
                    mode: str, tol: float, max_offset: float,
                    allow_scale: bool, verbose: bool,
                    hint: float | None = None, hint_window: float = 15.0,
                    manual_offset: float | None = None) -> Alignment:
    # interior boundaries only; chapter 1 always starts at 0 of its own timeline
    boundaries = [c.start for c in book.chapters[1:]]
    sil_mids = [s.mid for s in silences]
    al = Alignment(total=len(boundaries))

    if mode == "manual":
        al.offset = manual_offset or 0.0
        al.method = f"manual offset {al.offset:+.3f}s"
        al.matched, al.mean_err, _ = _score(boundaries, sil_mids, al.offset, 1.0, tol)
        return al

    if mode == "none":
        al.method = "none (raw Audnexus timings)"
        al.matched, al.mean_err, _ = _score(boundaries, sil_mids, 0.0, 1.0, tol)
        return al

    if mode == "intro":
        al.offset = -book.brand_intro
        al.method = f"brand intro ({book.brand_intro:.3f}s removed)"
        al.matched, al.mean_err, _ = _score(boundaries, sil_mids, al.offset, 1.0, tol)
        return al

    # mode == "auto": vote over every (boundary, silence) pairing.
    if not boundaries or not sil_mids:
        al.method = "auto (insufficient data)"
        al.notes.append("no boundaries or no silences to align against")
        return al

    # A hint outside the search range would silently find nothing, so widen
    # the range to cover it rather than failing for a reason nobody can see.
    if hint is not None:
        needed = abs(hint) + hint_window
        if needed > max_offset:
            al.notes.append(
                f"widened the offset search from {max_offset:.0f}s to "
                f"{needed:.0f}s to reach the hint {hint:+.1f}s")
            max_offset = needed

    candidates = []
    for b in boundaries:
        for s in sil_mids:
            d = s - b
            if abs(d) > max_offset:
                continue
            # A hint narrows the search. On files with thousands of silences
            # this is the difference between a real answer and a coincidence.
            if hint is not None and abs(d - hint) > hint_window:
                continue
            candidates.append(d)

    if not candidates:
        if hint is not None:
            al.notes.append(
                f"no silence within {hint_window:.0f}s of the hinted offset "
                f"{hint:+.1f}s; using the hint as given")
            al.offset = hint
            al.method = f"auto (hint {hint:+.1f}s, unconfirmed)"
            al.matched, al.mean_err, _ = _score(
                boundaries, sil_mids, al.offset, 1.0, tol)
            return al
        al.notes.append(
            f"no silence within {max_offset:.0f}s of any Audnexus boundary; "
            "the file is probably a different edition")
        al.method = "auto (failed, fell back to brand intro)"
        al.offset = -book.brand_intro
        al.matched, al.mean_err, _ = _score(boundaries, sil_mids, al.offset, 1.0, tol)
        return al

    # Cluster candidate offsets, then score each cluster centre properly.
    candidates.sort()
    clusters, cur = [], [candidates[0]]
    for d in candidates[1:]:
        if d - cur[-1] <= tol:
            cur.append(d)
        else:
            clusters.append(cur)
            cur = [d]
    clusters.append(cur)
    centres = [sum(c) / len(c) for c in clusters]

    best = None
    for off in centres:
        matched, err, pairs = _score(boundaries, sil_mids, off, 1.0, tol)
        key = (matched, -err)
        if best is None or key > best[0]:
            best = (key, off, matched, err, pairs)

    _, offset, matched, err, pairs = best
    al.offset, al.matched, al.mean_err = offset, matched, err
    al.method = ("auto (offset search)" if hint is None else
                 f"auto (offset search near hint {hint:+.1f}s)")

    if allow_scale and len(pairs) >= 5:
        fit = _linfit(pairs)
        if fit:
            scale, off2 = fit
            # a sane rip is within ~1% of the Audible timeline
            if 0.99 <= scale <= 1.01:
                m2, e2, _ = _score(boundaries, sil_mids, off2, scale, tol)
                if (m2, -e2) > (matched, -err):
                    al.scale, al.offset = scale, off2
                    al.matched, al.mean_err = m2, e2
                    al.method = "auto (offset + scale fit)"
                    if abs(scale - 1.0) > 1e-4:
                        al.notes.append(
                            f"time scale {scale:.5f} - the local file runs "
                            f"{'slower' if scale > 1 else 'faster'} than Audible's")
            else:
                al.notes.append(
                    f"rejected implausible scale fit {scale:.4f}; using offset only")

    if book.runtime:
        drift = local_dur - (book.runtime * al.scale + al.offset)
        if abs(drift) > 60:
            al.notes.append(
                f"local file ends {hms(abs(drift))} "
                f"{'later' if drift > 0 else 'earlier'} than expected - "
                "possibly a different edition or an abridgement")

    return al


def place_chapters(book: AudnexBook, silences: list[Silence], al: Alignment,
                   local_dur: float, snap: bool, snap_window: float,
                   min_len: float) -> list[Placed]:
    sil_mids = [s.mid for s in silences]
    placed: list[Placed] = []

    for i, ch in enumerate(book.chapters):
        t = 0.0 if i == 0 else al.apply(ch.start)
        snapped, delta = False, 0.0

        if snap and i > 0 and sil_mids:
            best, bestd = None, snap_window
            for s in sil_mids:
                d = abs(s - t)
                if d < bestd:
                    best, bestd = s, d
            if best is not None:
                delta = best - t
                t = best
                snapped = True

        t = max(0.0, min(t, local_dur))
        placed.append(Placed(ch.title, t, snapped, delta))

    # enforce monotonic, drop anything degenerate
    cleaned: list[Placed] = []
    for p in placed:
        if cleaned and p.start < cleaned[-1].start + min_len:
            continue
        cleaned.append(p)

    if cleaned:
        cleaned[0].start = 0.0
    return cleaned


