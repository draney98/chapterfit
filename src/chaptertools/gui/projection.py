"""Pure view of one book's artifact: stages, table rows, result copy.

Tk never imports this. The GUI asks for a projection after every artifact
change and then paints; tests ask the same questions without a window.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field

from chaptertools.envelope.alignment import measurement_value, pinned_times
from chaptertools.envelope.emit import select_chapters
from chaptertools.util import hms, parse_time

from .stages import (
    PENDING,
    DiskFacts,
    identify_verdict,
    next_stage,
    recommend,
    stage_states,
)


@dataclass(frozen=True)
class TableRow:
    """One chapter as the table paints it."""
    index: int
    mark: str
    title: str
    published: float | None
    placed: float | None
    gap: str
    confidence: float | None
    pin: str
    tags: tuple[str, ...]
    source: dict


@dataclass
class WorkflowProjection:
    """Everything the main area needs to draw, from artifact + threshold."""

    analysis: dict
    min_conf: float
    disk: DiskFacts
    audio: str | None = None
    nfo_chapters: list | None = None
    states: dict = field(init=False)
    rows: list[TableRow] = field(init=False)
    weak: list[dict] = field(init=False)
    recommendation: str = field(init=False)
    next: str = field(init=False)
    conflicts_unknown: bool = field(init=False)
    confident_count: int = field(init=False)
    measure_text: str = field(init=False)
    identify_text: str = field(init=False)
    identify_button: str = field(init=False)
    align_text: str = field(init=False)
    emit_text: str = field(init=False)
    header_fix: str | None = field(init=False)

    def __post_init__(self):
        self.states = stage_states(self.analysis, self.min_conf, self.disk)
        self.next = next_stage(self.states)
        self.rows = self._rows()
        self.weak = self._weak()
        self.recommendation = recommend(self.analysis, self.weak)
        self.conflicts_unknown = self._conflicts_unknown()
        self.confident_count = sum(
            1 for r in self.rows
            if r.placed is not None
            and (r.confidence or 0) >= self.min_conf)
        self.header_fix = self._header_fix()
        self.measure_text = self._measure_text()
        self.identify_text, self.identify_button = self._identify()
        self.align_text = self._align_text()
        self.emit_text = self._emit_text()

    def _display_results(self) -> list[dict]:
        nfo = self.nfo_chapters or []
        if len(nfo) >= 2:
            ident_ch = ((self.analysis.get("identification") or {})
                        .get("chapters") or [])
            rows = []
            for i, c in enumerate(nfo):
                published = None
                if i < len(ident_ch):
                    published = ident_ch[i].get("start")
                rows.append({
                    "index": i, "title": c.get("title", ""),
                    "published": published, "time": c.get("start"),
                    "confidence": 1.0, "pinned": False, "nfo": True,
                })
            return rows
        alignment = self.analysis.get("alignment") or {}
        results = list(alignment.get("results") or [])
        if results:
            return results
        ident = self.analysis.get("identification") or {}
        source = ident.get("chapters") or []
        pins_now = self.analysis.get("pins") or {}
        return [
            {"index": i, "title": c.get("title", ""),
             "published": c.get("start"),
             "time": pins_now.get(str(i)),
             "confidence": 1.0 if str(i) in pins_now else None,
             "pinned": str(i) in pins_now}
            for i, c in enumerate(source)]

    def _conflicts_unknown(self) -> bool:
        alignment = self.analysis.get("alignment") or {}
        if not alignment or "conflicts" in alignment:
            return False
        return any(r.get("pinned") for r in alignment.get("results") or [])

    def _rows(self) -> list[TableRow]:
        results = self._display_results()
        alignment = self.analysis.get("alignment") or {}
        aligned = bool(alignment) and not any(r.get("nfo") for r in results)
        withheld = select_chapters(results, self.min_conf)[1] if aligned else set()
        conflicts = set(alignment.get("conflicts") or [])
        gap_at = {round(g["mid"], 3): g for g in
                  (self.analysis.get("gaps") or {}).get("gaps") or []
                  if isinstance(g, dict) and g.get("mid") is not None}
        rows = []
        for r in results:
            n = r["index"] + 1
            mark = f"?{n}" if aligned and n in withheld else str(n)
            tags: list[str] = []
            pin = "pin" if r.get("pinned") else ""
            if aligned and r.get("pinned") and r["index"] in conflicts:
                tags.append("conflict")
                pin = "! pin"
            elif r.get("time") is None:
                tags.append("unplaced")
            elif aligned and n in withheld:
                tags.append("lowconf")
            g = gap_at.get(round(r["time"], 3)) if r.get("time") is not None else None
            gap = (f"{g['dur']:.2f} s" if g and g.get("dur") is not None else "")
            rows.append(TableRow(
                index=r["index"], mark=mark, title=r.get("title", ""),
                published=r.get("published"), placed=r.get("time"),
                gap=gap, confidence=r.get("confidence"), pin=pin,
                tags=tuple(tags), source=r))
        return rows

    def _weak(self) -> list[dict]:
        results = (self.analysis.get("alignment") or {}).get("results") or []
        dur = (self.analysis.get("envelope") or {}).get("duration") or 0.0
        pins = pinned_times(results)
        return sorted(
            (r for r in results if r["index"] != 0
             and not r.get("pinned")
             and (r.get("time") is None
                  or (r.get("confidence") or 0) < self.min_conf)),
            key=lambda r: measurement_value(r, pins, dur),
            reverse=True)

    def _header_fix(self) -> str | None:
        src = self.analysis.get("source") or {}
        if not self.audio or src.get("timeline_trustworthy", True):
            return None
        stem, ext = os.path.splitext(self.audio)
        return (f"ffmpeg -i {shlex.quote(self.audio)} -c:a copy "
                f"-write_xing 1 {shlex.quote(stem + '.fixed' + ext)}")

    def _measure_text(self) -> str:
        if self.states["measure"]["state"] == PENDING:
            return ""
        gaps = self.analysis.get("gaps") or {}
        pops = gaps.get("populations") or []
        src = self.analysis.get("source") or {}
        lines = [f"{len(gaps.get('gaps') or [])} gaps in {len(pops)} "
                 f"length populations"
                 + (f"; longest: {pops[-1]['n']} around "
                    f"{pops[-1]['centre']:.1f} s" if pops else "")]
        if src.get("declared_duration") is not None \
                and src.get("decoded_duration") is not None:
            lines.append(
                f"header says {hms(src['declared_duration'])}, audio is "
                f"{hms(src['decoded_duration'])} "
                f"({src.get('header_error_sec', 0):+.1f} s)")
        if self.header_fix:
            lines.append(
                f"The header is wrong by "
                f"{src.get('header_error_ratio', 0) * 100:.1f}% - every "
                f"seek lands off. Fix it, then re-run Measure:\n    "
                f"{self.header_fix}")
        return "\n".join(lines)

    def _identify(self) -> tuple[str, str]:
        ident = self.analysis.get("identification") or {}
        verdict = identify_verdict(ident)
        best = ident.get("best_trial") or {}
        if not ident:
            return "", "Identify"
        bits = [ident.get("label") or "?",
                f"{len(ident.get('chapters') or [])} chapters"]
        if best.get("score") is not None:
            bits.append(f"fit score {best['score']:.1f}")
        if best.get("rate_spread") is not None:
            bits.append(f"rate spread {best['rate_spread'] * 100:.1f}%")
        if ident.get("duration_ratio"):
            bits.append(f"runtime ratio {ident['duration_ratio']:.3f}")
        text = " · ".join(bits) + {
            "consistent": " - consistent: this edition fits the audio",
            "weak": " - weak: positions are provisional, check a few by "
                    "ear",
            "rejected": " - rejected: most likely a different edition; "
                        "try another ASIN or chapter file",
        }[verdict]
        for reason in ident.get("blocked") or []:
            text += f"\n! {reason}"
        button = "Try another edition" if verdict == "rejected" else "Identify"
        return text, button

    def _align_text(self) -> str:
        al = self.analysis.get("alignment") or {}
        if not al:
            return ""
        rate = al.get("rate")
        drift = f"{(rate - 1) * 3600:+.1f}s/h" if rate else "?"
        placed = sum(1 for r in al["results"] if r["time"] is not None)
        return f"placed {placed}/{len(al['results'])} - rate {rate} ({drift})"

    def _emit_text(self) -> str:
        emitted = self.analysis.get("emitted") or {}
        if not emitted:
            return ""
        total = len((self.analysis.get("alignment") or {}).get("results") or [])
        return (f"{emitted.get('count')} embedded, "
                f"{total - emitted.get('count', 0)} withheld")


def transcript_text(analysis: dict) -> str:
    """Transcript tab body from the artifact's heard clips, or empty."""
    from chaptertools.asr import collect_transcripts

    class _Heard:
        def __init__(self, data):
            self.data = data

    lines = []
    for t, _model, text, _words in collect_transcripts(_Heard(analysis or {})):
        if text:
            lines.append(f"{hms(t):>13}  {text}")
    return "\n".join(lines)


def filter_transcript(body: str, pattern: str) -> str:
    """Keep lines matching `pattern`. An empty pattern keeps every line."""
    pattern = (pattern or "").strip()
    if not pattern:
        return body
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"bad regex: {e}"
    return "\n".join(ln for ln in body.splitlines() if rx.search(ln))


def transcript_line_time(line: str) -> float | None:
    """The leading H:MM:SS.mmm on a transcript line, or None."""
    token = line.strip().split(None, 1)
    if not token or ":" not in token[0]:
        return None
    try:
        return parse_time(token[0])
    except ValueError:
        return None
