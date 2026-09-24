"""Which placed chapters emit writes, shared by the CLI and the GUI."""
from __future__ import annotations

import hashlib
import json

DEDUP_SECONDS = 2.0


def select_chapters(results: list[dict], thr: float) -> tuple[list[dict], set[int]]:
    """Split alignment results into (kept, withheld chapter numbers).

    Kept: placed chapters at confidence >= thr, plus pins and chapter 1
    regardless of confidence, sorted by time, dropping any mark within
    DEDUP_SECONDS of the previous kept one. Withheld: every 1-based
    chapter number not kept.
    """
    keep = [r for r in results
            if r.get("time") is not None
            and (r.get("pinned") or r["index"] == 0
                 or (r.get("confidence") or 0) >= thr)]
    keep.sort(key=lambda r: r["time"])
    kept: list[dict] = []
    for r in keep:
        if kept and r["time"] - kept[-1]["time"] < DEDUP_SECONDS:
            continue
        kept.append(r)
    kept_numbers = {r["index"] + 1 for r in kept}
    withheld = {r["index"] + 1 for r in results} - kept_numbers
    return kept, withheld


def alignment_fingerprint(alignment: dict | None) -> str | None:
    """Stable digest of what an alignment places, for emit staleness."""
    if not alignment:
        return None
    placed = [(r["index"], None if r.get("time") is None
               else round(r["time"], 3), bool(r.get("pinned")))
              for r in alignment.get("results") or []]
    return hashlib.sha1(json.dumps(placed).encode()).hexdigest()
