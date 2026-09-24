"""Optional Whisper / transcription helpers."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile

from chaptertools import __version__
from chaptertools.envelope.artifact import Artifact
from chaptertools.util import die, hms, need, parse_time

# Whisper on a 17-hour book is hours of compute. But a narrator says
# "Chapter Twenty-Seven" in the few seconds *after* a chapter break, and the
# candidate gaps are already known. Transcribing 12 seconds at each of ~100
# candidates is about 20 minutes of audio - a couple of minutes of work - and
# it turns a statistical alignment into a read-off.

NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}

ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18,
    "nineteenth": 19, "twentieth": 20, "thirtieth": 30, "fortieth": 40,
}



def words_to_number(text: str):
    """'twenty-seven' -> 27. Returns None if there is no number in there."""
    text = text.lower().replace("-", " ").replace(" and ", " ")
    toks = [t for t in re.split(r"[^a-z0-9]+", text) if t]
    if not toks:
        return None
    if toks[0].isdigit():
        return int(toks[0])
    total, current, seen = 0, 0, False
    for t in toks:
        if t in ORDINALS and not seen:
            return ORDINALS[t]
        if t not in NUM_WORDS:
            if seen:
                break
            continue
        seen = True
        v = NUM_WORDS[t]
        if v == 100:
            current = max(current, 1) * 100
        else:
            current += v
    total += current
    return total if seen and total > 0 else None


# What a chapter announcement sounds like. Whisper punctuates, so keep these
# loose and case-insensitive.
HEADING_RE = re.compile(
    r"\b(?:"
    r"(?P<kind>chapter|part|book|section|episode)\s+"
    r"(?P<num>[0-9]{1,3}|[a-z\- ]{3,40}?)"
    r"(?=[\s.,:;!?\"']|$)"
    r"|(?P<solo>prologue|epilogue|foreword|afterword|introduction|"
    r"preface|opening credits|end credits)"
    r")", re.I)


def parse_headings(text: str):
    """Every chapter announcement in a fragment, in order.

    A window can easily hold more than one - the tail of one chapter and the
    start of the next - and a false positive like "chapter and verse" can sit
    ahead of the real thing. Reading only the first match loses both cases.
    """
    out = []
    for m in HEADING_RE.finditer(text or ""):
        if m.group("solo"):
            out.append({"kind": m.group("solo").lower(), "number": None,
                        "raw": m.group(0).strip(), "pos": m.start()})
            continue
        num = words_to_number(m.group("num") or "")
        if num is None:
            continue
        out.append({"kind": m.group("kind").lower(), "number": num,
                    "raw": m.group(0).strip(), "pos": m.start()})
    return out


def parse_heading(text: str):
    """The single best announcement in a fragment, or None.

    Prefers a numbered heading over a bare 'prologue', and the earliest one -
    the clip starts at the break, so the announcement is at the front and any
    later "chapter" is narrative rather than a heading.
    """
    hits = parse_headings(text)
    if not hits:
        return None
    numbered = [h for h in hits if h["number"] is not None]
    return (numbered or hits)[0]


def clip(audio: str, start: float, dur: float, dest: str):
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}", "-i", audio,
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dest],
        capture_output=True, text=True)
    return r.returncode == 0


class Transcriber:
    """faster-whisper if available, or any external command that prints text.

    The command form exists so another engine can be dropped in, and so the
    pipeline can be exercised without a model present.
    """

    def __init__(self, model="base", device="auto", compute="int8",
                 cmd=None, language="en", verbose=False, model_dir=None):
        self.cmd = cmd
        self.verbose = verbose
        self.language = language
        self.model = None
        if cmd:
            return
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            die("""faster-whisper is not installed.

  Debian and Ubuntu refuse system-wide pip installs (PEP 668), and there is
  no distro package. Use a virtual environment and run this script from it -
  chapterfit needs nothing else, so it works unchanged there:

      python3 -m venv ~/.venvs/whisper
      ~/.venvs/whisper/bin/pip install faster-whisper
      ~/.venvs/whisper/bin/python chapterfit.py listen FILE --pin

  Or keep your normal python and point at another engine with
  --transcribe-cmd.""")
        if device == "auto":
            device = "cpu"
        where = f" from {model_dir}" if model_dir else ""
        print(f"  loading whisper model {model!r} on {device}{where}...")
        try:
            self.model = WhisperModel(model, device=device,
                                      compute_type=compute,
                                      download_root=model_dir)
        except Exception as e:      # noqa: BLE001 - the causes are varied
            msg = str(e)
            hint = ""
            if "huggingface" in msg.lower() or "LocalEntryNotFound" in type(e).__name__:
                hint = ("""

  The model is downloaded from huggingface.co on first use. If this machine
  cannot reach it, fetch the model elsewhere and copy it over:

      # on a machine with access
      pip install huggingface_hub
      huggingface-cli download Systran/faster-whisper-%s --local-dir fw-%s

      # then here
      chapterfit.py listen FILE --model-dir /path/to/fw-%s --model %s""" %
                        (model, model, model, model))
            die(f"could not load the whisper model {model!r}: "
                f"{msg.splitlines()[0][:160]}{hint}")

    def __call__(self, path: str) -> str:
        return " ".join(seg["t"] for seg in self.segments(path)).strip()

    def segments(self, path: str):
        """[{s, e, t, w}] - segment start/end/text plus [[time, word], ...].

        Word times are the point. Without them a hit in a 13-second clip can
        only be pinned to the clip, which is how an announcement spoken eight
        seconds after an ordinary pause gets mistaken for that pause being a
        chapter break.
        """
        if self.cmd:
            try:
                argv = shlex.split(self.cmd) + [path]
            except ValueError as e:
                die(f"invalid --transcribe-cmd: {e}")
            r = subprocess.run(argv, capture_output=True, text=True)
            if r.returncode != 0:
                err = (r.stderr or r.stdout or "").strip()[-400:]
                die(f"transcribe command failed (exit {r.returncode})"
                    + (f":\n{err}" if err else ""))
            out = []
            for line in (r.stdout or "").splitlines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("{"):
                    try:
                        j = json.loads(line)
                        out.append({"s": float(j.get("start", 0.0)),
                                    "e": float(j.get("end", j.get("start", 0.0))),
                                    "t": str(j.get("text", "")),
                                    "w": [[float(a), str(b)]
                                          for a, b in (j.get("words") or [])]})
                        continue
                    except (ValueError, TypeError):
                        pass
                out.append({"s": 0.0, "e": 0.0, "t": line, "w": []})
            return out
        segs, _ = self.model.transcribe(
            path, language=self.language, beam_size=1, vad_filter=False,
            word_timestamps=True, condition_on_previous_text=False)
        out = []
        for sg in segs:
            words = []
            for w in (getattr(sg, "words", None) or []):
                words.append([round(float(w.start), 2), w.word.strip()])
            out.append({"s": float(sg.start), "e": float(sg.end),
                        "t": sg.text, "w": words})
        return out


HEADWORD_RE = re.compile(
    r"chapter|part|book|section|episode|prologue|epilogue|foreword|"
    r"afterword|introduction|preface", re.I)


def locate_heading(segments, heading) -> float | None:
    """When, within the clip, the announcement is spoken.

    Prefers the word timestamp of the heading word itself; falls back to the
    segment that contains it. Returns None when there is no timing at all.
    """
    if not segments:
        return None
    kind = (heading or {}).get("kind", "")
    for seg in segments:
        if not HEADWORD_RE.search(seg.get("t") or ""):
            continue
        for wt, word in seg.get("w") or []:
            if HEADWORD_RE.match(word.strip(" .,:;!?\"'")) and \
                    (not kind or word.strip(" .,:;!?\"'").lower().startswith(kind[:4])):
                return wt
        for wt, word in seg.get("w") or []:
            if HEADWORD_RE.match(word.strip(" .,:;!?\"'")):
                return wt
        return float(seg.get("s", 0.0))
    return None


def segments_text(segments) -> str:
    return " ".join(sg.get("t", "") for sg in segments).strip()


CACHE_FORMAT = "w2"          # segments carrying word timings


def clip_key(start: float, dur: float, model: str) -> str:
    return f"{start:.2f}|{dur:.2f}|{model}#{CACHE_FORMAT}"


class ClipCache:
    """Transcripts keyed on (clip position, length, model, format).

    Whisper is the expensive part, so nothing is transcribed twice. The format
    tag is in the key because the stored shape has changed - word timings were
    added - and an old entry must not be read as a new one.
    """

    def __init__(self, art: Artifact, model: str, flush_every: int = 10):
        self.art = art
        self.model = model
        self.flush_every = flush_every
        self.pending = 0
        heard = art.data.setdefault("heard", {})
        self.store = heard.setdefault("clips", {})
        self.hits = 0
        self.misses = 0

    def get(self, start, dur):
        v = self.store.get(clip_key(start, dur, self.model))
        if v is not None:
            self.hits += 1
        return v

    def put(self, start, dur, text):
        self.store[clip_key(start, dur, self.model)] = text
        self.misses += 1
        self.pending += 1
        if self.pending >= self.flush_every:
            self.flush()

    def flush(self):
        if self.pending:
            self.art.save()
            self.pending = 0


def listen_candidates(art: Artifact, args):
    """Which positions are worth listening to?"""
    gs = art.data["gaps"]
    gaps = gs["gaps"]
    ident = art.data.get("identification") or {}
    mids = set(round(m, 3) for m in (ident.get("candidate_mids") or []))
    if mids and not args.all_gaps:
        sel = [g for g in gaps if round(g["mid"], 3) in mids]
        src = "the candidate set chosen by identify"
    else:
        cut = args.min_gap or 0.0
        sel = sorted((g for g in gaps if g["dur"] >= cut),
                     key=lambda g: -g["dur"])[:args.max_candidates]
        sel.sort(key=lambda g: g["mid"])
        src = f"the {len(sel)} longest gaps"
    if len(sel) > args.max_candidates:
        sel = sorted(sel, key=lambda g: -g["dur"])[:args.max_candidates]
        sel.sort(key=lambda g: g["mid"])
    return sel, src


def fit_from_confirmed(confirmed, chapters):
    """Least-squares actual = scale * published + offset over confirmations."""
    pts = [(chapters[i]["start"], t) for i, t in sorted(confirmed.items())]
    if len(pts) < 2:
        return (1.0, pts[0][1] - pts[0][0]) if pts else None
    n = len(pts)
    sx = sum(a for a, _ in pts); sy = sum(b for _, b in pts)
    sxx = sum(a * a for a, _ in pts); sxy = sum(a * b for a, b in pts)
    den = n * sxx - sx * sx
    if abs(den) < 1e-9:
        return (1.0, pts[0][1] - pts[0][0])
    scale = (n * sxy - sx * sy) / den
    return (scale, (sy - scale * sx) / n)


def spread_pick(items, k, keyfn):
    """k items spread evenly across the range, not clustered."""
    if k >= len(items):
        return list(items)
    lo, hi = keyfn(items[0]), keyfn(items[-1])
    want = [lo + (hi - lo) * (i + 0.5) / k for i in range(k)]
    out, used = [], set()
    for w in want:
        best = min((x for j, x in enumerate(items) if j not in used),
                   key=lambda x: abs(keyfn(x) - w), default=None)
        if best is None:
            break
        used.add(items.index(best))
        out.append(best)
    return sorted(out, key=keyfn)


def sweep_span(audio, start, length, tr, cache, model, verbose=False):
    """Transcribe a continuous span and return every heading with its time.

    This is the escape from needing a silence at all. A de-gapped transfer can
    run straight from one chapter into the next with no pause to detect, but
    the narrator still says the words, and a segment timestamp says where.
    """
    key_start, key_len = round(start, 2), round(length, 2)
    cached = cache.get(key_start, key_len)
    segs = None
    if cached is not None:
        try:
            segs = json.loads(cached)
            if segs and not isinstance(segs[0], dict):
                segs = [{"s": float(a), "e": float(a), "t": b, "w": []}
                        for a, b in segs]
        except (ValueError, TypeError):
            segs = None
    if segs is None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "sweep.wav")
            if not clip(audio, start, length, wav):
                return [], False
            if os.environ.get("CHAPTERFIT_CLIP_DEBUG"):
                with open(wav + ".start", "w") as fh:
                    fh.write(str(start))
            segs = tr.segments(wav)
        cache.put(key_start, key_len, json.dumps(segs))
        was_cached = False
    else:
        was_cached = True

    found = []
    for i, sg in enumerate(segs):
        text = sg.get("t", "")
        for h in parse_headings(text):
            at = locate_heading([sg], h)
            found.append({"time": start + (at if at is not None
                                           else float(sg.get("s", 0.0))),
                          "heading": h, "text": text.strip(),
                          "word_timed": at is not None and bool(sg.get("w"))})
    return found, was_cached


def collect_transcripts(art: Artifact):
    """Every transcribed fragment, absolute time first, in order.

    listen stores one text per clip; sweep stores timestamped segments. Both
    live in the same cache, so flatten them into one timeline.
    """
    out = []
    clips = (art.data.get("heard") or {}).get("clips") or {}
    for key, val in clips.items():
        try:
            start_s, len_s, model = key.split("|", 2)
            start, length = float(start_s), float(len_s)
        except ValueError:
            continue
        try:
            segs = json.loads(val)
        except (ValueError, TypeError):
            out.append((start, model, str(val).strip(), []))
            continue
        if isinstance(segs, list) and segs and isinstance(segs[0], dict):
            for sg in segs:
                words = [[start + float(w[0]), w[1]] for w in (sg.get("w") or [])]
                out.append((start + float(sg.get("s", 0.0)), model,
                            (sg.get("t") or "").strip(), words))
        elif isinstance(segs, list):
            for a, b in segs:
                out.append((start + float(a), model, str(b).strip(), []))
        else:
            out.append((start, model, str(val).strip(), []))
    out.sort(key=lambda x: x[0])
    return out


def cmd_transcripts(args):
    """Print or save what was transcribed, so it can be read and searched."""
    art = Artifact(args.audio, args.work_dir)
    rows = collect_transcripts(art)
    if not rows:
        die("nothing transcribed yet - run 'listen' or 'sweep' first")

    pat = re.compile(args.grep, re.I) if args.grep else None
    lines = []
    for t, model, text, words in rows:
        if not text:
            continue
        if pat and not pat.search(text):
            continue
        if args.words and words:
            # the exact moment each matching word was spoken
            for wt, w in words:
                if not pat or pat.search(w):
                    lines.append(f"{hms(wt):>13}  {w}")
        else:
            lines.append(f"{hms(t):>13}  {text}")

    header = (f"# transcripts for {os.path.basename(art.audio)}\n"
              f"# {len(rows)} fragment(s) cached"
              + (f", {len(lines)} matching {args.grep!r}" if pat else "")
              + "\n")
    body = "\n".join(lines) + "\n"
    if args.out:
        with open(args.out, "w") as f:
            f.write(header + body)
        print(f"wrote {args.out}  ({len(lines)} line(s))")
    else:
        sys.stdout.write(header + body)


def cmd_sweep(args):
    """Search continuous audio for chapter announcements."""
    need("ffmpeg")
    art = Artifact(args.audio, args.work_dir)
    art.require("envelope", "chapterfit.py measure FILE")
    dur = art.data["envelope"]["duration"]
    ident = art.data.get("identification")
    chapters = ident["chapters"] if ident else None

    print(f"\n=== chapterfit {__version__} - sweep ===")
    lo = parse_time(args.start) if args.start else 0.0
    hi = parse_time(args.end) if args.end else dur
    lo, hi = max(0.0, lo), min(dur, hi)
    if hi <= lo:
        die("empty range")
    span = hi - lo
    print(f"  sweeping {hms(lo)} to {hms(hi)}  ({span/60:.1f} min, "
          f"{100*span/dur:.1f}% of the book)")

    cache = ClipCache(art, f"sweep:{args.model}")
    tr = Transcriber(args.model, args.device, args.compute_type,
                     args.transcribe_cmd, args.language, args.verbose,
                     args.model_dir)

    chunk = args.chunk
    hits, transcribed = [], 0
    t = lo
    while t < hi:
        length = min(chunk, hi - t)
        found, was_cached = sweep_span(args.audio, t, length, tr, cache,
                                       args.model, args.verbose)
        transcribed += 0 if was_cached else length
        for f in found:
            hits.append(f)
            h = f["heading"]
            lbl = h["kind"] + (f" {h['number']}" if h["number"] else "")
            mark = "" if f.get("word_timed") else "  (segment timing only)"
            print(f"  {hms(f['time']):>13}  {lbl:<18} "
                  f"{f['text'][:40]!r}{mark}")
        t += length
        cache.flush()
    cache.flush()

    print(f"\n  {len(hits)} announcement(s) found; transcribed "
          f"{transcribed/60:.1f} min of new audio")
    if not hits:
        print("  Nothing. The narrator may not name chapters in this range.")
        return

    if not chapters:
        print("  Run identify to map these onto a chapter list.")
        return

    entries = [{"mid": f["time"], "end": f["time"], "dur": 0.0,
                "text": f["text"], "heading": f["heading"]} for f in hits]
    # record them alongside listen's findings so later runs and the recap see
    # one picture rather than two
    heard = art.data.setdefault("heard", {})
    positions = heard.setdefault("positions", [])
    known = {round(p.get("mid", -1), 2) for p in positions}
    for e in entries:
        if round(e["mid"], 2) not in known:
            positions.append(dict(e, source="sweep"))
    art.save()
    pins, _ = match_headings(entries, chapters, 0.0)
    if not pins:
        print("  None matched a chapter in the list.")
        return
    print(f"\n  {len(pins)} matched the chapter list:")
    for idx in sorted(pins):
        print(f"    chapter {idx+1:<4} at {hms(pins[idx])}  "
              f"{chapters[idx]['title'][:40]}")
    if args.pin or confirm_pin(args):
        existing = art.data.get("pins") or {}
        for idx, t2 in pins.items():
            existing[str(idx)] = round(t2, 3)
        art.data["pins"] = existing
        art.data.pop("alignment", None)
        art.save()
        print(f"\n  pinned {len(pins)} chapter(s)")
        print(f"  next:  chapterfit.py align {os.path.basename(args.audio)!r}")


def cmd_listen(args):
    need("ffmpeg")
    art = Artifact(args.audio, args.work_dir)
    art.require("gaps", "chapterfit.py measure FILE")
    dur = art.data["envelope"]["duration"]
    ident = art.data.get("identification")

    print(f"\n=== chapterfit {__version__} - listen ===")
    cands, src = listen_candidates(art, args)
    if not cands:
        die("no candidate gaps to listen at")
    window = args.pre + args.post
    cache = ClipCache(art, args.model)
    cached_n = sum(1 for g in cands
                   if cache.get(g["end"] - args.pre, window) is not None)
    print(f"  {len(cands)} candidate positions from {src}")
    if cached_n:
        print(f"  {cached_n} already transcribed in a previous run - reusing")

    chapters = ident["chapters"] if ident else None
    strategy = args.strategy
    if strategy == "adaptive" and not chapters:
        print("  (no identification yet, so listening to everything)")
        strategy = "all"

    tr = None

    def transcribe(g):
        """Cached transcription of the window after one gap.

        Returns (segments, was_cached). Segments carry word times, so the
        announcement can be located within the clip rather than assumed to
        coincide with the gap.
        """
        nonlocal tr
        start_t = g["end"] - args.pre
        hit = cache.get(start_t, window)
        if hit is not None:
            try:
                return json.loads(hit), True
            except (ValueError, TypeError):
                return [{"s": 0.0, "e": 0.0, "t": str(hit), "w": []}], True
        if tr is None:
            tr = Transcriber(args.model, args.device, args.compute_type,
                             args.transcribe_cmd, args.language, args.verbose,
                             args.model_dir)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wav = os.path.join(td, "clip.wav")
            if not clip(args.audio, start_t, window, wav):
                return [], False
            if os.environ.get("CHAPTERFIT_CLIP_DEBUG"):
                with open(wav + ".start", "w") as fh:
                    fh.write(str(start_t))
            segs = tr.segments(wav)
        cache.put(start_t, window, json.dumps(segs))
        return segs, False

    heard = []
    confirmed = {}          # chapter index -> time
    listened = 0
    from_cache = 0
    from_pins = 0

    def record(g, segs):
        text = segments_text(segs)
        hit = parse_heading(text)
        entry = {"mid": g["mid"], "end": g["end"], "dur": g["dur"],
                 "text": text, "heading": hit}
        if hit:
            off = locate_heading(segs, hit)
            if off is not None:
                # where the words actually fall, in absolute time
                spoken = g["end"] - args.pre + off
                entry["spoken_at"] = round(spoken, 3)
                entry["offset_from_gap"] = round(spoken - g["end"], 2)
                # An announcement belongs to this gap only if it follows it
                # closely. Several seconds later means the break is elsewhere
                # and this gap is an ordinary pause.
                if abs(entry["offset_from_gap"]) > args.max_announce_lag:
                    entry["suspect"] = True
        heard.append(entry)
        return entry

    def try_confirm(entry):
        """Match one announcement onto the published list."""
        if not chapters or not entry["heading"]:
            return None
        if entry.get("suspect") and not args.accept_suspect:
            return None
        pins, _ = match_headings([entry], chapters, args.title_slack)
        for idx, t in pins.items():
            if idx not in confirmed:
                confirmed[idx] = t
                entry["chapter_index"] = idx
                return idx
        return None

    # ---- recall ---------------------------------------------------------
    # Start from what is already known rather than rediscovering it. Cached
    # transcripts cost nothing to re-read, and pins are the strongest evidence
    # there is - they were measured by ear.
    if chapters:
        for idx_s, t in (art.data.get("pins") or {}).items():
            try:
                confirmed[int(idx_s)] = float(t)
                from_pins += 1
            except (TypeError, ValueError):
                pass
    for g in cands:
        raw = cache.get(g["end"] - args.pre, window)
        if raw is None:
            continue
        try:
            segs = json.loads(raw)
        except (ValueError, TypeError):
            segs = [{"s": 0.0, "e": 0.0, "t": str(raw), "w": []}]
        e = record(g, segs)
        try_confirm(e)
        if e["heading"]:
            from_cache += 1
    if from_pins or from_cache:
        print(f"  recalled {from_pins} pin(s) and {from_cache} cached "
              f"announcement(s) -> {len(confirmed)} chapters already known")

    target_now = min(args.confirm, len(chapters) - 1) if chapters else 0
    if chapters and len(confirmed) >= target_now and strategy == "adaptive":
        print(f"  that already meets the target of {target_now}; "
              f"nothing new to transcribe")
        strategy = "done"

    if strategy == "all":
        est = len(cands) * window
        print(f"  {est/60:.1f} min of audio to transcribe "
              f"({100*est/dur:.1f}% of the book)")
        known = {round(h["mid"], 3) for h in heard}
        todo = [g for g in cands if round(g["mid"], 3) not in known]
        for n, g in enumerate(todo, 1):
            segs, was_cached = transcribe(g)
            listened += 0 if was_cached else 1
            e = record(g, segs)
            idx = try_confirm(e)
            if e["heading"]:
                lbl = e["heading"]["kind"] + (
                    f" {e['heading']['number']}" if e["heading"]["number"] else "")
                print(f"  {n:>4}/{len(todo)} {hms(g['mid']):>13} {lbl}"
                      + (f"  -> chapter {idx+1}" if idx is not None else ""))
            elif args.verbose:
                print(f"  {n:>4}/{len(todo)} {hms(g['mid']):>13} "
                      f"{e['text'][:50]!r}")
    else:
        # ---- adaptive ---------------------------------------------------
        # Seed with a handful spread across the book, fit a timeline from
        # whatever they confirm, then test only the predicted positions.
        # Stop once there are enough anchors to interpolate the rest.
        target = min(args.confirm, len(chapters) - 1)
        print(f"  adaptive: aiming for {target} confirmations rather than "
              f"transcribing all {len(cands)}")
        already = {round(h["mid"], 3) for h in heard}
        fresh = [g for g in cands if round(g["mid"], 3) not in already]
        n_seed = max(0, args.seeds - len(confirmed))
        seeds = spread_pick(fresh, n_seed, lambda g: g["mid"]) if n_seed else []
        if seeds:
            print(f"\n  seeding with {len(seeds)} new position(s) "
                  f"({len(already)} already read)")
        else:
            print(f"\n  enough is known already - skipping the seed phase")
        for g in seeds:
            segs, was_cached = transcribe(g)
            listened += 0 if was_cached else 1
            e = record(g, segs)
            idx = try_confirm(e)
            lbl = "-"
            if e["heading"]:
                lbl = e["heading"]["kind"] + (
                    f" {e['heading']['number']}" if e["heading"]["number"] else "")
            print(f"    {hms(g['mid']):>13}  {lbl}"
                  + (f"  -> chapter {idx+1}" if idx is not None else ""))

        if not confirmed:
            print("""
  No announcement at any position tried. Either the narrator does not name
  chapters, or the window is wrong. Try --post 20, or --strategy all to
  check every candidate.""")
        else:
            tested = {round(g["mid"], 3) for g in seeds} | already
            rounds = 0
            while len(confirmed) < target and rounds < args.confirm * 4:
                rounds += 1
                fit = fit_from_confirmed(confirmed, chapters)
                if not fit:
                    break
                scale, offset = fit
                # the most useful next chapter is the one furthest from any
                # existing confirmation - that is where the model is weakest
                best, best_gap = None, -1
                have = sorted(confirmed)
                for i in range(1, len(chapters)):
                    if i in confirmed:
                        continue
                    d = min((abs(i - h) for h in have), default=999)
                    if d > best_gap:
                        best, best_gap = i, d
                if best is None:
                    break
                predicted = scale * chapters[best]["start"] + offset
                near = [g for g in cands
                        if abs(g["mid"] - predicted) <= args.predict_window
                        and round(g["mid"], 3) not in tested]
                if not near:
                    tested.add(-best)          # nothing to try; do not loop
                    confirmed.setdefault(best, None)
                    confirmed.pop(best, None)
                    have.append(best)
                    continue
                near.sort(key=lambda g: abs(g["mid"] - predicted))
                g = near[0]
                tested.add(round(g["mid"], 3))
                segs, was_cached = transcribe(g)
                listened += 0 if was_cached else 1
                e = record(g, segs)
                idx = try_confirm(e)
                if args.verbose or idx is not None:
                    lbl = "-"
                    if e["heading"]:
                        lbl = e["heading"]["kind"] + (
                            f" {e['heading']['number']}"
                            if e["heading"]["number"] else "")
                    print(f"    {hms(g['mid']):>13}  predicted ch {best+1}"
                          f"  heard {lbl}"
                          + (f"  -> chapter {idx+1}" if idx is not None else ""))

    if strategy == "done":
        pass
    cache.flush()
    found = [h for h in heard if h["heading"]]
    art.data["heard"].update({"positions": heard, "pre": args.pre,
                              "post": args.post, "model": args.model})
    art.save()

    spent = listened * window
    print(f"\n  transcribed {listened} new clip(s) "
          f"({spent/60:.1f} min of audio, {100*spent/dur:.2f}% of the book)")
    if cache.hits:
        print(f"  reused {cache.hits} cached transcript(s)")
    print(f"  heard an announcement at {len(found)} position(s), "
          f"{len(confirmed)} matched to the chapter list")

    if not found:
        print("""
  Nothing recognisable. Either the narrator does not announce chapters, the
  announcement falls outside the window (try --post 20), or the model is too
  small for the numbers (try --model small).""")
        return
    if not chapters:
        print("\n  Run identify to map these onto a chapter list.")
        show_heard(found)
        return

    show_heard(found)
    pins = {i: t for i, t in confirmed.items() if t is not None}
    if pins and (args.pin or confirm_pin(args)):
        existing = art.data.get("pins") or {}
        for idx, t in pins.items():
            existing[str(idx)] = t
        art.data["pins"] = existing
        art.data.pop("alignment", None)
        art.save()
        print(f"\n  pinned {len(pins)} chapter(s) from what was heard")
        print(f"  next:  chapterfit.py align {os.path.basename(args.audio)!r}")
    elif pins:
        print("\n  (not pinned - add --pin to write these in)")


def confirm_pin(args):
    if args.yes:
        return True
    if not sys.stdin.isatty():
        return False
    return input("\n  pin these positions? (y/n) ").strip().lower().startswith("y")


def match_headings(found, chapters, slack):
    """Map each heard announcement onto a chapter in the published list.

    Matching on the spoken number is unambiguous where it works, which is the
    whole appeal: no alignment, no drift model, just the narrator telling you
    which chapter this is.
    """
    # published chapter -> its spoken identity
    want = {}
    for i, ch in enumerate(chapters):
        h = parse_heading(ch["title"])
        if h:
            key = (h["kind"], h["number"])
            want.setdefault(key, i)
    pins, unmatched = {}, []
    used = set()
    for h in found:
        key = (h["heading"]["kind"], h["heading"]["number"])
        idx = want.get(key)
        if idx is None and h["heading"]["number"] is not None:
            # the list may say "Chapter One: April 2005" while the narrator
            # says only "Chapter One" - the number alone is enough
            for (k2, n2), i2 in want.items():
                if n2 == h["heading"]["number"] and k2 in ("chapter", "part"):
                    idx = i2
                    break
        if idx is None or idx in used:
            unmatched.append(h)
            continue
        used.add(idx)
        if h.get("dur", 0) > 0 and not h.get("suspect"):
            pins[idx] = round(h["mid"], 3)          # the silence is the break
        else:
            pins[idx] = round(h.get("spoken_at") or h["mid"], 3)
        h["chapter_index"] = idx
    return pins, unmatched


def show_heard(found, pins=None):
    pins = pins or {}
    print(f"\n  {'position':>13} {'gap':>6}  heard")
    print("  " + "-" * 72)
    for h in found:
        hit = h["heading"]
        label = hit["kind"] + (f" {hit['number']}" if hit["number"] else "")
        idx = h.get("chapter_index")
        tag = f"  -> chapter {idx+1}" if idx is not None else "  (no match)"
        print(f"  {hms(h['mid']):>13} {h['dur']:>5.2f}s  {label:<22}{tag}")


