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
    "AudnexError",
    "cache_file",
    "read_cached_chapters",
    "fetch_chapters_payload",
    "chapters_payload",
    "fetch_book_meta",
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


class AudnexError(Exception):
    """A chapter list could not be had; the message is for the user."""


def cache_file(cache_dir: str, asin: str, region: str) -> str:
    """The one cache name both pipelines write."""
    return os.path.join(cache_dir, f"chapters.{asin}.{region}.json")


def _legacy_cache_file(cache_dir: str, asin: str, region: str) -> str:
    """Name the silence pipeline used to write; still read."""
    return os.path.join(cache_dir, f"{asin}.{region}.chapters.json")


def read_cached_chapters(cache_dir: str | None, asin: str,
                         region: str) -> tuple[dict, str] | None:
    """(payload, path) from the cache, or None."""
    if not cache_dir:
        return None
    for path in (cache_file(cache_dir, asin, region),
                 _legacy_cache_file(cache_dir, asin, region)):
        try:
            with open(path) as f:
                return json.load(f), path
        except (OSError, ValueError):
            continue
    return None


def fetch_chapters_payload(asin: str, region: str,
                           user_agent: str | None = None,
                           timeout: int = 25) -> dict:
    """The raw Audnexus chapter payload from the network."""
    url = f"{AUDNEX_BASE}/books/{asin}/chapters?region={region}"
    try:
        return http_json(url, timeout=timeout, user_agent=user_agent)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:400]
        if e.code == 404:
            raise AudnexError(
                f"Audnexus has no chapter data for ASIN {asin} (region "
                f"{region}).\n  Chapter data needs Audible credentials on the "
                f"server side and is\n  missing for some titles. Try "
                f"--region uk, or check the ASIN.") from e
        raise AudnexError(f"Audnexus HTTP {e.code} for {asin}: {body}") from e
    except urllib.error.URLError as e:
        raise AudnexError(f"could not reach Audnexus: {e.reason}") from e
    except (TimeoutError, OSError, ValueError) as e:
        raise AudnexError(f"could not read Audnexus reply: {e}") from e


def chapters_payload(asin: str, region: str, cache_dir: str | None = None,
                     offline: bool = False, user_agent: str | None = None,
                     on_cached=None) -> dict:
    """Cached payload if present, else fetched (and cached). Raises
    AudnexError. `on_cached(path)` is told when the cache answered."""
    hit = read_cached_chapters(cache_dir, asin, region)
    if hit is not None:
        payload, path = hit
        if on_cached:
            on_cached(path)
        return payload
    if offline:
        raise AudnexError(f"no cached chapter data for {asin}/{region} and "
                          f"--offline was set")
    payload = fetch_chapters_payload(asin, region, user_agent=user_agent)
    if cache_dir:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_file(cache_dir, asin, region), "w") as f:
                json.dump(payload, f)
        except OSError:
            pass
    return payload


def fetch_book_meta(asin: str, region: str,
                    user_agent: str | None = None) -> tuple[str | None, str | None]:
    """(title, author) from Audnexus, or Nones; never raises."""
    try:
        meta = http_json(f"{AUDNEX_BASE}/books/{asin}?region={region}",
                         user_agent=user_agent)
        authors = meta.get("authors") or []
        author = ", ".join(a.get("name", "") for a in authors
                           if a.get("name")) or None
        return meta.get("title"), author
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            ValueError, KeyError, TypeError, AttributeError, OSError):
        return None, None


def fetch_audnex(asin: str, region: str, cache_dir: str | None,
                 user_agent: str | None = None) -> AudnexBook:
    """Parsed chapter list (cached as chapters.{asin}.{region}.json) plus
    title/author when the network answers."""
    fetched = read_cached_chapters(cache_dir, asin, region) is None
    try:
        payload = chapters_payload(asin, region, cache_dir,
                                   user_agent=user_agent)
    except AudnexError as e:
        die(str(e))
    book = parse_audnex(payload, asin)
    if fetched:
        book.title, book.author = fetch_book_meta(asin, region, user_agent)
    return book
