"""Edition identification against Audnexus chapter lists."""
from __future__ import annotations

import os
import statistics

from chaptertools.audnex import AudnexError, chapters_payload
from chaptertools.envelope.alignment import score_population
from chaptertools.envelope.artifact import Artifact
from chaptertools.util import die, hms


def fetch_chapters(asin: str, region: str, cache_dir: str | None = None,
                   offline: bool = False) -> dict:
    """Raw Audnexus payload through the shared cache (chaptertools.audnex)."""
    try:
        return chapters_payload(
            asin, region, cache_dir, offline=offline,
            on_cached=lambda p: print(
                f"  using cached chapter data ({os.path.basename(p)})"))
    except AudnexError as e:
        die(str(e))


def coarse_fit(chapters, cands, dur, max_off, tol):
    """Robust global offset and scale from candidate gaps only.

    Voting on every (chapter, candidate) pairing. This is cheap and reliable
    once the candidate set is the chapter-break population rather than every
    silence in the file.
    """
    pub = [c["start"] for c in chapters[1:]]
    ts = [g["mid"] for g in cands]
    if not pub or not ts:
        return None
    offs = []
    for b in pub:
        for t in ts:
            d = t - b
            if abs(d) <= max_off:
                offs.append(d)
    if not offs:
        return None
    offs.sort()
    clusters, cur = [], [offs[0]]
    for d in offs[1:]:
        if d - cur[-1] <= tol:
            cur.append(d)
        else:
            clusters.append(cur)
            cur = [d]
    clusters.append(cur)

    best = None
    for c in clusters:
        off = statistics.fmean(c)
        matched, err, pairs = 0, 0.0, []
        for b in pub:
            t0 = b + off
            hit, hd = None, tol
            for t in ts:
                dd = abs(t - t0)
                if dd < hd:
                    hit, hd = t, dd
            if hit is not None:
                matched += 1
                err += hd
                pairs.append((b, hit))
        if matched < 2:
            continue
        key = (matched, -err / matched)
        if best is None or key > best[0]:
            best = (key, off, matched, err / matched, pairs)
    if not best:
        return None
    _, off, matched, mean_err, pairs = best

    scale = 1.0
    if len(pairs) >= 5:
        n = len(pairs)
        sx = sum(b for b, _ in pairs); sy = sum(t for _, t in pairs)
        sxx = sum(b * b for b, _ in pairs); sxy = sum(b * t for b, t in pairs)
        den = n * sxx - sx * sx
        if abs(den) > 1e-9:
            sc = (n * sxy - sx * sy) / den
            if 0.97 <= sc <= 1.03:
                scale = sc
                off = (sy - sc * sx) / n
    return {"offset": off, "scale": scale, "matched": matched,
            "total": len(pub), "mean_error": mean_err,
            "n_candidates": len(ts)}




def candidate_sets(gaps, pops, need_n):
    """Every plausible answer to 'which gaps are the chapter breaks?'.

    Clustering finds populations, but a chapter-break population can be a
    thin tail pressed against a much larger one - on a dense file the
    centroid is dragged away and the tail is never isolated. So also offer
    the upper tail directly at a range of cut-offs and let the scorer choose.
    """
    sets = []
    for p in pops:
        if p["n"] >= max(2, need_n * 0.4):
            sets.append({"name": f"pop {p['k']}",
                         "detail": f"{p['centre']:.2f}s cluster",
                         "gaps": [g for g in gaps if g["pop"] == p["k"]]})
    durs = sorted(g["dur"] for g in gaps)
    if durs:
        cuts = set()
        for q in (0.80, 0.90, 0.95, 0.97, 0.98, 0.99, 0.995):
            cuts.add(round(durs[min(len(durs) - 1, int(len(durs) * q))], 2))
        lo, hi = durs[0], durs[-1]
        step = max(0.25, (hi - lo) / 12)
        x = lo + step
        while x < hi:
            cuts.add(round(x, 2))
            x += step
        for c in sorted(cuts):
            sel = [g for g in gaps if g["dur"] >= c]
            if max(2, need_n * 0.4) <= len(sel) <= need_n * 12:
                sets.append({"name": f">={c:.2f}s",
                             "detail": f"upper tail, {len(sel)} gaps",
                             "gaps": sel})
    # drop duplicates by size and membership signature
    seen, out = set(), []
    for cs in sets:
        key = (len(cs["gaps"]),
               round(sum(g["mid"] for g in cs["gaps"][:20]), 2))
        if key in seen:
            continue
        seen.add(key)
        out.append(cs)
    return out


def stage_identify(art: Artifact, chapters, meta, label, tol, verbose) -> dict:
    gs = art.require("gaps", "chapterfit.py measure FILE")
    dur = art.data["envelope"]["duration"]
    pops = gs["populations"]
    gaps = gs["gaps"]

    rep = {"label": label, "n_chapters": len(chapters),
           "published_runtime": meta.get("runtime"),
           "chapters": chapters, "meta": meta}

    print(f"  {len(chapters)} chapters from {label}")

    # --- structural gates, before trying to place anything --------------
    reasons = []
    if meta.get("runtime"):
        ratio = dur / meta["runtime"]
        rep["duration_ratio"] = ratio
        print(f"  published runtime {hms(meta['runtime'])} vs file {hms(dur)} "
              f"(ratio {ratio:.3f})")
        if ratio < 0.85 or ratio > 1.18:
            reasons.append(f"file is {ratio*100:.0f}% of the published runtime. "
                           f"A correct edition matches within a few percent, so "
                           f"this is an abridgement or a different edition.")
        elif ratio < 0.95 or ratio > 1.05:
            reasons.append(f"file is {ratio*100:.0f}% of the published runtime - "
                           f"further off than an edition match should be; "
                           f"worth double-checking the ASIN")

    # which population could plausibly be the chapter breaks?
    need_n = len(chapters) - 1
    viable = [p for p in pops if p["n"] >= need_n * 0.4]
    rep["viable_populations"] = [p["k"] for p in viable]
    if not viable:
        reasons.append(f"no gap population has enough members to supply "
                       f"{need_n} chapter breaks; the largest has "
                       f"{max((p['n'] for p in pops), default=0)}")

    rep["blocked"] = reasons
    for r in reasons:
        print(f"  ! {r}")

    # --- score every candidate hypothesis with the real solver -----------
    pins = {int(k): v for k, v in (art.data.get("pins") or {}).items()}
    sets = candidate_sets(gaps, pops, need_n)
    print(f"\n  testing {len(sets)} candidate gap sets...")
    trials = []
    for cs in sets:
        sc = score_population(chapters, cs["gaps"], dur, pins,
                              art.data.get("max_offset", 900.0),
                              art.data.get("w_rate", 25.0),
                              art.data.get("skip_penalty", 3.0), tol)
        sc["name"] = cs["name"]
        sc["detail"] = cs["detail"]
        sc["min_dur"] = min((g["dur"] for g in cs["gaps"]), default=0.0)
        sc["mids"] = [g["mid"] for g in cs["gaps"]]
        trials.append(sc)

    if trials:
        trials.sort(key=lambda t: -t["score"])
        print(f"\n  {'candidate':>12} {'cands':>7} {'placed':>10} {'chance':>8} "
              f"{'z':>7} {'rate':>9} {'spread':>9} {'score':>8}")
        for t in trials[:12]:
            rm = f"{t['rate_median']:.5f}" if t["rate_median"] else "-"
            rs = f"{t['rate_spread']:.4f}" if t["rate_spread"] is not None else "-"
            print(f"  {t['name']:>12} {t['n_candidates']:>7} "
                  f"{t['assigned']:>4}/{t['need']:<5} {t['expected']:>8.1f} "
                  f"{t['z']:>7.1f} {rm:>9} {rs:>9} {t['score']:>8.1f}")
        if len(trials) > 12:
            print(f"  ... {len(trials)-12} weaker candidates not shown")
        best = trials[0]
        rep["best_trial"] = {k: v for k, v in best.items()
                             if k not in ("assign", "mids")}
        rep["candidate_mids"] = best["mids"]
        rep["assignment"] = {str(k): v for k, v in best["assign"].items()}
        rep["candidate_min_dur"] = best["min_dur"]
        print(f"\n  best: {best['name']} ({best['detail']}) - "
              f"{best['assigned']}/{best['need']} chapters placed, "
              f"z={best['z']:.1f}, rate spread {(best['rate_spread'] or 0):.4f}")
        if best.get("rate_median"):
            print(f"        implied rate {best['rate_median']:.5f} "
                  f"({(best['rate_median']-1)*3600:+.1f}s per hour of drift)")
        sp = best.get("rate_spread") or 0.0
        if best["score"] >= 5 and sp < 0.02:
            print("        verdict: consistent - this edition fits the audio")
        elif best["score"] >= 2:
            print(f"""
  ! weak. Chapters are being placed, but the implied rate scatters by
    {sp*100:.1f}% between them; a real alignment stays under about 2%. Treat
    the positions as provisional and check a few by ear.""")
        else:
            print(f"""
  ! REJECTED. The best candidate scatters by {sp*100:.1f}% between chapters,
    which is not an alignment - it is gaps being hit by chance. A high z
    alone does not mean much when the candidate set is sparse.

    Most likely the chapter data is for a different edition. Check the
    runtime ratio above: a correct edition matches within a few percent.""")
    else:
        rep["best_trial"] = None
        print("  no candidate set produced a usable fit")
    return rep


# ==========================================================================
# stage 7 - probabilistic alignment
# ==========================================================================

