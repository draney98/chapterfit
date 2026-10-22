"""
chapterfit - locate audiobook chapters by measurement rather than by search
(the envelope pipeline CLI).

Run as the `chapterfit` console script, `python -m chaptertools.cli.chapterfit`,
or the root chapterfit.py shim (chapterbook.sh). Also the historical import
surface: `import chapterfit` re-exports the stage helpers below.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

from chaptertools import __version__
from chaptertools.abs_client import (  # noqa: E402
    ABS,
    ENV_KEYS,
    cmd_finalize,
    finalize_done,
    item_is_correct,
    load_env,
    mask,
    wait_for_scan,
)
from chaptertools.asr import (  # noqa: E402
    ClipCache,
    Transcriber,
    cmd_listen,
    cmd_sweep,
    cmd_transcripts,
    collect_transcripts,
    confirm_pin,
    listen_candidates,
    locate_heading,
    match_headings,
    parse_heading,
    parse_headings,
    segments_text,
    sweep_span,
    words_to_number,
)
from chaptertools.audnex import AUDNEX_BASE, flatten_chapters  # noqa: E402
from chaptertools.envelope.alignment import (  # noqa: E402
    chance_rate,
    posteriors,
    score_population,
    solve,
    stage_align,
    suggest_measurements,
)
from chaptertools.envelope.artifact import (  # noqa: E402
    ENV_FLOOR_DB,
    ENVELOPE_HZ,
    SCHEMA,
    Artifact,
)
from chaptertools.envelope.emit import (  # noqa: E402
    alignment_fingerprint,
    select_chapters,
)
from chaptertools.envelope.identification import (  # noqa: E402
    candidate_sets,
    coarse_fit,
    fetch_chapters,
    stage_identify,
)
from chaptertools.envelope.measurement import (  # noqa: E402
    classify_gaps,
    decoded_duration,
    describe_population,
    extract_envelope,
    find_gaps,
    find_period,
    histogram,
    probe,
    smooth,
    stage_calibrate,
    stage_fingerprint,
    stage_gaps,
    stage_measure,
    stage_verify,
    valley_split,
)
from chaptertools.output import embed, merge_tags, read_tags  # noqa: E402
from chaptertools.util import (  # noqa: E402
    clock,
    die,
    hms,
    logsumexp,
    need,
    normalize_work_dir,
    parse_time,
    q as _q,
)

# chapterfit historically exposed AUDNEX (same host)
AUDNEX = AUDNEX_BASE

# Re-export helpers some cmd_* expect under historical names
from chaptertools.envelope import measurement as _measurement  # noqa: E402

def print_alignment(art: Artifact, al, limit=None):
    res = al["results"]
    gaps = {round(g["mid"], 3): g for g in art.data["gaps"]["gaps"]}
    print(f"\n  {'#':>3} {'P':>1} {'published':>12} {'placed':>12} "
          f"{'gap':>7} {'conf':>6} {'+/-':>8} {'resid':>9}  title")
    print("  " + "-" * 104)
    shown = res if not limit else res[:limit]
    for r in shown:
        g = gaps.get(round(r["time"], 3)) if r["time"] is not None else None
        gap_s = f"{g['dur']:.2f}s" if g else "-"
        conf_s = f"{r['confidence']:.2f}"
        spread_s = f"{r['spread']:.1f}s" if r["spread"] is not None else "-"
        resid_s = (f"{r['residual']:+.1f}s" if r.get("residual") is not None
                   else "-")
        mark = "P" if r["pinned"] else " "
        flag = " " if r["confidence"] >= al["min_confidence"] else "?"
        print(f" {flag}{r['index']+1:>3} {mark:>1} {hms(r['published']):>12} "
              f"{hms(r['time']):>12} {gap_s:>7} {conf_s:>6} {spread_s:>8} "
              f"{resid_s:>9}  {r['title'][:30]}")
    if limit and len(res) > limit:
        print(f"  ... {len(res)-limit} more (use --all)")
    print("\n  published = from the chapter data.  placed = where the audio "
          "says it is.")
    print("  gap = length of the silence chosen.  conf = posterior; "
          "+/- = spread over\n  the alternatives.  resid = how far the placement "
          "sits from the coarse model,")
    print("  which should walk slowly rather than jump.")


# ==========================================================================
# CLI
# ==========================================================================

def cmd_measure(args):
    need("ffmpeg"); need("ffprobe")
    art = Artifact(args.audio, args.work_dir)
    redo = set(args.redo or [])
    if "all" in redo:
        fp = art.data.get("file")
        art.data = {"schema": SCHEMA, "audio": art.audio, "file": fp}
    print(f"\n=== chapterfit {__version__} - measure "
          f"{os.path.basename(art.audio)} ===")

    concat = getattr(args, "concat", None) or None
    if concat and not os.path.isfile(concat):
        die(f"no such concat list: {concat}")

    # ---- envelope (the only expensive step) -----------------------------
    env_sig = {"hz": args.hz}
    if concat:
        env_sig["concat"] = os.path.abspath(concat)
    have_env = (art.valid("envelope", env_sig)
                and os.path.exists(art.env_path)
                and "envelope" not in redo)
    print("\n[1/5] envelope")
    if have_env:
        e = art.data["envelope"]
        print(f"  cached: {e['frames']} frames at {e['hz']} Hz "
              f"({e['duration']/3600:.2f} h) - skipping extraction")
    else:
        art.put("envelope",
                stage_measure(art, args.hz, args.verbose, concat=concat),
                env_sig)
        # anything derived from the envelope is now stale
        for k in ("calibration", "gaps", "fingerprint",
                  "identification", "alignment"):
            art.data.pop(k, None)
    env_dur = art.data["envelope"]["duration"]

    # ---- verify, using the duration the envelope already established ----
    print("\n[2/5] verify timeline")
    src_sig = {"env": round(env_dur, 3)}
    if concat:
        src_sig["concat"] = os.path.abspath(concat)
    if art.valid("source", src_sig) and "verify" not in redo:
        sinfo = art.data["source"]
        print(f"  cached: declared {hms(sinfo['declared_duration'])} vs "
              f"{hms(sinfo['decoded_duration'])} measured "
              f"({sinfo['header_error_sec']:+.2f}s)")
        if not sinfo.get("timeline_trustworthy"):
            print("  ! header duration is wrong - see the earlier warning")
    else:
        art.put("source",
                stage_verify(art, args.verbose, env_dur, concat=concat),
                src_sig)

    # ---- calibration ----------------------------------------------------
    print("\n[3/5] calibrate")
    cal_sig = {"env": round(env_dur, 3), "hz": args.hz}
    if art.valid("calibration", cal_sig) and "calibrate" not in redo:
        c = art.data["calibration"]
        print(f"  cached: floor {c['noise_floor_db']:.1f} dB, speech "
              f"{c['speech_db']:.1f} dB, threshold {c['threshold_db']:.1f} dB")
    else:
        art.put("calibration", stage_calibrate(art, args.verbose), cal_sig)
        for k in ("gaps", "fingerprint", "identification", "alignment"):
            art.data.pop(k, None)

    # ---- gaps -----------------------------------------------------------
    print("\n[4/5] gaps and populations")
    thr = art.data["calibration"]["threshold_db"]
    gap_sig = {"min_gap": args.min_gap, "thr": thr, "hz": args.hz}
    if art.valid("gaps", gap_sig) and "gaps" not in redo:
        g = art.data["gaps"]
        print(f"  cached: {len(g['gaps'])} gaps in "
              f"{len(g['populations'])} population(s)")
        for p in g["populations"]:
            print(f"     pop {p['k']}: {p['n']:>6} gaps, ~{p['centre']:.2f}s  "
                  f"{p.get('role','')}")
    else:
        art.put("gaps", stage_gaps(art, args.min_gap, args.verbose), gap_sig)
        for k in ("fingerprint", "identification", "alignment"):
            art.data.pop(k, None)

    # ---- structure ------------------------------------------------------
    print("\n[5/5] structure")
    fp_sig = {"gaps": len(art.data["gaps"]["gaps"]),
              "side_min": args.side_min, "side_max": args.side_max}
    if art.valid("fingerprint", fp_sig) and "structure" not in redo:
        f = art.data["fingerprint"]
        print(f"  cached: longest gap {f.get('longest_gap', 0):.2f}s"
              + (f", tape side every {hms(f['tape']['period'])}"
                 if f.get("tape") else ""))
    else:
        art.put("fingerprint",
                stage_fingerprint(art, args.verbose, args.side_min,
                                  args.side_max), fp_sig)
        for k in ("identification", "alignment"):
            art.data.pop(k, None)

    art.save()
    print(f"\nwrote {art.path}")
    print("next:  chapterfit.py identify "
          f"{os.path.basename(art.audio)!r} --asin ASIN")


def cmd_identify(args):
    art = Artifact(args.audio, args.work_dir)
    art.require("gaps", "chapterfit.py measure FILE")
    print(f"\n=== identify ===")
    if args.chapters_json:
        with open(args.chapters_json) as f:
            payload = json.load(f)
        label = args.chapters_json
    elif args.asin:
        payload = fetch_chapters(args.asin, args.region,
                                 os.path.dirname(art.path),
                                 args.offline)
        label = f"Audnexus {args.asin}"
    else:
        die("need --asin or --chapters-json")
    chapters, meta = flatten_chapters(payload)
    if not chapters:
        die("no chapters in that data")
    art.data["max_offset"] = args.max_offset
    art.data["identification"] = stage_identify(
        art, chapters, meta, label, args.tolerance, args.verbose)
    art.data.pop("alignment", None)
    art.save()
    print(f"\nnext:  chapterfit.py align {os.path.basename(art.audio)!r}")


def cmd_align(args):
    art = Artifact(args.audio, args.work_dir)
    print(f"\n=== align ===")
    al = stage_align(art, args)
    art.data["alignment"] = al
    art.save()
    print_alignment(art, al, None if args.all else 25)
    nxt = suggest_measurements(art, al, args.suggest)
    weak = [r for r in al["results"]
            if r["time"] is None or r["confidence"] < 0.9]
    if nxt and weak:
        print("\n--- measure these to improve the fit ---\n")
        print("  Listen at each placed time and note where the chapter really")
        print("  starts. These are chosen to reduce uncertainty the most.\n")
        lead = args.lead_in
        for r in nxt:
            print(f"    ch {r['index']+1:<4} placed {hms(r['time']):>13}  "
                  f"conf {r['confidence']:.2f}  +/-{r['spread'] or 0:.0f}s  "
                  f"\"{r['title'][:38]}\"")
            print(f"         mpv --start={clock(r['time'] - lead)} "
                  f"{_q(art.audio)}")
            print(f"         (starts {lead:.0f}s early; the break should fall "
                  f"at {clock(r['time'])})")
        pins = " ".join(f"--at {r['index']+1}=<time>" for r in nxt)
        print(f"\n  Note where each chapter really starts, then:")
        print(f"    chapterfit.py pin {_q(art.audio)} {pins}")
        print(f"    chapterfit.py align {_q(art.audio)}")


def cmd_pin(args):
    art = Artifact(args.audio, args.work_dir)
    art.require("identification", "chapterfit.py identify FILE --asin ASIN")
    pins = art.data.get("pins") or {}
    n_ch = len(art.data["identification"]["chapters"])
    for spec in args.at:
        if "=" not in spec:
            die(f"--at wants CHAPTER=TIME, got {spec!r}")
        k, v = spec.split("=", 1)
        try:
            i = int(k)
        except ValueError:
            die(f"{k!r} is not a chapter number")
        if not 1 <= i <= n_ch:
            die(f"chapter {i} out of range 1..{n_ch}")
        pins[str(i - 1)] = parse_time(v)
        print(f"  pinned chapter {i} at {hms(parse_time(v))}")
    for spec in args.remove:
        pins.pop(str(int(spec) - 1), None)
        print(f"  removed pin on chapter {spec}")
    art.data["pins"] = pins
    art.data.pop("alignment", None)
    art.save()
    print(f"\n{len(pins)} pin(s). Re-run:  chapterfit.py align "
          f"{os.path.basename(art.audio)!r}")


def cmd_status(args):
    art = Artifact(args.audio, args.work_dir)
    d = art.data
    print(f"\n=== {os.path.basename(art.audio)} "
          f"(chapterfit {__version__}) ===")
    print(f"artifact : {art.path}")
    stages = [("source", "timeline verified"), ("envelope", "envelope"),
              ("calibration", "calibrated"), ("gaps", "gaps classified"),
              ("fingerprint", "structure"), ("identification", "edition"),
              ("alignment", "alignment")]
    for key, name in stages:
        print(f"  {'[x]' if d.get(key) else '[ ]'} {name}")
    if d.get("source"):
        s = d["source"]
        print(f"\nduration : {hms(s.get('decoded_duration'))} decoded"
              + ("" if s.get("timeline_trustworthy")
                 else f"  ! header off by {s['header_error_sec']:+.1f}s"))
    if d.get("calibration"):
        c = d["calibration"]
        print(f"levels   : floor {c['noise_floor_db']:.1f} dB, speech "
              f"{c['speech_db']:.1f} dB, threshold {c['threshold_db']:.1f} dB")
    if d.get("gaps"):
        print(f"gaps     : {len(d['gaps']['gaps'])} in "
              f"{len(d['gaps']['populations'])} population(s)")
        for p in d["gaps"]["populations"]:
            print(f"             pop {p['k']}: {p['n']:>5} gaps, "
                  f"~{p['centre']:.2f}s   {p.get('role','')}")
    if d.get("fingerprint") and d["fingerprint"].get("tape"):
        t = d["fingerprint"]["tape"]
        print(f"tape     : side every {hms(t['period'])}, {t['sides']} sides")
    if d.get("pins"):
        print(f"pins     : " + ", ".join(
            f"ch{int(k)+1}@{hms(v)}" for k, v in sorted(
                d["pins"].items(), key=lambda x: int(x[0]))))
    env_ok = d.get("envelope") and os.path.exists(art.env_path)
    print(f"\ncached   : envelope {'yes' if env_ok else 'NO - will re-extract'}"
          + (f" ({d['envelope']['frames']} frames)" if env_ok else ""))
    cached = [n for n in ("source", "calibration", "gaps", "fingerprint")
              if d.get(n)]
    print(f"           stages held: {', '.join(cached) or 'none'}")
    ch = [f for f in os.listdir(os.path.dirname(art.path) or ".")
          if f.startswith("chapters.") and f.endswith(".json")] \
        if os.path.isdir(os.path.dirname(art.path) or ".") else []
    if ch:
        print(f"           chapter data: {', '.join(sorted(ch)[:4])}")
    if d.get("alignment"):
        al = d["alignment"]
        good = sum(1 for r in al["results"]
                   if r["confidence"] >= al["min_confidence"])
        print(f"aligned  : {good}/{len(al['results'])} chapters above "
              f"confidence {al['min_confidence']}")


def cmd_emit(args):
    media = args.audio
    source = getattr(args, "source", None) or media
    if source != media and not os.path.isfile(source):
        die(f"no such file: {source}")
    art = Artifact(source, args.work_dir)
    al = art.data.get("alignment")
    if not (al or {}).get("results"):
        # Seed from album.nfo / pins so Finish Apply works without a prior Align.
        from chaptertools.gui.stages import placement_rows, seed_alignment
        from chaptertools.nfo import read_nfo_chapters
        nfo = read_nfo_chapters(
            os.path.join(os.path.dirname(source), "album.nfo"))
        thr_seed = args.min_confidence or 0.5
        seeded = seed_alignment(placement_rows(art.data, nfo), thr_seed)
        if seeded is None:
            art.require("alignment", "chapterfit.py align FILE")
        art.data["alignment"] = seeded
        art.save()
        al = seeded
        print("  seeded alignment from album.nfo / placed chapters")
    dur = art.require("envelope", "chapterfit.py measure FILE")["duration"]
    thr = args.min_confidence or al["min_confidence"]
    dedup, _withheld = select_chapters(al["results"], thr)
    if dedup:
        dedup[0]["time"] = 0.0
    dropped = len(al["results"]) - len(dedup)
    print(f"\n  emitting {len(dedup)} chapters at confidence >= {thr:.2f}"
          + (f"; {dropped} withheld as unsupported" if dropped else ""))
    if dropped:
        print("  A chapter mark in the wrong place is worse than none.")

    # global tags: keep whatever the original carries unless overridden
    overrides = {"title": args.title, "artist": args.author,
                 "album": args.album, "date": args.year,
                 "genre": args.genre, "comment": args.comment}
    tags = merge_tags(read_tags(media), overrides)
    if tags:
        print("  tags: " + ", ".join(f"{k}={v[:40]}" for k, v in tags.items()))

    out = []
    for i, r in enumerate(dedup):
        end = dedup[i + 1]["time"] if i + 1 < len(dedup) else dur
        out.append({"id": i, "start": round(r["time"], 3),
                    "end": round(end, 3), "title": r["title"]})

    art.data["emitted"] = {
        "count": len(out),
        "first": out[0]["start"] if out else None,
        "last": out[-1]["start"] if out else None,
        "min_confidence": thr,
        "tags": tags,
        "alignment_sig": alignment_fingerprint(al),
    }
    art.save()

    if args.abs_json:
        with open(args.abs_json, "w") as f:
            json.dump(out, f, indent=2)
        print(f"  wrote {args.abs_json}")
    embed_dest = getattr(args, "embed_dest", None)
    if args.ffmetadata or args.embed or embed_dest:
        lines = [";FFMETADATA1"]
        for k, v in tags.items():
            lines.append(f"{k}={v}")
        for c in out:
            lines += ["[CHAPTER]", "TIMEBASE=1/1000",
                      f"START={int(c['start']*1000)}",
                      f"END={int(c['end']*1000)}",
                      f"title={c['title']}", ""]
        meta = "\n".join(lines)
        if args.ffmetadata:
            with open(args.ffmetadata, "w") as f:
                f.write(meta)
            print(f"  wrote {args.ffmetadata}")
        if args.embed or embed_dest:
            dest = embed_dest or (os.path.splitext(media)[0]
                                  + ".chapters" + os.path.splitext(media)[1])
            tmp = dest + ".ffmeta.tmp"
            with open(tmp, "w") as f:
                f.write(meta)
            try:
                embed(media, tmp, dest)
            finally:
                os.unlink(tmp)
            print(f"  wrote {dest}")
    if args.chapters_txt:
        with open(args.chapters_txt, "w") as f:
            for c in out:
                t = c["start"]
                h, rem = divmod(int(t), 3600)
                m, sec = divmod(rem, 60)
                f.write(f"{h}:{m:02d}:{sec:02d} {c['title']}\n")
        print(f"  wrote {args.chapters_txt}  "
              f"(timestamp-per-line, for bulk paste)")

    if args.split:
        os.makedirs(args.split, exist_ok=True)
        ext = os.path.splitext(media)[1] or ".mp3"
        width = max(2, len(str(len(out))))
        print(f"  splitting into {args.split}/ ...")
        for i, c in enumerate(out, 1):
            safe = re.sub(r'[/\\:*?"<>|]', "-", c["title"]).strip()
            safe = re.sub(r"\s+", " ", safe)[:110] or f"Chapter {i}"
            name = f"{str(i).zfill(width)} - {safe}{ext}"
            dest = os.path.join(args.split, name)
            r = subprocess.run(
                ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", f"{c['start']:.3f}", "-to", f"{c['end']:.3f}",
                 "-i", media, "-c", "copy",
                 "-metadata", f"title={c['title']}",
                 "-metadata", f"track={i}/{len(out)}", dest],
                capture_output=True, text=True)
            if r.returncode:
                die(f"split failed on chapter {i}: {r.stderr[-300:]}")
            print(f"    {name}")
        print(f"""
  Stream-copied, so no re-encode. mp3 cuts land on frame boundaries, which
  can be a few tens of milliseconds off - inaudible, but it is why embedding
  is the more faithful option.""")

    if not (args.abs_json or args.ffmetadata or args.embed
            or getattr(args, "embed_dest", None) or args.split
            or args.chapters_txt):
        print("  (nothing written - pass --abs-json, --ffmetadata or --embed)")


# ==========================================================================
# finalize - put the chaptered file in place and make Audiobookshelf see it
# ==========================================================================



def main():
    ap = argparse.ArgumentParser(
        description="Locate audiobook chapters by measurement.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work-dir", help="where to keep the analysis artifact "
                                       "(default: $CHAPTERFIT_WORK/<author>/"
                                       "<book>, CHAPTERFIT_WORK defaulting to "
                                       "~/.local/share/chapterfit)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("measure", help="verify, envelope, calibrate, classify")
    p.add_argument("audio")
    p.add_argument("--hz", type=int, default=ENVELOPE_HZ,
                   help=f"envelope resolution (default {ENVELOPE_HZ} Hz)")
    p.add_argument("--min-gap", type=float, default=0.4,
                   help="shortest gap to record, seconds (default 0.4)")
    p.add_argument("--side-min", type=float,
                   help="shortest tape side to look for, minutes")
    p.add_argument("--side-max", type=float,
                   help="longest tape side to look for, minutes")
    p.add_argument("--concat", metavar="LIST",
                   help="ffmpeg concat demuxer list of section files "
                        "(LibriVox playlist); envelope spans the whole book")
    p.add_argument("--redo", action="append", default=[],
                   choices=["envelope", "verify", "calibrate", "gaps",
                            "structure", "all"],
                   help="force a stage to recompute even if cached; "
                        "repeatable")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_measure)

    p = sub.add_parser("identify", help="test a chapter list for compatibility")
    p.add_argument("audio")
    p.add_argument("--asin")
    p.add_argument("--region", default="us")
    p.add_argument("--chapters-json")
    p.add_argument("--offline", action="store_true",
                   help="use only cached chapter data, never the network")
    p.add_argument("--tolerance", type=float, default=6.0)
    p.add_argument("--max-offset", type=float, default=900.0)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_identify)

    p = sub.add_parser("align", help="solve for chapter positions")
    p.add_argument("audio")
    p.add_argument("--tolerance", type=float, default=6.0)
    p.add_argument("--window", type=float, default=240.0,
                   help="how far from the coarse prediction to look (default 240s)")
    p.add_argument("--rate-penalty", type=float, default=25.0,
                   help="cost weight on local rate deviation (default 25)")
    p.add_argument("--max-offset", type=float, default=900.0,
                   help="largest plausible shift from published times (default 900s)")
    p.add_argument("--skip-penalty", type=float, default=3.0,
                   help="cost of leaving a chapter unplaced")
    p.add_argument("--near", metavar="CH=TIME",
                   help="roughly where a chapter starts, e.g. --near 3=43:47. "
                        "Not a pin - it only aims the search, and the solver "
                        "still picks the exact gap.")
    p.add_argument("--near-window", type=float, default=180.0,
                   help="how far either side of the hinted position to look "
                        "(default 180s)")
    p.add_argument("--min-confidence", type=float, default=0.5)
    p.add_argument("--suggest", type=int, default=2)
    p.add_argument("--lead-in", type=float, default=3.0,
                   help="seconds before the placed time to start mpv "
                        "(default 3)")
    p.add_argument("--all", action="store_true", help="print every chapter")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_align)

    p = sub.add_parser("pin", help="record a chapter position you measured")
    p.add_argument("audio")
    p.add_argument("--at", action="append", default=[], metavar="CH=TIME")
    p.add_argument("--remove", action="append", default=[], metavar="CH")
    p.set_defaults(func=cmd_pin)

    p = sub.add_parser("finalize",
                       help="stage away the original, then make ABS reload it")
    p.add_argument("audio")
    p.add_argument("--chaptered", help="the embedded-chapter file "
                                       "(default <name>.chapters.<ext>)")
    p.add_argument("--stage", help="where displaced files go "
                                   "(default <work-dir>/originals)")
    p.add_argument("--env", help="path to a .env with ABS_URL / ABS_TOKEN")
    p.add_argument("--library-id")
    p.add_argument("--library-name")
    p.add_argument("--item-id", help="skip lookup and use this library item")
    p.add_argument("--group", help="chgrp the final file to this group "
                                   "(e.g. audiobookshelf)")
    p.add_argument("--apply", action="store_true",
                   help="actually move files and call the API")
    p.add_argument("--allow-delete", action="store_true",
                   help="permit the delete-and-re-add last resort, which "
                        "clears listening progress")
    p.add_argument("--push-chapters", action="store_true",
                   help="write chapters over the API if ABS ends up with none")
    p.add_argument("--no-chapters", action="store_true",
                   help="do not require ABS to show chapters")
    p.add_argument("--timeout", type=int, default=900,
                   help="seconds to wait for a library scan (default 900)")
    p.add_argument("-y", "--yes", action="store_true",
                   help="answer yes to prompts, including replacing "
                        "existing album.nfo chapter tracks")
    p.add_argument("--force-nfo", action="store_true",
                   help="replace existing album.nfo chapter tracks "
                        "without asking")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_finalize)

    p = sub.add_parser("listen",
                       help="transcribe just after each candidate gap and read "
                            "the chapter announcements")
    p.add_argument("audio")
    p.add_argument("--model", default="base",
                   help="whisper model: tiny, base, small, medium (default base)")
    p.add_argument("--device", default="auto")
    p.add_argument("--compute-type", default="int8")
    p.add_argument("--model-dir",
                   help="directory holding a pre-downloaded model, for "
                        "machines that cannot reach huggingface")
    p.add_argument("--language", default="en")
    p.add_argument("--transcribe-cmd",
                   help="external command that prints a transcript for a wav "
                        "path, instead of faster-whisper")
    p.add_argument("--pre", type=float, default=1.0,
                   help="seconds before the end of the gap (default 1)")
    p.add_argument("--post", type=float, default=12.0,
                   help="seconds after the end of the gap (default 12)")
    p.add_argument("--max-candidates", type=int, default=400)
    p.add_argument("--min-gap", type=float,
                   help="only listen at gaps this long or longer")
    p.add_argument("--all-gaps", action="store_true",
                   help="ignore the identified candidate set and use the "
                        "longest gaps instead")
    p.add_argument("--strategy", choices=["adaptive", "all"], default="adaptive",
                   help="adaptive listens at predicted positions until it has "
                        "enough anchors; all transcribes every candidate")
    p.add_argument("--confirm", type=int, default=12,
                   help="how many confirmed chapters adaptive aims for "
                        "(default 12)")
    p.add_argument("--seeds", type=int, default=6,
                   help="positions to sample before fitting (default 6)")
    p.add_argument("--predict-window", type=float, default=300.0,
                   help="how far from a predicted position to look for a gap "
                        "(default 300s)")
    p.add_argument("--max-announce-lag", type=float, default=4.0,
                   help="how far after a gap the announcement may be spoken "
                        "and still be treated as that gap's break "
                        "(default 4s)")
    p.add_argument("--accept-suspect", action="store_true",
                   help="use announcements that fall too far from their gap")
    p.add_argument("--title-slack", type=float, default=0.0)
    p.add_argument("--pin", action="store_true",
                   help="write what was heard in as pins")
    p.add_argument("-y", "--yes", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_listen)

    p = sub.add_parser("sweep",
                       help="transcribe a continuous range and search it for "
                            "chapter announcements (no silence needed)")
    p.add_argument("audio")
    p.add_argument("--start", help="range start, e.g. 8:00:00")
    p.add_argument("--end", help="range end")
    p.add_argument("--chunk", type=float, default=600.0,
                   help="seconds per transcription chunk (default 600)")
    p.add_argument("--model", default="base")
    p.add_argument("--device", default="auto")
    p.add_argument("--compute-type", default="int8")
    p.add_argument("--model-dir")
    p.add_argument("--language", default="en")
    p.add_argument("--transcribe-cmd")
    p.add_argument("--pin", action="store_true")
    p.add_argument("-y", "--yes", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_sweep)

    p = sub.add_parser("transcripts",
                       help="print everything transcribed so far, timestamped")
    p.add_argument("audio")
    p.add_argument("--grep", metavar="REGEX",
                   help="only lines matching this, e.g. --grep chapter")
    p.add_argument("--words", action="store_true",
                   help="one line per word with its own timestamp")
    p.add_argument("--out", metavar="FILE", help="write to a file instead")
    p.set_defaults(func=cmd_transcripts)

    p = sub.add_parser("version", help="print the version and exit")
    p.add_argument("audio", nargs="?")
    p.set_defaults(func=lambda a: None)

    p = sub.add_parser("status", help="show the artifact")
    p.add_argument("audio")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("emit", help="write the supported chapters")
    p.add_argument("audio")
    p.add_argument("--min-confidence", type=float)
    p.add_argument("--title", help="global ID3 title for the chaptered file")
    p.add_argument("--author", help="global ID3 artist")
    p.add_argument("--album", help="global ID3 album")
    p.add_argument("--year", help="global ID3 date/year")
    p.add_argument("--genre", help="global ID3 genre")
    p.add_argument("--comment", help="global ID3 comment")
    p.add_argument("--abs-json", metavar="FILE")
    p.add_argument("--ffmetadata", metavar="FILE")
    p.add_argument("--source", metavar="FILE",
                   help="book the alignment belongs to, when embedding "
                        "a different audio file")
    p.add_argument("--embed", action="store_true",
                   help="write a chaptered copy of the audio")
    p.add_argument("--embed-dest", metavar="DEST",
                   help="path for that copy "
                        "(default: <name>.chapters.<ext>)")
    p.add_argument("--split", metavar="DIR",
                   help="write one file per chapter into DIR")
    p.add_argument("--chapters-txt", metavar="FILE",
                   help="write 'H:MM:SS Title' lines for pasting into a "
                        "chapter editor")
    p.set_defaults(func=cmd_emit)

    lv = sub.add_parser("librivox", help="search and fetch LibriVox books")
    lv_sub = lv.add_subparsers(dest="lv_cmd", required=True)
    lv_search = lv_sub.add_parser(
        "search", help="search finished and unfinished projects")
    lv_search.add_argument("--title", required=True,
                           help="title prefix (anchored at the start)")
    lv_search.add_argument("--author", help="author last-name query")
    lv_search.add_argument("--json", action="store_true",
                           help="one JSON object on stdout")
    lv_search.set_defaults(func=_cmd_librivox_search)
    lv_fetch = lv_sub.add_parser(
        "fetch", help="download one Archive bitrate set into the library")
    lv_fetch.add_argument("id", help="LibriVox catalog id")
    lv_fetch.add_argument("--library", required=True, metavar="ROOT",
                          help="library root (author/title folders)")
    lv_fetch.add_argument("--set", dest="set", metavar="NAME",
                          help="Archive set name (default: 128Kbps MP3, …)")
    lv_fetch.add_argument("--plan-only", action="store_true",
                          help="emit plan or refused; download nothing")
    lv_fetch.add_argument("--json", action="store_true",
                          help="one JSON event object per line")
    lv_fetch.set_defaults(func=_cmd_librivox_fetch)

    args = ap.parse_args()
    if args.cmd == "version":
        print(f"chapterfit {__version__}")
        return
    if args.cmd in ("finalize", "librivox"):
        return args.func(args)
    if not os.path.isfile(args.audio):
        die(f"no such file: {args.audio}")
    args.func(args)


def _cmd_librivox_search(args):
    # Imported only here so the GUI process never loads this module.
    from chaptertools.sources.librivox_fetch import cmd_search
    return cmd_search(args)


def _cmd_librivox_fetch(args):
    from chaptertools.sources.librivox_fetch import cmd_fetch
    return cmd_fetch(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)

