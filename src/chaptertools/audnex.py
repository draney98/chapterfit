"""Audnexus chapter fetch/parse (shared by silence and envelope pipelines)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from chaptertools.util import die

__all__ = [
    "AUDNEX_BASE",
    "USER_AGENT",
    "Chapter",
    "AudnexBook",
    "parse_audnex",
    "http_json",
    "fetch_audnex",
    "flatten_chapters",
]

AUDNEX_BASE = "https://api.audnex.us"
USER_AGENT = "chaptertools/1.0 (+audiobook chapter alignment)"


@dataclass
class Chapter:
    title: str
    start: float
    length: float | None


@dataclass
class AudnexBook:
    asin: str
    chapters: list[Chapter]
    brand_intro: float = 0.0
    brand_outro: float = 0.0
    runtime: float | None = None
    is_accurate: bool | None = None
    title: str | None = None
    author: str | None = None


def _flatten(raw_chapters, out):
    for c in raw_chapters:
        title = (c.get("title") or "").strip()
        start_ms = c.get("startOffsetMs", c.get("start_offset_ms"))
        length_ms = c.get("lengthMs", c.get("length_ms"))
        if start_ms is not None:
            out.append(Chapter(
                title=title or "Untitled",
                start=start_ms / 1000.0,
                length=(length_ms / 1000.0) if length_ms is not None else None,
            ))
        nested = c.get("chapters")
        if nested:
            _flatten(nested, out)
    return out


def parse_audnex(payload: dict, asin: str) -> AudnexBook:
    node = payload
    if "content_metadata" in node:
        node = node["content_metadata"].get("chapter_info", node["content_metadata"])

    raw = node.get("chapters")
    if not raw:
        raise ValueError("no 'chapters' array in response")

    chapters = sorted(_flatten(raw, []), key=lambda c: c.start)

    runtime_ms = node.get("runtimeLengthMs")
    runtime = runtime_ms / 1000.0 if runtime_ms else None
    if runtime is None and chapters and chapters[-1].length is not None:
        runtime = chapters[-1].start + chapters[-1].length

    return AudnexBook(
        asin=node.get("asin", asin),
        chapters=chapters,
        brand_intro=(node.get("brandIntroDurationMs") or 0) / 1000.0,
        brand_outro=(node.get("brandOutroDurationMs") or 0) / 1000.0,
        runtime=runtime,
        is_accurate=node.get("isAccurate"),
    )


def flatten_chapters(payload) -> tuple[list, dict]:
    """Dict-shaped chapters for the envelope pipeline."""
    node = payload
    if "content_metadata" in node:
        node = node["content_metadata"].get("chapter_info", node["content_metadata"])
    out = []

    def walk(items):
        for c in items:
            s = c.get("startOffsetMs", c.get("start_offset_ms"))
            if s is not None:
                out.append({"title": (c.get("title") or "Untitled").strip(),
                            "start": s / 1000.0})
            if c.get("chapters"):
                walk(c["chapters"])

    walk(node.get("chapters") or [])
    out.sort(key=lambda c: c["start"])
    meta = {
        "brand_intro": (node.get("brandIntroDurationMs") or 0) / 1000.0,
        "brand_outro": (node.get("brandOutroDurationMs") or 0) / 1000.0,
        "runtime": (node.get("runtimeLengthMs") or 0) / 1000.0 or None,
        "accurate": node.get("isAccurate"),
    }
    return out, meta


def http_json(url: str, timeout: int = 20, user_agent: str | None = None) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": user_agent or USER_AGENT,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_audnex(asin: str, region: str, cache_dir: str | None,
                 user_agent: str | None = None) -> AudnexBook:
    """Fetch with chapterize-style cache naming: {asin}.{region}.chapters.json."""
    cache_path = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, f"{asin}.{region}.chapters.json")
        if os.path.exists(cache_path):
            with open(cache_path) as f:
                return parse_audnex(json.load(f), asin)

    url = f"{AUDNEX_BASE}/books/{asin}/chapters?region={region}"
    try:
        payload = http_json(url, user_agent=user_agent)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        if e.code == 404:
            die(f"Audnexus has no chapter data for ASIN {asin} (region {region}).\n"
                f"  Chapter data needs Audible credentials on the server side and is\n"
                f"  missing for some titles. Try --region uk, or check the ASIN.")
        die(f"Audnexus HTTP {e.code} for {asin}: {body}")
    except urllib.error.URLError as e:
        die(f"could not reach Audnexus: {e.reason}")

    book = parse_audnex(payload, asin)

    try:
        meta = http_json(f"{AUDNEX_BASE}/books/{asin}?region={region}",
                         user_agent=user_agent)
        book.title = meta.get("title")
        authors = meta.get("authors") or []
        if authors:
            book.author = ", ".join(
                a.get("name", "") for a in authors if a.get("name"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            ValueError, KeyError, TypeError, OSError):
        pass

    if cache_path:
        with open(cache_path, "w") as f:
            json.dump(payload, f)

    return book
