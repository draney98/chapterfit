"""The extracted ranking core reproduces the original suggest_measurements."""
import math
import random

from chaptertools.envelope.alignment import (
    measurement_value,
    pinned_times,
    suggest_measurements,
)


def _original(art, al, n=2):
    """suggest_measurements as it was before the core was extracted."""
    res = al["results"]
    pinned = [r["time"] for r in res if r["pinned"] and r["time"] is not None]
    dur = art.data["envelope"]["duration"]
    scored = []
    for r in res:
        if r["index"] == 0 or r["pinned"] or r["time"] is None:
            continue
        lev = min((abs(r["time"] - p) for p in pinned), default=dur / 2)
        unc = (1 - r["confidence"]) + (r["spread"] or 0) / 60.0
        if unc < 0.02:
            continue
        scored.append((unc * math.sqrt(lev), r))
    scored.sort(key=lambda x: -x[0])
    out, used = [], []
    for _, r in scored:
        if any(o["time"] is not None
               and abs(r["time"] - o["time"]) < dur * 0.1 for o in used):
            continue
        used.append(r)
        out.append(r)
        if len(out) >= n:
            break
    return out


class _Art:
    def __init__(self, dur):
        self.data = {"envelope": {"duration": dur}}


def _book(rng):
    dur = rng.uniform(600, 60000)
    n = rng.randint(1, 60)
    res = []
    for i in range(n):
        placed = rng.random() > 0.15
        res.append({
            "index": i,
            "time": rng.uniform(0, dur) if placed else None,
            "confidence": rng.choice([1.0, 0.99, rng.random()]),
            "spread": rng.choice([None, 0.0, rng.uniform(0, 120)]),
            "pinned": placed and rng.random() < 0.15,
        })
    return _Art(dur), {"results": res}


def test_identical_to_original_on_random_books():
    rng = random.Random(1234)
    for _ in range(500):
        art, al = _book(rng)
        for n in (1, 2, 5):
            assert suggest_measurements(art, al, n) == _original(art, al, n)


def test_value_of_unplaced_chapter_uses_half_the_book():
    r = {"index": 3, "time": None, "confidence": None, "spread": None}
    assert measurement_value(r, [100.0], 3600.0) == math.sqrt(1800.0)


def test_pinned_times_skips_unplaced_pins():
    res = [{"time": 5.0, "pinned": True}, {"time": None, "pinned": True},
           {"time": 9.0, "pinned": False}]
    assert pinned_times(res) == [5.0]
