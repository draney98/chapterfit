#!/usr/bin/env python3
"""
abchapters.py - front end for chapterize.py / find_asin.py /
               librivox_chapters.py / consensus_chapters.py

Ask it for a file and it will work out the chapters:

    Audible route    find the ASIN, pull Audnexus chapter offsets, align them
                     against the silences in your file.
    LibriVox route   if the Audible route finds nothing that lines up, look the
                     book up on LibriVox and rebuild the boundaries from the
                     published per-section runtimes.
    Consensus route  evaluate Audible and LibriVox timelines together with a
                     global statistical sequence matcher and keep the best
                     complete structure.

Run with no arguments and it prompts for everything. Pass arguments and it
skips the prompts for whatever you supplied, so the same script works
interactively and in scripts.

    abchapters.py
    abchapters.py book.mp3
    abchapters.py book.mp3 --title "Ubik" --author "Philip K. Dick" --embed
    abchapters.py book.mp3 --asin B002V1OF70 --embed --yes
    abchapters.py --explain            # what every knob does
    abchapters.py --cache-info book.mp3
    abchapters.py --clear-cache

Silence detection is the slow part, so it is cached on disk and shared by all
three scripts. Scanning a 12-hour book takes a minute or two the first time and
is instant on every run after that, including runs with different chapter
sources. The cache is keyed on the file's size and mtime, so editing the file
invalidates it automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    import chapterize
except ImportError:
    chapterize = None
try:
    import find_asin
except ImportError:
    find_asin = None

CHAPTERIZE = os.path.join(HERE, "chapterize.py")
FIND_ASIN = os.path.join(HERE, "find_asin.py")
LIBRIVOX = os.path.join(HERE, "librivox_chapters.py")
CONSENSUS = os.path.join(HERE, "consensus_chapters.py")

AUDIO_EXT = (".mp3", ".m4a", ".m4b", ".opus", ".ogg", ".flac", ".aac", ".wav")
INPUT_CACHE = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "abchapters", "last-inputs.json")


# ==========================================================================
# the knobs, in one place so --explain and the prompts never disagree
# ==========================================================================

KNOBS = [
    ("noise", -40.0, "dB", "silence threshold",
     "How quiet audio must be to count as silence. Raise toward 0 (e.g. -30) "
     "when a noisy or hissy rip never goes quiet enough and no silences are "
     "found. Lower it (e.g. -50) when the scan returns hundreds of silences "
     "because it is catching pauses between sentences."),

    ("min_silence", 1.5, "s", "minimum silence length",
     "How long a quiet stretch must last to count. Lower to 1.0 or 0.8 when a "
     "narrator barely pauses at chapter breaks and nothing is detected. Raise "
     "to 2.5 or 3.0 when ordinary sentence pauses are being picked up and "
     "swamping the real chapter gaps."),

    ("tolerance", 4.0, "s", "match tolerance",
     "How close a published chapter offset must land to a detected silence to "
     "count as a match. This only affects scoring, not the final positions. "
     "Raise to 6-8 when you know the edition is right but the match count "
     "looks low; lower to 2 to be stricter about which ASIN wins."),

    ("snap_window", 15.0, "s", "snap window",
     "After the offset is solved, each boundary moves to the nearest silence "
     "within this window. Lower to 5 when boundaries are jumping to the wrong "
     "gap in a book with many long pauses. Raise to 25 when the edition drifts "
     "and cuts land noticeably inside the narration."),

    ("max_offset", 120.0, "s", "offset search range",
     "The largest shift the aligner will consider between the published "
     "timeline and your file. The default covers a missing Audible intro and "
     "credits. Raise to 600 if your rip omits a long foreword or a whole "
     "opening section."),

    ("min_match", 0.60, "ratio", "Audible accept threshold",
     "The fraction of chapter boundaries that must land on a silence before "
     "the Audible result is accepted. Below this the wrapper falls back to "
     "LibriVox. Raise to 0.8 to only accept near-perfect alignments; lower to "
     "0.4 when a book genuinely has few audible pauses and you would rather "
     "hand-check the result than fall through."),
]

KNOB_DEFAULTS = {k[0]: k[1] for k in KNOBS}


def explain_knobs():
    print("\nTunable knobs\n" + "=" * 72)
    for name, default, unit, short, text in KNOBS:
        flag = "--" + name.replace("_", "-")
        d = f"{default}{unit}" if unit != "ratio" else f"{default}"
        print(f"\n  {flag}  ({short})")
        print(f"    default: {d}")
        for line in _wrap(text, 68):
            print(f"    {line}")
    print("\n" + "=" * 72)
    print("""
Rules of thumb

  Nothing detected at all      raise --noise toward 0 and lower --min-silence
  Hundreds of silences found   lower --noise and raise --min-silence
  Right book, low match count  raise --tolerance
  Cuts land inside speech      raise --snap-window
  Wrong edition keeps winning  lower --tolerance and raise --min-match

Silence detection is cached per (file, --noise, --min-silence) pair, so
changing either of those two triggers a rescan. Everything else re-uses the
cached scan and is effectively free to retry.
""")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


# ==========================================================================
# prompting
# ==========================================================================

def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def load_last_inputs() -> dict:
    try:
        with open(INPUT_CACHE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: data.get(k) for k in ("path", "title", "author")
            if isinstance(data.get(k), str)}


def save_last_inputs(path: str, title: str | None, author: str | None):
    """Remember the most recent identity without making a run depend on it."""
    data = {
        "path": os.path.abspath(os.path.expanduser(path)),
        "title": title,
        "author": author,
    }
    try:
        os.makedirs(os.path.dirname(INPUT_CACHE), exist_ok=True)
        tmp = INPUT_CACHE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, INPUT_CACHE)
    except OSError:
        # This is only a convenience; a read-only home must not block a run.
        try:
            os.unlink(INPUT_CACHE + ".tmp")
        except OSError:
            pass


def ask(prompt: str, default: str | None = None, allow_blank: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            v = input(f"{prompt}{suffix}: ").strip()
        except EOFError:
            print()
            return default or ""
        if not v and default is not None:
            return default
        if v or allow_blank:
            return v
        print("  (required)")


def ask_choice(prompt: str, options: list[tuple[str, str]], default: str) -> str:
    print(f"\n{prompt}")
    for key, desc in options:
        mark = "*" if key == default else " "
        print(f"  {mark} {key:<9} {desc}")
    valid = {k for k, _ in options}
    while True:
        v = ask("choice", default)
        if v in valid:
            return v
        print(f"  pick one of: {', '.join(sorted(valid))}")


def ask_float(prompt: str, default: float) -> float:
    while True:
        v = ask(prompt, str(default))
        try:
            return float(v)
        except ValueError:
            print("  needs to be a number")


def ask_yes(prompt: str, default: bool = False) -> bool:
    d = "y" if default else "n"
    v = ask(f"{prompt} (y/n)", d).lower()
    return v.startswith("y")


def pick_file(default: str | None = None) -> str:
    while True:
        p = ask("Path to the audiobook file", default, allow_blank=False)
        p = os.path.expanduser(p.strip().strip("'\""))
        if os.path.isfile(p):
            return p
        if os.path.isdir(p):
            found = sorted(f for f in os.listdir(p)
                           if f.lower().endswith(AUDIO_EXT))
            if len(found) == 1:
                full = os.path.join(p, found[0])
                print(f"  using {found[0]}")
                return full
            if found:
                print(f"  {len(found)} audio files in that folder:")
                for i, f in enumerate(found[:20], 1):
                    print(f"    {i:>2}. {f}")
                sel = ask("  pick a number", "1")
                try:
                    return os.path.join(p, found[int(sel) - 1])
                except (ValueError, IndexError):
                    print("  no such number")
                    continue
            print("  no audio files there")
            continue
        print("  no such file")


# ==========================================================================
# running the sibling scripts
# ==========================================================================

def run(cmd: list[str], quiet: bool = False) -> tuple[int, str]:
    if not quiet:
        print(f"\n$ {' '.join(_q(c) for c in cmd)}\n")
    proc = subprocess.run(cmd, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if not quiet:
        sys.stdout.write(proc.stdout)
    return proc.returncode, proc.stdout


def _q(s: str) -> str:
    return f"'{s}'" if " " in s else s


def load_report(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ==========================================================================
# the routes
# ==========================================================================

def try_audible(cfg: dict) -> dict | None:
    """Returns the chapterize report on success, None to fall through."""
    asin = cfg["asin"]

    if not asin:
        print("\n--- identifying the edition on Audible ---")
        with tempfile.TemporaryDirectory() as td:
            js = os.path.join(td, "asin.json")
            cmd = [sys.executable, FIND_ASIN, cfg["audio"],
                   "--region", cfg["region"], "--verify", "--json", js,
                   "--noise", str(cfg["noise"]),
                   "--min-silence", str(cfg["min_silence"]),
                   "--tolerance", str(cfg["tolerance"]),
                   "--max-offset", str(cfg["max_offset"]),
                   "--silence-cache", cfg["cache_dir"]]
            if cfg["offset_hint"] is not None:
                cmd += ["--offset-hint", str(cfg["offset_hint"]),
                        "--offset-window", str(cfg["offset_window"])]
            if cfg["title"]:
                cmd += ["--title", cfg["title"]]
            if cfg["author"]:
                cmd += ["--author", cfg["author"]]
            if cfg["verbose"]:
                cmd += ["-v"]

            rc, _ = run(cmd)
            data = load_report(js)

        if rc != 0 or not data:
            print("  search failed")
            return None

        best = None
        for c in data.get("candidates", []):
            if c.get("verified") and c.get("match_ratio", 0) >= cfg["min_match"]:
                best = c
                break
        if not best:
            top = (data.get("candidates") or [None])[0]
            if top and top.get("verified"):
                print(f"  best Audible candidate matched only "
                      f"{top['matched']}/{top['total']} boundaries "
                      f"(need {cfg['min_match']:.0%})")
            else:
                print("  no Audible candidate could be verified")
            return None

        asin = best["asin"]
        print(f"\n  accepted {asin} - {best['matched']}/{best['total']} "
              f"boundaries matched  ({best.get('title', '')})")

    print("\n--- building chapters from Audnexus ---")
    with tempfile.TemporaryDirectory() as td:
        rep = os.path.join(td, "report.json")
        cmd = [sys.executable, CHAPTERIZE, cfg["audio"], "--asin", asin,
               "--region", cfg["region"], "--report", rep,
               "--noise", str(cfg["noise"]),
               "--min-silence", str(cfg["min_silence"]),
               "--tolerance", str(cfg["tolerance"]),
               "--snap-window", str(cfg["snap_window"]),
               "--max-offset", str(cfg["max_offset"]),
               "--silence-cache", cfg["cache_dir"]]
        if cfg["offset_hint"] is not None:
            cmd += ["--offset-hint", str(cfg["offset_hint"]),
                    "--offset-window", str(cfg["offset_window"])]
        cmd += output_flags(cfg)
        if cfg["verbose"]:
            cmd += ["-v"]

        rc, _ = run(cmd)
        data = load_report(rep)

    if rc != 0 or not data:
        return None

    ratio = data.get("align", {}).get("match_ratio", 0)
    if ratio < cfg["min_match"]:
        print(f"\n  alignment matched only {ratio:.0%} of boundaries "
              f"(threshold {cfg['min_match']:.0%})")
        return None
    return data


def try_librivox(cfg: dict) -> dict | None:
    print("\n--- trying LibriVox ---")
    with tempfile.TemporaryDirectory() as td:
        rep = os.path.join(td, "report.json")
        cmd = [sys.executable, LIBRIVOX, cfg["audio"], "--report", rep,
               "--snap", "--snap-window", str(min(cfg["snap_window"], 8.0)),
               "--noise", str(cfg["noise"]),
               "--min-silence", str(cfg["min_silence"]),
               "--tolerance", str(cfg["tolerance"]),
               "--max-offset", str(cfg["max_offset"]),
               "--silence-cache", cfg["cache_dir"]]
        if cfg["offset_hint"] is not None:
            cmd += ["--offset-hint", str(cfg["offset_hint"]),
                    "--offset-window", str(cfg["offset_window"])]
        if cfg["lv_id"]:
            cmd += ["--lv-id", cfg["lv_id"]]
        if cfg["title"]:
            cmd += ["--title", cfg["title"]]
        if cfg["author"]:
            cmd += ["--author", cfg["author"]]
        if cfg["lv_sections_json"]:
            cmd += ["--sections-json", cfg["lv_sections_json"]]
        cmd += output_flags(cfg)
        if cfg["verbose"]:
            cmd += ["-v"]

        rc, _ = run(cmd)
        data = load_report(rep)

    if rc != 0 or not data:
        return None
    # Prefer silence-aligned LibriVox results. Runtime-scale fallbacks with
    # large drift are probably a different recording.
    align = data.get("align") or {}
    if data.get("timeline_source") == "runtime_scale" and data.get("drift_ratio", 0) > 0.05:
        print(f"\n  runtime is {data['drift_ratio']:.1%} off the LibriVox "
              f"project - probably a different recording")
        return None
    if align and align.get("accepted") is False and data.get("drift_ratio", 0) > 0.05:
        print(f"\n  runtime is {data['drift_ratio']:.1%} off the LibriVox "
              f"project - probably a different recording")
        return None
    return data


def try_consensus(cfg: dict) -> dict | None:
    print("\n--- trying consensus matcher ---")
    if not os.path.isfile(CONSENSUS):
        print("  consensus_chapters.py is missing")
        return None
    with tempfile.TemporaryDirectory() as td:
        rep = os.path.join(td, "report.json")
        cmd = [sys.executable, CONSENSUS, cfg["audio"], "--report", rep,
               "--noise", str(cfg["noise"]),
               "--min-silence", str(cfg["min_silence"]),
               "--tolerance", str(cfg["tolerance"]),
               "--max-offset", str(cfg["max_offset"]),
               "--silence-cache", cfg["cache_dir"],
               "--sources", "both"]
        if cfg["offset_hint"] is not None:
            cmd += ["--offset-hint", str(cfg["offset_hint"]),
                    "--offset-window", str(cfg["offset_window"])]
        if cfg["asin"]:
            cmd += ["--asin", cfg["asin"]]
        if cfg["lv_id"]:
            cmd += ["--lv-id", cfg["lv_id"]]
        if cfg["title"]:
            cmd += ["--title", cfg["title"]]
        if cfg["author"]:
            cmd += ["--author", cfg["author"]]
        if cfg["lv_sections_json"]:
            cmd += ["--sections-json", cfg["lv_sections_json"]]
        cmd += output_flags(cfg)
        if cfg["verbose"]:
            cmd += ["-v"]

        rc, _ = run(cmd)
        data = load_report(rep)

    if data is None:
        return None
    # Accept even on exit 2 if the report exists and matched something usable.
    align = data.get("align") or {}
    if align.get("match_ratio", 0) < max(0.35, cfg["min_match"] * 0.5):
        print(f"\n  consensus matched only {align.get('match_ratio', 0):.0%} "
              f"of boundaries")
        return None
    if rc not in (0, 2):
        return None
    return data


def output_flags(cfg: dict) -> list[str]:
    out = []
    if cfg["action"] == "embed":
        out += ["--embed"] + ([cfg["dest"]] if cfg["dest"] else [])
    elif cfg["action"] == "split":
        out += ["--split", cfg["dest"] or "chapters_out"]
    elif cfg["action"] == "json":
        out += ["--abs-json", cfg["dest"] or "chapters.json"]
    elif cfg["action"] == "ffmeta":
        out += ["--ffmetadata", cfg["dest"] or "chapters.txt"]
    return out


# ==========================================================================
# cache management
# ==========================================================================

def cache_dir(cfg_dir: str | None) -> str:
    """None means the default, which is a sidecar beside each audio file."""
    return cfg_dir or chapterize.DEFAULT_CACHE


def _sidecars_under(root: str) -> list[str]:
    out = []
    if os.path.isfile(root):
        cp = chapterize.sidecar_path(root)
        return [cp] if os.path.exists(cp) else []
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            if f.endswith(".silences.json"):
                out.append(os.path.join(dirpath, f))
    return sorted(out)


def _describe(cp: str) -> tuple[str, list[str], int]:
    try:
        with open(cp) as fh:
            blob = json.load(fh)
    except (OSError, ValueError):
        return os.path.basename(cp), [], 0
    name = os.path.basename(blob.get("file", cp))
    lines = [f"{k:<28} {len(v)} silences"
             for k, v in (blob.get("runs") or {}).items()]
    return name, lines, os.path.getsize(cp)


def cache_info(path: str | None, cdir: str):
    found: list[str] = []

    if cdir == chapterize.SIDECAR:
        target = path or os.getcwd()
        found += _sidecars_under(target)
        if not path:
            print(f"sidecar caches under {target}")
        # anything that had to fall back because the folder was read-only
        fb = os.path.join(chapterize.FALLBACK_CACHE, "silences")
        if os.path.isdir(fb):
            extra = [os.path.join(fb, f) for f in sorted(os.listdir(fb))
                     if f.endswith(".json")]
            if extra:
                print(f"(plus {len(extra)} in the fallback cache {fb})")
                found += extra
    else:
        sdir = os.path.join(cdir, "silences")
        if os.path.isdir(sdir):
            found = [os.path.join(sdir, f) for f in sorted(os.listdir(sdir))
                     if f.endswith(".json")]
        print(f"silence cache: {sdir}")
        if path:
            want = chapterize.silence_cache_path(path, cdir)
            found = [f for f in found if os.path.abspath(f) == want]

    if not found:
        print("nothing cached" + (f" for {os.path.basename(path)}" if path else ""))
        return

    total = 0
    for cp in found:
        name, lines, size = _describe(cp)
        total += size
        print(f"  {name[:56]}")
        print(f"      {cp}")
        for line in lines:
            print(f"      {line}")
    print(f"\n{len(found)} file(s), {total/1024:.1f} KiB")


def clear_cache(cdir: str, target: str | None, yes: bool):
    victims: list[str] = []

    if cdir == chapterize.SIDECAR:
        victims += _sidecars_under(target or os.getcwd())
        fb = os.path.join(chapterize.FALLBACK_CACHE, "silences")
        if os.path.isdir(fb):
            victims += [os.path.join(fb, f) for f in os.listdir(fb)
                        if f.endswith(".json")]
    else:
        sdir = os.path.join(cdir, "silences")
        if os.path.isdir(sdir):
            victims += [os.path.join(sdir, f) for f in os.listdir(sdir)
                        if f.endswith(".json")]

    if not victims:
        print("nothing to clear")
        return

    where = target or (os.getcwd() if cdir == chapterize.SIDECAR else cdir)
    if not yes and interactive():
        print(f"about to delete {len(victims)} cache file(s) under {where}:")
        for v in victims[:10]:
            print(f"  {v}")
        if len(victims) > 10:
            print(f"  ... and {len(victims) - 10} more")
        if not ask_yes("delete them?", False):
            print("kept")
            return

    n = 0
    for v in victims:
        try:
            os.unlink(v)
            n += 1
        except OSError as e:
            print(f"  could not delete {v}: {e.strerror}")
    print(f"cleared {n} cache file(s)")


# ==========================================================================

def main():
    ap = argparse.ArgumentParser(
        description="Chapterize an audiobook: Audible first, LibriVox as fallback.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio", nargs="?", help="the audiobook file")
    ap.add_argument("--title")
    ap.add_argument("--author")
    ap.add_argument("--asin", help="skip the search, use this Audible ASIN")
    ap.add_argument("--lv-id", help="skip the search, use this LibriVox project id")
    ap.add_argument("--region", default="us")
    ap.add_argument("--offset-hint", type=float,
                    help="roughly how many seconds later the book starts "
                         "in your file than in the published edition")
    ap.add_argument("--offset-window", type=float, default=15.0)

    ap.add_argument("--source", choices=["auto", "audible", "librivox", "consensus"],
                    default="auto",
                    help="auto tries Audible, then LibriVox, then consensus "
                         "(default auto)")

    act = ap.add_mutually_exclusive_group()
    act.add_argument("--preview", action="store_true", help="report only")
    act.add_argument("--embed", action="store_true",
                     help="write a copy with chapters embedded")
    act.add_argument("--split", action="store_true", help="cut into per-chapter files")
    act.add_argument("--abs-json", action="store_true",
                     help="write Audiobookshelf chapter JSON")
    act.add_argument("--ffmeta", action="store_true", help="write an ffmetadata file")
    ap.add_argument("--dest", help="output path or directory for the action above")

    for name, default, unit, short, _ in KNOBS:
        ap.add_argument("--" + name.replace("_", "-"), type=float, default=None,
                        help=f"{short} (default {default}{'' if unit=='ratio' else unit})")

    ap.add_argument("--cache-dir",
                    help="silence cache location: a directory, or "
                         "'sidecar' to write <file>.silences.json next "
                         "to the audio (default: sidecar)")
    ap.add_argument("--cache-info", action="store_true",
                    help="show what is cached and exit; pass a file or "
                         "folder to limit the scope")
    ap.add_argument("--clear-cache", action="store_true",
                    help="delete cached silence scans and exit; pass a "
                         "file or folder to limit the scope")
    ap.add_argument("--explain", action="store_true",
                    help="explain every knob and exit")

    ap.add_argument("--lv-sections-json", help="offline LibriVox API response")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="never prompt; use defaults for anything not given")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.explain:
        explain_knobs()
        return

    if chapterize is None:
        print("error: chapterize.py must sit next to this script", file=sys.stderr)
        sys.exit(1)
    for script in (CHAPTERIZE, FIND_ASIN, LIBRIVOX, CONSENSUS):
        if not os.path.isfile(script):
            print(f"error: missing {os.path.basename(script)}", file=sys.stderr)
            sys.exit(1)

    cdir = cache_dir(args.cache_dir)

    if args.clear_cache:
        clear_cache(cdir, args.audio, args.yes)
        return
    if args.cache_info:
        cache_info(args.audio, cdir)
        return

    # ---- gather inputs ----------------------------------------------------
    last = load_last_inputs()
    audio = args.audio
    if not audio:
        if not interactive():
            print("error: no file given", file=sys.stderr)
            sys.exit(1)
        print("\nAudiobook chapterizer")
        print("=" * 60)
        previous_path = last.get("path")
        audio = pick_file(previous_path if previous_path and
                          os.path.exists(previous_path) else None)
    audio = os.path.expanduser(audio)
    if not os.path.isfile(audio):
        print(f"error: no such file: {audio}", file=sys.stderr)
        sys.exit(1)

    guess_title = guess_author = None
    if find_asin:
        try:
            lf = find_asin.probe(audio)
            guess_title, guess_author = lf.title, lf.author
            print(f"\nfile     : {os.path.basename(audio)}")
            print(f"duration : {chapterize.hms(lf.duration)}")
        except SystemExit:
            pass

    prompting = interactive() and not args.yes

    title, author = args.title, args.author
    asin, lv_id = args.asin, args.lv_id
    same_as_last = (last.get("path") == os.path.abspath(audio))
    cached_title = last.get("title") if same_as_last else None
    cached_author = last.get("author") if same_as_last else None

    if prompting:
        if not title:
            title = ask("Title", cached_title or guess_title) or None
        if not author:
            author = ask("Author", cached_author or guess_author) or None
        if not asin and args.source in ("auto", "audible"):
            asin = ask("Audible ASIN (blank to search)", "") or None
    else:
        title = title or guess_title
        author = author or guess_author

    save_last_inputs(audio, title, author)

    # ---- action -----------------------------------------------------------
    action = ("embed" if args.embed else "split" if args.split
              else "json" if args.abs_json else "ffmeta" if args.ffmeta
              else "preview" if args.preview else None)
    if action is None:
        if prompting:
            action = ask_choice("What should it produce?", [
                ("preview", "report the chapters, write nothing"),
                ("embed", "a copy of the file with chapters embedded (no re-encode)"),
                ("split", "one file per chapter"),
                ("json", "Audiobookshelf chapter JSON"),
                ("ffmeta", "an ffmetadata chapter file"),
            ], "preview")
        else:
            action = "preview"

    dest = args.dest
    if prompting and action in ("embed", "split", "json", "ffmeta") and not dest:
        suggestion = {
            "embed": os.path.splitext(audio)[0] + ".chapters" + os.path.splitext(audio)[1],
            "split": "chapters_out",
            "json": "chapters.json",
            "ffmeta": "chapters.txt",
        }[action]
        dest = ask("Output path", suggestion) or suggestion

    # ---- knobs ------------------------------------------------------------
    knobs = dict(KNOB_DEFAULTS)
    for name in knobs:
        v = getattr(args, name, None)
        if v is not None:
            knobs[name] = v

    if prompting:
        if ask_yes("\nAdjust detection settings?", False):
            explain_knobs()
            for name, default, unit, short, _ in KNOBS:
                knobs[name] = ask_float(f"  {short} ({name})", knobs[name])

    cfg = {
        "audio": audio, "title": title, "author": author,
        "asin": asin, "lv_id": lv_id, "region": args.region,
        "action": action, "dest": dest,
        "verbose": args.verbose,
        "lv_sections_json": args.lv_sections_json,
        "cache_dir": cdir,
        "offset_hint": args.offset_hint,
        "offset_window": args.offset_window,
        **knobs,
    }

    # ---- show the plan ----------------------------------------------------
    print("\n" + "=" * 60)
    print(f"  file      {os.path.basename(audio)}")
    print(f"  title     {title or '(unknown)'}")
    print(f"  author    {author or '(unknown)'}")
    print(f"  source    {args.source}"
          + (f" (asin {asin})" if asin else "")
          + (f" (librivox {lv_id})" if lv_id else ""))
    print(f"  produce   {action}" + (f" -> {dest}" if dest else ""))
    print(f"  knobs     noise {knobs['noise']}dB, min-silence "
          f"{knobs['min_silence']}s, tolerance {knobs['tolerance']}s, "
          f"snap {knobs['snap_window']}s, accept {knobs['min_match']:.0%}")
    cached = chapterize._cache_load(
        audio, cdir, f"noise={knobs['noise']}:d={knobs['min_silence']}")
    print(f"  silences  {'cached (' + str(len(cached)) + ' runs)' if cached else 'will be scanned'}")
    print("=" * 60)

    if prompting and not ask_yes("Proceed?", True):
        print("cancelled")
        return

    # ---- run --------------------------------------------------------------
    result = None
    if args.source in ("auto", "audible") and not lv_id:
        result = try_audible(cfg)

    if result is None and args.source in ("auto", "librivox"):
        if args.source == "auto":
            print("\n" + "-" * 60)
            print("Audible route did not produce a confident match.")
            if prompting and not ask_yes("Try LibriVox?", True):
                print("stopping")
                sys.exit(2)
        result = try_librivox(cfg)

    if result is None and args.source in ("auto", "consensus"):
        if args.source == "auto":
            print("\n" + "-" * 60)
            print("Single-source routes did not produce a confident match.")
            if prompting and not ask_yes("Try the consensus matcher?", True):
                print("stopping")
                sys.exit(2)
        result = try_consensus(cfg)

    print("\n" + "=" * 60)
    if result is None:
        print("No chapter source matched this file.")
        print("""
Things to try, roughly in order:

  1. Check the silences were found at all:
         abchapters.py FILE --preview -v
     Zero silences means the detection knobs are wrong for this rip.
     Try --noise -30 --min-silence 1.0 and see if runs appear.

  2. Give it the identity directly. If you know the book, look it up on
     audible.com and pass --asin, or on librivox.org and pass --lv-id.
     That skips the search entirely and just does the alignment.

  3. Force the statistical matcher:
         abchapters.py FILE --source consensus --preview

  4. Lower the bar: --min-match 0.4 accepts a weaker alignment. Check the
     printed table before writing anything.

  5. If the file is a fan recording, a podcast rip, or an edition that was
     never on Audible or LibriVox, no lookup will help. Silence-based
     splitting with mp3splt is the remaining option and will not give you
     real chapter titles.
""")
        sys.exit(2)

    src = result.get("source")
    n = len(result.get("chapters", []))
    print(f"Done via {src}: {n} chapters.")
    if src == "audible":
        a = result["align"]
        print(f"  ASIN {result['asin']}, {a['matched']}/{a['total']} boundaries "
              f"matched, offset {a['offset_sec']:+.2f}s")
    elif src == "librivox":
        print(f"  LibriVox project {result['librivox_id']}, "
              f"{result['sections']} sections, drift {result['drift_sec']:+.1f}s")
    elif src == "consensus":
        selected = result.get("selected") or {}
        a = result.get("align") or {}
        print(f"  chose {selected.get('source')} {selected.get('identity')}, "
              f"score {a.get('score', '?')}, "
              f"{a.get('matched', '?')}/{a.get('total', '?')} matched, "
              f"offset {a.get('offset_sec', 0):+.2f}s")
    if action == "preview":
        print("\nPreview only. Re-run with --embed, --split or --abs-json to write.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ninterrupted")
        sys.exit(130)
