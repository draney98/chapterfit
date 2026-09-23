"""Envelope measure / calibrate / gap classification."""
from __future__ import annotations

import array
import json
import re
import math
import os
import statistics
import subprocess

from chaptertools.envelope.artifact import ENV_FLOOR_DB, ENVELOPE_HZ, Artifact
from chaptertools.util import die, hms

def probe(audio: str) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_format",
                        "-show_streams", "-of", "json", audio],
                       capture_output=True, text=True)
    if r.returncode:
        die(f"ffprobe failed on {audio}")
    j = json.loads(r.stdout)
    fmt = j.get("format", {})
    astream = next((s for s in j.get("streams", [])
                    if s.get("codec_type") == "audio"), {})
    return {
        "declared_duration": float(fmt.get("duration", 0) or 0),
        "bit_rate": int(fmt.get("bit_rate", 0) or 0),
        "codec": astream.get("codec_name"),
        "sample_rate": int(astream.get("sample_rate", 0) or 0),
        "channels": astream.get("channels"),
        "format": fmt.get("format_name"),
    }


TIME_RE = re.compile(r"time=(\d+):(\d\d):(\d\d\.\d+)")


def decoded_duration(audio: str) -> float:
    """Decode the whole file to find out how long it really is.

    The declared duration comes from a header. On a file built by joining
    cassette sides, that header describes only the first segment, so the
    declared figure can be wildly wrong - and every player's seek bar
    inherits the error.
    """
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", audio,
                        "-f", "null", "-"], capture_output=True, text=True)
    last = None
    for m in TIME_RE.finditer(r.stderr):
        last = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return last or 0.0


def stage_verify(art: Artifact, verbose: bool, real: float | None = None) -> dict:
    """Compare the declared duration against the real one.

    The real length comes from the envelope pass, which already decoded the
    whole file - so this costs nothing rather than a second full decode.
    """
    info = probe(art.audio)
    if real is None:
        print("  decoding to measure the true length...")
        real = decoded_duration(art.audio)
    declared = info["declared_duration"]
    drift = real - declared
    rel = abs(drift) / max(real, 1e-9)

    info["decoded_duration"] = real
    info["header_error_sec"] = drift
    info["header_error_ratio"] = rel
    info["timeline_trustworthy"] = rel < 0.002
    info["duration_from"] = "envelope"

    print(f"  declared {hms(declared)}   decoded {hms(real)}   "
          f"difference {drift:+.2f}s")
    if not info["timeline_trustworthy"]:
        print(f"""
  ! The header is wrong by {rel*100:.1f}%. Players map their seek bar from
    that header, so every timestamp you check by seeking will be off, and
    the error grows with position. This is what joining MP3s without
    rewriting the Xing header does.

    Fix it first, then re-run measure:
        ffmpeg -i {art.audio!r} -c:a copy -write_xing 1 OUT.mp3
""")
    return info


# ==========================================================================
# stage 2 - the envelope
# ==========================================================================

def extract_envelope(audio: str, hz: int, verbose: bool):
    """Rectify, smooth and decimate to a loudness envelope.

    Done once. Every threshold afterwards is a query against this array
    rather than another pass over the audio.
    """
    af = (f"aresample=4000,aeval=abs(val(0)):c=same,"
          f"lowpass=f={hz // 2},aresample={hz}")
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", audio, "-af", af,
           "-ac", "1", "-f", "s16le", "-"]
    if verbose:
        print(f"  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode:
        die(f"envelope extraction failed:\n{r.stderr.decode()[-600:]}")
    pcm = array.array("h")
    pcm.frombytes(r.stdout)
    out = []
    for v in pcm:
        a = abs(v)
        out.append(ENV_FLOOR_DB if a == 0
                   else max(ENV_FLOOR_DB, 20 * math.log10(a / 32768.0)))
    return out


def stage_measure(art: Artifact, hz: int, verbose: bool) -> dict:
    print("  extracting loudness envelope...")
    env = extract_envelope(art.audio, hz, verbose)
    art.write_envelope(env)
    meta = {"hz": hz, "frames": len(env),
            "duration": len(env) / hz,
            "path": os.path.basename(art.env_path),
            "scale": "mean-rectified dBFS (relative; calibrate before use)"}
    print(f"  {len(env)} frames at {hz} Hz "
          f"({len(env)/hz/3600:.2f} h, {len(env)/1024:.0f} KiB)")
    return meta


# ==========================================================================
# stage 3 - calibrate from the distribution
# ==========================================================================

def histogram(env, lo=-96, hi=0, width=1.0):
    n = int((hi - lo) / width) + 1
    h = [0] * n
    for v in env:
        i = int((max(lo, min(hi, v)) - lo) / width)
        h[i] += 1
    return h, lo, width


def smooth(h, k=2):
    out = []
    for i in range(len(h)):
        a = max(0, i - k)
        b = min(len(h), i + k + 1)
        out.append(sum(h[a:b]) / (b - a))
    return out


def stage_calibrate(art: Artifact, verbose: bool) -> dict:
    env = [float(v) for v in art.read_envelope()]
    h, lo, width = histogram(env)
    sh = smooth(h)
    total = sum(h) or 1

    # the loud mode is speech; the quiet mode is the noise floor
    peaks = []
    for i in range(1, len(sh) - 1):
        if sh[i] >= sh[i - 1] and sh[i] > sh[i + 1] and sh[i] > total * 0.002:
            peaks.append((i, sh[i]))
    peaks.sort(key=lambda p: -p[1])
    top = sorted(p[0] for p in peaks[:4])

    def db(i):
        return lo + i * width

    speech_i = max(top) if top else len(sh) - 1
    floor_i = min(top) if top else 0

    # threshold at the valley between the two modes
    if floor_i < speech_i:
        valley = min(range(floor_i, speech_i + 1), key=lambda i: sh[i])
    else:
        valley = floor_i
    thr = db(valley)

    vals = sorted(env)
    p = lambda q: vals[min(len(vals) - 1, int(len(vals) * q))]

    cal = {
        "noise_floor_db": db(floor_i),
        "speech_db": db(speech_i),
        "threshold_db": thr,
        "separation_db": db(speech_i) - db(floor_i),
        "p01": p(0.01), "p10": p(0.10), "p50": p(0.50), "p90": p(0.90),
        "modes_db": [db(i) for i in top],
        "below_threshold_fraction": sum(1 for v in env if v <= thr) / len(env),
    }
    print(f"  noise floor {cal['noise_floor_db']:.1f} dB, "
          f"speech {cal['speech_db']:.1f} dB, "
          f"separation {cal['separation_db']:.1f} dB")
    print(f"  threshold set at the valley between them: "
          f"{cal['threshold_db']:.1f} dB "
          f"({cal['below_threshold_fraction']*100:.1f}% of the file is below it)")
    if cal["separation_db"] < 12:
        print("  ! modes barely separate. Heavily compressed or noise-gated "
              "audio;\n    gap detection will be unreliable.")
    return cal


# ==========================================================================
# stage 4 - gaps and their populations
# ==========================================================================

def find_gaps(env, hz, thr_db, min_dur):
    gaps, run = [], None
    for i, v in enumerate(env):
        if v <= thr_db:
            if run is None:
                run = i
        else:
            if run is not None and (i - run) / hz >= min_dur:
                gaps.append((run, i))
            run = None
    if run is not None and (len(env) - run) / hz >= min_dur:
        gaps.append((run, len(env)))
    out = []
    for a, b in gaps:
        seg = env[a:b]
        out.append({"start": a / hz, "end": b / hz,
                    "mid": (a + b) / 2 / hz,
                    "dur": (b - a) / hz,
                    "depth": min(seg) if seg else ENV_FLOOR_DB})
    return out


def valley_split(vals, bins=None, min_members=3, min_prom=0.35):
    """Split a duration distribution at density valleys in log space.

    Variance-based clustering cannot see a rare population: 68 chapter breaks
    among 18,000 sentence pauses contribute almost nothing to total variance,
    so k-means spends all its clusters subdividing the bulk. But a distinct
    population still sits in its own bump with near-empty space beneath it,
    and looking for valleys in the density finds exactly that.
    """
    if len(vals) < min_members * 2:
        return [vals]
    # bins must track sample size: 90 bins over 39 gaps is mostly empty space
    if bins is None:
        bins = max(12, min(120, len(vals) // 3))
    lx = sorted(math.log(v) for v in vals)
    lo, hi = lx[0], lx[-1]
    if hi - lo < 1e-9:
        return [vals]
    w = (hi - lo) / bins
    h = [0] * bins
    for v in lx:
        h[min(bins - 1, int((v - lo) / w))] += 1
    sm = []
    for i in range(bins):
        a, b = max(0, i - 1), min(bins, i + 2)
        sm.append(sum(h[a:b]) / (b - a))

    cuts = []
    for i in range(2, bins - 2):
        if sm[i] <= sm[i - 1] and sm[i] <= sm[i + 1]:
            left = max(sm[:i]) if i else 0.0
            right = max(sm[i + 1:]) if i + 1 < bins else 0.0
            # a valley only counts with a real peak on both sides
            if left > 0 and right > 0 and min(left, right) * min_prom > max(sm[i], 1e-9):
                cuts.append(lo + (i + 0.5) * w)
    if not cuts:
        return _ratio_split(vals, min_members)
    merged = []
    for c in cuts:
        if not merged or c - merged[-1] > 0.25:
            merged.append(c)

    parts, prev = [], -1e9
    for c in merged + [1e9]:
        grp = [v for v in vals if prev < math.log(v) <= c]
        if grp:
            parts.append(grp)
        prev = c
    parts = [p for p in parts if len(p) >= min_members]
    return parts or _ratio_split(vals, min_members)


def _ratio_split(vals, min_members=3, min_jump=1.45):
    """Fallback for small samples: cut at the largest relative jump.

    With only a few dozen gaps a density estimate is too noisy to trust, but
    a clean separation still shows up as one big step in the sorted lengths.
    """
    sv = sorted(vals)
    if len(sv) < min_members * 2:
        return [sv]
    best, at = 1.0, None
    for i in range(min_members, len(sv) - min_members + 1):
        r = sv[i] / max(sv[i - 1], 1e-9)
        if r > best:
            best, at = r, i
    if at is None or best < min_jump:
        return [sv]
    return [sv[:at], sv[at:]]


def classify_gaps(gaps, max_pops=6):
    """Split gaps into natural length populations.

    A produced recording contains distinct kinds of pause: breaths, sentence
    gaps, deliberate chapter breaks, and on a cassette transfer the dead air
    at a side change. Separating them is what makes chapter candidates
    selectable at all.
    """
    if len(gaps) < 4:
        return [{"k": 0, "n": len(gaps), "centre": 1.0, "lo": 0.0,
                 "hi": 1e9, "median": 1.0}]
    parts = valley_split([g["dur"] for g in gaps])
    parts.sort(key=lambda p: statistics.median(p))
    if len(parts) > max_pops:
        # keep the longest populations distinct; they are the useful ones
        head = parts[:len(parts) - max_pops + 1]
        parts = [[v for p in head for v in p]] + parts[len(head):]
        parts.sort(key=lambda p: statistics.median(p))

    pops = []
    for k, vals in enumerate(parts):
        sv = sorted(vals)
        pops.append({"k": k, "n": len(sv),
                     "centre": statistics.median(sv),
                     "lo": sv[0], "hi": sv[-1],
                     "median": statistics.median(sv)})
    bounds = [(p["lo"], p["hi"]) for p in pops]
    for g in gaps:
        g["pop"] = next((i for i, (a, b) in enumerate(bounds)
                         if a <= g["dur"] <= b),
                        min(range(len(pops)),
                            key=lambda i: abs(g["dur"] - pops[i]["centre"])))
    return pops


def stage_gaps(art: Artifact, min_dur: float, verbose: bool) -> dict:
    env = [float(v) for v in art.read_envelope()]
    cal = art.require("calibration", "chapterfit.py measure FILE")
    hz = art.require("envelope", "chapterfit.py measure FILE")["hz"]
    gaps = find_gaps(env, hz, cal["threshold_db"], min_dur)
    if len(gaps) > 4000:
        print(f"  {len(gaps)} gaps - clustering, this takes a moment...")
    print(f"  {len(gaps)} gaps of {min_dur}s or longer "
          f"({len(gaps)/(len(env)/hz/3600):.0f} per hour)")
    if not gaps:
        return {"min_dur": min_dur, "gaps": [], "populations": []}
    pops = classify_gaps(gaps)
    print(f"\n  {'pop':>4} {'count':>7} {'typical':>9} {'range':>16}  role")
    for p in pops:
        role = describe_population(p, pops, len(gaps))
        print(f"  {p['k']:>4} {p['n']:>7} {p['centre']:>8.2f}s "
              f"{p['lo']:>6.2f}-{p['hi']:<7.2f}s  {role}")
        p["role"] = role
    return {"min_dur": min_dur,
            "threshold_db": cal["threshold_db"],
            "gaps": gaps, "populations": pops}


def describe_population(p, pops, total):
    """Label by role, judged on rarity and how far it stands off the rest."""
    share = p["n"] / max(total, 1)
    is_last = p["k"] == len(pops) - 1
    ratio = 1.0
    if p["k"] > 0:
        ratio = p["centre"] / max(pops[p["k"] - 1]["centre"], 1e-9)
    if is_last and len(pops) > 1 and share < 0.35 and ratio >= 2.0:
        # could be either; only a confirmed period settles it, and that is
        # decided later in the structure stage
        return "rare and much longer - chapter breaks or side changes"
    if share > 0.5:
        return "the bulk - sentence and breath pauses"
    if p["k"] == 0 and len(pops) > 1:
        return "shortest - breaths"
    return "candidate chapter breaks"


# ==========================================================================
# stage 5 - structural fingerprint
# ==========================================================================

def find_period(gaps, dur, min_p=18 * 60, max_p=70 * 60, tol=90.0,
                median_dur=None, max_candidates=80):
    """Is there a repeating long gap, i.e. a cassette side change?

    Bounded deliberately: the pair-voting is quadratic, so it only ever sees
    the longest few dozen gaps. A side change is among the longest things in
    the file by definition, so nothing useful is lost, and handing it several
    thousand ordinary pauses would take effectively forever.
    """
    if len(gaps) < 3:
        return None
    if len(gaps) > max_candidates:
        gaps = sorted(gaps, key=lambda g: -g["dur"])[:max_candidates]
        gaps.sort(key=lambda g: g["mid"])
    # Vote on the period implied by each pair of long gaps, then test only
    # the periods that actually attracted votes. Testing every distinct value
    # is what made this unusable on a long file.
    votes = {}
    BIN = 2.0
    for i in range(len(gaps)):
        for j in range(i + 1, len(gaps)):
            d = gaps[j]["mid"] - gaps[i]["mid"]
            for n in range(1, 12):
                pp = d / n
                if min_p <= pp <= max_p:
                    votes[int(pp / BIN)] = votes.get(int(pp / BIN), 0) + 1
    if not votes:
        return None
    top = sorted(votes.items(), key=lambda kv: -kv[1])[:24]
    periods = [(k + 0.5) * BIN for k, _ in top]

    med = median_dur if median_dur is not None else \
        statistics.median(g["dur"] for g in gaps)
    seeds = sorted(gaps, key=lambda g: -g["dur"])[:20]

    best = None
    for pp in periods:
        sides = int(round(dur / pp))
        if sides < 3:
            continue
        for seed in seeds:
            phase = seed["mid"] - round(seed["mid"] / pp) * pp
            matched, err, picks, durs = 0, 0.0, [], []
            for k in range(1, sides):
                target = k * pp + phase
                if not (0 < target < dur):
                    continue
                hit, hd, hs = None, tol, -1.0
                for g in gaps:
                    dd = abs(g["mid"] - target)
                    if dd > tol:
                        continue
                    sc = g["dur"] / (1 + dd / tol)
                    if sc > hs:
                        hit, hd, hs = g, dd, sc
                if hit:
                    matched += 1
                    err += hd
                    picks.append(hit["mid"])
                    durs.append(hit["dur"])
            exp = max(1, sides - 1)
            if matched < 3 or matched < exp * 0.5:
                continue
            if statistics.fmean(durs) < med * 1.5:
                continue
            score = (matched / exp, -err / matched)
            if best is None or score > best["score"]:
                best = {"score": score, "period": pp, "sides": sides,
                        "matched": matched, "expected": exp,
                        "mean_error": err / matched, "breaks": picks}
    return best


def stage_fingerprint(art: Artifact, verbose: bool,
                      side_min=None, side_max=None) -> dict:
    gs = art.require("gaps", "chapterfit.py measure FILE")
    gaps, pops = gs["gaps"], gs["populations"]
    dur = art.data["envelope"]["duration"]
    fp = {"duration": dur, "n_gaps": len(gaps),
          "gaps_per_hour": len(gaps) / max(dur / 3600, 1e-9),
          "populations": len(pops)}

    longest = max((g["dur"] for g in gaps), default=0.0)
    fp["longest_gap"] = longest
    fp["degapped"] = longest < 6.0
    if fp["degapped"]:
        print(f"  longest gap in the whole file is {longest:.2f}s")
        print("  -> nothing long enough to be a cassette side change, which\n"
              "     normally leaves 5-15s of dead air. Either this is a digital\n"
              "     source, or the joins were trimmed when the sides were\n"
              "     assembled - in which case expect a small timing step at each.")

    tail = [g for g in gaps if g["pop"] == len(pops) - 1] if pops else []
    print("  searching for a repeating side-change interval...")
    if verbose:
        print(f"  period search over the {min(len(tail), 80)} longest of "
              f"{len(tail)} candidates")
    lo = (side_min * 60.0) if side_min else max(300.0, dur / 60.0)
    hi = (side_max * 60.0) if side_max else min(70 * 60.0, dur / 2.5)
    all_med = (statistics.median(g["dur"] for g in gaps) if gaps else 0.0)
    per = (find_period(tail, dur, lo, hi, median_dur=all_med)
           if len(tail) >= 3 and hi > lo else None)
    fp["tape"] = None
    if per:
        fp["tape"] = {k: per[k] for k in
                      ("period", "sides", "matched", "expected",
                       "mean_error", "breaks")}
        for p_ in pops:
            if p_["k"] == len(pops) - 1:
                p_["role"] = "side changes (repeating period confirmed)"
        art.data["gaps"]["populations"] = pops
        print(f"  repeating long gap every {hms(per['period'])} "
              f"({per['period']/60:.1f} min), {per['sides']} sides "
              f"(~{per['sides']/2:.0f} cassettes)")
        if 15 * 60 <= per["period"] <= 70 * 60:
            nominal = min((60, 90, 120), key=lambda n: abs(per["period"] - n * 30))
            print(f"  closest standard cassette: C{nominal} "
                  f"({nominal//2} min/side, {100*per['period']/(nominal*30):.0f}% used)")
    elif not fp["degapped"]:
        print("  no repeating long gap - not a cassette transfer, or the "
              "joins were trimmed")
    return fp
