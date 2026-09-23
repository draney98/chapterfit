"""Sacred envelope local-rate DP alignment."""
from __future__ import annotations

import math
import statistics

from chaptertools.envelope.artifact import Artifact
from chaptertools.util import die, parse_time

def solve(chapters, cands, pins, max_off, w_rate, skip_penalty, max_skip=6,
          drift_allow=0.02, place_bonus=10.0, max_opts=60,
          offset_hint=None, hint_window=180.0):
    """Align published chapters onto observed gaps by local rate consistency.

    No global offset or scale appears anywhere in this formulation. Between
    two assigned chapters the implied rate is

        r = (t_later - t_earlier) / (p_later - p_earlier)

    and a correct assignment keeps r near 1 everywhere, whatever the absolute
    offset is and however much the timeline has drifted since the start. That
    makes trimmed tape joins, a missing foreword, and a wrong constant offset
    all the same kind of small local deviation rather than three separate
    failure modes. Chapters whose break is undetectable are skipped instead
    of being forced onto a passing pause.

    Returns (assignment, stats) where assignment maps chapter index -> time.
    """
    n = len(chapters)
    ts = sorted(g["mid"] for g in cands)
    NEG = -1e300

    # candidates per chapter: anything within max_off of the published time,
    # widened to allow for drift accumulated across the book
    opts = []
    for i, ch in enumerate(chapters):
        if i in pins:
            opts.append([pins[i]])
            continue
        if i == 0:
            opts.append([])           # chapter 1 is the file start by definition
            continue
        # Without a hint the window is centred on the published time, which
        # is wrong by however much front matter the file carries. On a dense
        # file the "nearest max_opts" cut then discards the true gap before
        # the solver ever sees it. A rough offset moves the window to the
        # right place and lets it be much narrower.
        if offset_hint is not None:
            centre = ch["start"] + offset_hint
            allow = hint_window + drift_allow * ch["start"]
        else:
            centre = ch["start"]
            allow = max_off + drift_allow * ch["start"]
        near = [t for t in ts if abs(t - centre) <= allow]
        if len(near) > max_opts:
            near.sort(key=lambda t: abs(t - centre))
            near = sorted(near[:max_opts])
        opts.append(near)

    best = [[NEG] * max(1, len(o)) for o in opts]
    back = [[None] * max(1, len(o)) for o in opts]
    start_cost = 0.0

    for i in range(1, n):
        for j, t in enumerate(opts[i]):
            # option 1: this is the first assigned chapter. Chapters before
            # it are left unplaced, which costs.
            cur, bk = place_bonus - skip_penalty * (i - 1), None
            # option 2: continue from an earlier assignment
            for pi in range(max(1, i - max_skip), i):
                dp = chapters[i]["start"] - chapters[pi]["start"]
                if dp <= 0:
                    continue
                for pj, pt in enumerate(opts[pi]):
                    if best[pi][pj] <= NEG / 2 or t <= pt:
                        continue
                    r = (t - pt) / dp
                    if r <= 0.2 or r >= 5.0:
                        continue
                    cost = w_rate * abs(math.log(r)) + skip_penalty * (i - pi - 1)
                    v = best[pi][pj] + place_bonus - cost
                    if v > cur:
                        cur, bk = v, (pi, pj)
            if cur > best[i][j] or best[i][j] <= NEG / 2:
                best[i][j], back[i][j] = cur, bk

    # best terminal state
    endi = endj = None
    bv = NEG
    for i in range(n - 1, 0, -1):
        for j in range(len(opts[i])):
            # chapters after the last assignment are also unplaced
            v = best[i][j] - skip_penalty * (n - 1 - i)
            if v > bv:
                bv, endi, endj = v, i, j
    if endi is None:
        return {}, {"assigned": 0, "rate_spread": None, "rates": []}

    assign = {}
    i, j = endi, endj
    while i is not None:
        assign[i] = opts[i][j]
        nxt = back[i][j]
        if nxt is None:
            break
        i, j = nxt

    order = sorted(assign)
    rates = []
    for a, b in zip(order, order[1:]):
        dp = chapters[b]["start"] - chapters[a]["start"]
        if dp > 0:
            rates.append((assign[b] - assign[a]) / dp)
    spread = statistics.pstdev([math.log(r) for r in rates]) if len(rates) > 1 \
        else 0.0
    return assign, {"assigned": len(assign), "rate_spread": spread,
                    "rates": rates,
                    "rate_median": statistics.median(rates) if rates else None}


def chance_rate(n_cands, dur, tol):
    return min(1.0, 2.0 * tol * n_cands / max(dur, 1e-9))


def suggest_measurements(art, al, n=2):
    """Which single listening check would reduce uncertainty the most?"""
    res = al["results"]
    pinned = [r["time"] for r in res if r["pinned"] and r["time"] is not None]
    dur = art.data["envelope"]["duration"]
    scored = []
    for r in res:
        if r["index"] == 0 or r["pinned"] or r["time"] is None:
            continue
        # uncertainty x leverage: distance from the nearest measured point
        lev = min((abs(r["time"] - p) for p in pinned), default=dur / 2)
        unc = (1 - r["confidence"]) + (r["spread"] or 0) / 60.0
        if unc < 0.02:
            continue
        scored.append((unc * math.sqrt(lev), r))
    scored.sort(key=lambda x: -x[0])
    out, used = [], []
    for _, r in scored:
        if any(o["time"] is not None
               and abs(r["time"] - o["time"]) < dur * 0.1 for o in used):
            continue
        used.append(r)
        out.append(r)
        if len(out) >= n:
            break
    return out


def score_population(chapters, cands, dur, pins, max_off, w_rate,
                     skip_penalty, tol):
    """How well does this gap population explain the chapter sequence?"""
    assign, st = solve(chapters, cands, pins, max_off, w_rate, skip_penalty)
    need = max(1, len(chapters) - 1)
    frac = st["assigned"] / need
    # chance baseline: with this many candidates, how many chapters would a
    # random monotone walk hit within tolerance?
    p = chance_rate(len(cands), dur, tol)
    exp = need * p
    var = need * p * (1 - p)
    z = (st["assigned"] - exp) / math.sqrt(var) if var > 0 else 0.0
    # Consistency is the real discriminator, and it has to bite hard. A wrong
    # edition can still hit many gaps by luck when candidates are sparse - that
    # inflates z - but the implied local rates then scatter. Genuine alignments
    # sit near 0.005 even under aggressive drift, so decay on that scale.
    spread = st["rate_spread"] or 0.0
    consistency = math.exp(-spread / 0.02)
    return {"assign": assign, "assigned": st["assigned"], "need": need,
            "fraction": frac, "expected": exp, "z": z,
            "rate_spread": st["rate_spread"],
            "rate_median": st["rate_median"],
            "consistency": consistency,
            "score": z * consistency,
            "n_candidates": len(cands)}


def posteriors(chapters, cands, assign, pins, window, w_rate):
    """Per-chapter confidence: how much better is the chosen gap than the
    alternatives nearby, given the neighbours the solver settled on?"""
    ts = sorted(g["mid"] for g in cands)
    order = sorted(assign)
    out = []
    for idx, ch in enumerate(chapters):
        if idx == 0:
            out.append({"time": 0.0, "confidence": 1.0, "spread": 0.0,
                        "n_options": 1})
            continue
        if idx not in assign:
            out.append({"time": None, "confidence": 0.0, "spread": None,
                        "n_options": 0})
            continue
        t = assign[idx]
        if idx in pins:
            out.append({"time": t, "confidence": 1.0, "spread": 0.0,
                        "n_options": 1})
            continue
        # local model from the nearest assigned neighbours
        prev = max([o for o in order if o < idx], default=None)
        nxt = min([o for o in order if o > idx], default=None)
        if prev is not None and nxt is not None:
            dp = chapters[nxt]["start"] - chapters[prev]["start"]
            frac = ((ch["start"] - chapters[prev]["start"]) / dp) if dp else 0.5
            pred = assign[prev] + frac * (assign[nxt] - assign[prev])
        elif prev is not None:
            pred = assign[prev] + (ch["start"] - chapters[prev]["start"])
        else:
            pred = t
        near = [x for x in ts if abs(x - pred) <= window]
        if not near:
            near = [t]
        weights = [math.exp(-abs(x - pred) / 3.0) for x in near]
        tot = sum(weights) or 1.0
        probs = [w / tot for w in weights]
        k = min(range(len(near)), key=lambda i2: abs(near[i2] - t))
        mean = sum(p2 * x for p2, x in zip(probs, near))
        var = sum(p2 * (x - mean) ** 2 for p2, x in zip(probs, near))
        out.append({"time": t, "confidence": probs[k],
                    "spread": math.sqrt(var), "n_options": len(near)})
    return out


def stage_align(art: Artifact, args) -> dict:
    ident = art.require("identification",
                        "chapterfit.py identify FILE --asin ASIN")
    gs = art.data["gaps"]
    dur = art.data["envelope"]["duration"]
    chapters = ident["chapters"]
    best = ident.get("best_trial")
    if not best:
        die("identification found no usable population; nothing to align")

    pins = {int(k): v for k, v in (art.data.get("pins") or {}).items()}
    if pins:
        print(f"  {len(pins)} measured position(s) held fixed")

    mids = set(round(m, 3) for m in ident.get("candidate_mids") or [])
    if not mids:
        die("identification stored no candidate set; re-run identify")
    cands = [g for g in gs["gaps"] if round(g["mid"], 3) in mids]
    print(f"  candidate set: {best.get('name','?')} ({len(cands)} gaps)")

    hint = None
    if args.near:
        if "=" not in args.near:
            die("--near wants CHAPTER=TIME, e.g. --near 3=43:47")
        k, v = args.near.split("=", 1)
        try:
            ci = int(k) - 1
        except ValueError:
            die(f"{k!r} is not a chapter number")
        if not 0 <= ci < len(chapters):
            die(f"chapter {k} out of range 1..{len(chapters)}")
        hint = parse_time(v) - chapters[ci]["start"]
        print(f"  approximate offset from chapter {k}: {hint:+.1f}s "
              f"(searching +/-{args.near_window:.0f}s around it)")
    assign, st = solve(chapters, cands, pins, args.max_offset,
                       args.rate_penalty, args.skip_penalty,
                       offset_hint=hint, hint_window=args.near_window)
    post = posteriors(chapters, cands, assign, pins, args.window,
                      args.rate_penalty)

    results = []
    order = sorted(assign)
    conflicts = []
    for i, ch in enumerate(chapters):
        pinfo = post[i]
        if i in pins and pinfo["time"] is None:
            # The measurement stands; it is the alignment that could not
            # accommodate it. That means the pin contradicts its neighbours -
            # worth saying out loud rather than dropping.
            conflicts.append(i)
            pinfo = {"time": pins[i], "confidence": 1.0, "spread": None,
                     "n_options": 1}
        # residual against a line through the neighbouring assignments
        resid = None
        if i in assign and len(order) >= 2:
            rate = st.get("rate_median") or 1.0
            ref = order[0]
            pred = assign[ref] + rate * (ch["start"] - chapters[ref]["start"])
            resid = assign[i] - pred
        results.append({
            "index": i, "title": ch["title"], "published": ch["start"],
            "time": pinfo["time"], "confidence": pinfo["confidence"],
            "spread": pinfo["spread"], "pinned": i in pins,
            "n_options": pinfo["n_options"], "residual": resid,
        })

    if conflicts:
        print(f"  ! {len(conflicts)} pin(s) could not be reconciled with the "
              f"rest: " + ", ".join(f"chapter {i+1}" for i in conflicts))
        print("    They are kept, but they contradict their neighbours - "
              "check the times,\n    or remove one with "
              "'pin FILE --remove N'.")
    placed = [r for r in results if r["time"] is not None]
    conf = [r for r in placed if r["confidence"] >= args.min_confidence]
    print(f"  {len(placed)}/{len(results)} chapters placed, "
          f"{len(conf)} at confidence >= {args.min_confidence:.2f}")
    if st.get("rate_median"):
        print(f"  implied rate {st['rate_median']:.6f} "
              f"({(st['rate_median']-1)*3600:+.1f}s drift per hour), "
              f"spread {st['rate_spread']:.4f}")
    return {"candidate": best.get("name"), "rate": st.get("rate_median"),
            "rate_spread": st.get("rate_spread"),
            "results": results, "min_confidence": args.min_confidence,
            "conflicts": conflicts}


# ==========================================================================
# reporting
# ==========================================================================

