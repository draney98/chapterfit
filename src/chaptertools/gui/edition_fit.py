"""Fit labels, verdict prose, and catalog names for the Match screen."""
from __future__ import annotations

import json
import os
from typing import Any

from chaptertools.gui.stages import identify_verdict
from chaptertools.sources.abs_match import (
    ABS_BOOK_PROVIDERS,
    PROVIDER_ALL,
    PROVIDER_FIND_ASIN,
    provider_choices,
)


def provider_catalog_labels() -> dict[str, str]:
    """Human Catalog combobox labels (never internal ids like find-asin/ALL)."""
    labels = {
        PROVIDER_FIND_ASIN: "Audible",
        PROVIDER_ALL: "All catalogs",
    }
    for name in ABS_BOOK_PROVIDERS:
        labels[f"abs:{name}"] = f"ABS · {name}"
    return labels


def source_group_label(source: str) -> str:
    """Muted divider text for a result source."""
    labels = provider_catalog_labels()
    if source in labels:
        return f"From {labels[source]}"
    if source.startswith("abs:"):
        return f"From ABS · {source.removeprefix('abs:')}"
    if source == PROVIDER_FIND_ASIN:
        return "From Audible lookup"
    return f"From {source}"


def format_length(runtime_min: int | float | None) -> str:
    if runtime_min is None:
        return ""
    total = int(round(float(runtime_min)))
    hours, minutes = divmod(max(0, total), 60)
    return f"{hours}:{minutes:02d}"


def fit_cell(fit: dict | None, *, checking: bool = False) -> str:
    if checking:
        return "checking…"
    if not fit:
        return "not checked"
    verdict = fit.get("verdict")
    if verdict == "consistent":
        return "✓ fits"
    if verdict in ("rejected", "weak"):
        return "✕ doesn't fit"
    return "not checked"


def fit_sort_key(row: dict) -> tuple:
    """Fits first, then by score descending, then not-checked last."""
    fit = row.get("fit") or {}
    verdict = fit.get("verdict")
    score = float(fit.get("score") or 0.0)
    if verdict == "consistent":
        rank = 0
    elif verdict in ("rejected", "weak"):
        rank = 1
    else:
        rank = 2
    return (rank, -score, row.get("asin") or "")


def verdict_headline(verdict: str | None) -> str:
    if verdict == "consistent":
        return "Fits this audio"
    if verdict in ("rejected", "weak"):
        return "Doesn't fit this audio"
    return ""


def _hms_short(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}:{m:02d}"


def reason_sentence(ident: dict) -> str:
    """One plain sentence for the verdict card."""
    if not ident:
        return ""
    verdict = identify_verdict(ident) or ident.get("verdict")
    ratio = ident.get("duration_ratio")
    file_rt = ident.get("file_runtime")
    pub_rt = ident.get("published_runtime")
    if isinstance(ratio, (int, float)) and ratio > 0:
        pct = abs(ratio - 1.0) * 100.0
        file_s = _hms_short(file_rt)
        pub_s = _hms_short(pub_rt)
        if ratio > 1.05:
            return (f"The file is {pct:.0f} % longer than this edition "
                    f"({file_s} vs {pub_s}). That usually means a different "
                    f"recording.")
        if ratio < 0.95:
            return (f"The file is {pct:.0f} % shorter than this edition "
                    f"({file_s} vs {pub_s}). That usually means an abridgement "
                    f"or a different recording.")
    blocked = ident.get("blocked") or []
    for raw in blocked:
        text = str(raw).lower()
        if "scatter" in text or "chance" in text:
            return ("Chapter breaks do not line up with the silences in this "
                    "file — most likely a different edition.")
        if "abridgement" in text or "different edition" in text:
            return ("Published runtime does not match this file — usually a "
                    "different recording or an abridgement.")
        if "no gap population" in text:
            return ("Not enough silence gaps to place this edition's chapters.")
    if verdict == "consistent":
        return "Chapter breaks line up with the silences in this file."
    if verdict == "weak":
        return ("Some chapters line up, but the fit is weak — check a few "
                "by ear before trusting it.")
    if verdict == "rejected":
        return ("This edition does not fit the audio. Try another row or "
                "search again.")
    return ""


def _length_words(ratio) -> str:
    if ratio is None:
        return ""
    value = float(ratio)
    if abs(value - 1.0) < 0.005:
        return "same length"
    return "longer than the edition" if value > 1 else "shorter than the edition"


def verdict_numbers(score, rate_spread, duration_ratio) -> str:
    """Fit, speed difference, and length. No raw ratios."""
    bits = []
    if score is not None:
        from chaptertools.gui.ui_kit import fmt_fit
        bits.append(f"Fit {fmt_fit(score)} (5 or more fits)")
    if rate_spread is not None:
        bits.append(f"speed difference {float(rate_spread) * 100:.1f} %")
    length = _length_words(duration_ratio)
    if length:
        bits.append(length)
    return " · ".join(bits)


def numbers_line(ident: dict) -> str:
    """Muted summary under the reason sentence."""
    if not ident:
        return ""
    best = ident.get("best_trial") or {}
    return verdict_numbers(
        best.get("score"), best.get("rate_spread"), ident.get("duration_ratio"))


def plain_found_summary(harvest: dict | None) -> str:
    """Book-info footer: no raw ID3 / nfo dumps."""
    harvest = harvest or {}
    if harvest.get("loading"):
        return "Reading tags and chapters from the file…"
    tags = harvest.get("tags") or []
    if isinstance(tags, dict):
        names = sorted(str(k) for k in tags if tags.get(k))
    else:
        names = sorted(str(t) for t in tags if t)
    # Drop encoder noise from the footer.
    names = [n for n in names if n.lower() not in ("encoder", "encoded_by")]
    found = ", ".join(names) if names else "nothing"
    embedded = harvest.get("embedded_chapters")
    if isinstance(embedded, list):
        n_ch = len(embedded)
    elif embedded is None:
        n_ch = len(harvest.get("chapters") or [])
    else:
        n_ch = int(embedded or 0)
    ch_bit = (f"{n_ch} chapters" if n_ch else "no chapters")
    return (f"Written as tags at Finish · Found in file: {found} · {ch_bit}")


def fit_cache_path(audio: str, work_dir: str) -> str:
    base = os.path.basename(audio)
    return os.path.join(work_dir, f"{base}.edition_fits.json")


def load_fit_cache(audio: str, work_dir: str | None) -> dict[str, dict]:
    if not audio or not work_dir:
        return {}
    path = fit_cache_path(audio, work_dir)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    fits = data.get("fits") if isinstance(data, dict) else None
    if not isinstance(fits, dict):
        return {}
    return {str(k): v for k, v in fits.items() if isinstance(v, dict)}


def save_fit_cache(audio: str, work_dir: str, fits: dict[str, dict]) -> None:
    path = fit_cache_path(audio, work_dir)
    os.makedirs(work_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"fits": fits}, handle, indent=2)


def fit_from_identification(ident: dict,
                            file_runtime: float | None = None) -> dict[str, Any]:
    """Compact cache row from an identify report."""
    best = ident.get("best_trial") or {}
    return {
        "verdict": identify_verdict(ident) or "rejected",
        "score": best.get("score"),
        "rate_spread": best.get("rate_spread"),
        "chapter_count": len(ident.get("chapters") or []),
        "duration_ratio": ident.get("duration_ratio"),
        "published_runtime": ident.get("published_runtime"),
        "file_runtime": ident.get("file_runtime") or file_runtime,
        "blocked": list(ident.get("blocked") or []),
        "label": ident.get("label") or "",
    }


# Re-export for callers that need the choice list next to labels.
__all__ = [
    "fit_cell",
    "fit_from_identification",
    "fit_sort_key",
    "format_length",
    "load_fit_cache",
    "numbers_line",
    "plain_found_summary",
    "provider_catalog_labels",
    "provider_choices",
    "reason_sentence",
    "save_fit_cache",
    "source_group_label",
    "verdict_headline",
]
