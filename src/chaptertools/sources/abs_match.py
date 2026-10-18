"""Map Audiobookshelf Match-provider hits into find_asin candidates."""
from __future__ import annotations

import re

from chaptertools.sources.find_asin import Candidate

# Book metadata providers exposed by ABS Match (not cover-only).
ABS_BOOK_PROVIDERS = (
    "audible",
    "google",
    "openlibrary",
    "itunes",
    "audnexus",
    "fantlab",
    "hardcover",
)

PROVIDER_FIND_ASIN = "find-asin"
PROVIDER_ALL = "all"

# Audible ASINs used by Audnexus identify (e.g. B002V5GYHA).
_ASIN_RE = re.compile(r"^B0[0-9A-Z]{8}$", re.IGNORECASE)


def provider_choices() -> list[str]:
    """CLI/GUI values: find-asin, abs:<name>…, all."""
    return [PROVIDER_FIND_ASIN,
            *[f"abs:{name}" for name in ABS_BOOK_PROVIDERS],
            PROVIDER_ALL]


def provider_labels() -> dict[str, str]:
    """Human Catalog combobox labels (never internal ids)."""
    labels = {
        PROVIDER_FIND_ASIN: "Audible",
        PROVIDER_ALL: "All catalogs",
    }
    for name in ABS_BOOK_PROVIDERS:
        labels[f"abs:{name}"] = f"ABS · {name}"
    return labels


def is_audible_asin(value: str | None) -> bool:
    return bool(value and _ASIN_RE.match(value.strip()))


def _split_names(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
            else:
                name = str(item).strip()
            if name:
                out.append(name)
        return out
    text = str(value).strip()
    if not text:
        return []
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text]


def _runtime_min(hit: dict) -> int | None:
    for key in ("durationMin", "runtime_min", "runtimeLengthMin"):
        value = hit.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(round(value))
    duration = hit.get("duration")
    if isinstance(duration, (int, float)) and duration > 0:
        # ABS Audible often sends seconds; values under a day of minutes
        # that look like seconds (> 600) convert to minutes.
        if duration > 600:
            return int(round(duration / 60.0))
        return int(round(duration))
    return None


def candidate_from_abs_hit(hit: dict, source: str) -> Candidate | None:
    """One ABS search hit → Candidate, or None when there is no Audible ASIN."""
    asin = str(hit.get("asin") or "").strip()
    if not is_audible_asin(asin):
        return None
    year = hit.get("publishedYear") or hit.get("year")
    release = None
    if year is not None and str(year).strip():
        text = str(year).strip()
        release = text if len(text) >= 4 else text
    authors = _split_names(hit.get("author") or hit.get("authors"))
    narrators = _split_names(hit.get("narrator") or hit.get("narrators"))
    return Candidate(
        asin=asin.upper(),
        title=str(hit.get("title") or "").strip(),
        subtitle=(str(hit["subtitle"]).strip()
                  if hit.get("subtitle") not in (None, "") else None),
        authors=authors,
        narrators=narrators,
        runtime_min=_runtime_min(hit),
        release=release[:10] if release else None,
        publisher=(str(hit["publisher"]).strip()
                   if hit.get("publisher") not in (None, "") else None),
        language=(str(hit["language"]).strip()
                  if hit.get("language") not in (None, "") else None),
        source=source,
    )


def candidates_from_abs_hits(hits: list[dict], source: str) -> list[Candidate]:
    out = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        cand = candidate_from_abs_hit(hit, source)
        if cand is not None:
            out.append(cand)
    return out


def merge_by_asin(groups: list[list[Candidate]]) -> list[Candidate]:
    """Keep the highest-scoring candidate per ASIN; preserve source."""
    best: dict[str, Candidate] = {}
    for group in groups:
        for cand in group:
            key = cand.asin.upper()
            if cand.asin != key:
                cand.asin = key
            prev = best.get(key)
            if prev is None or cand.score > prev.score:
                best[key] = cand
    return sorted(best.values(), key=lambda c: c.score, reverse=True)
