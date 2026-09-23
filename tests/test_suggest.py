"""Characterization tests for suggest_measurements (moved to alignment)."""
from chaptertools.envelope.alignment import suggest_measurements


class _Art:
    def __init__(self, dur=3600.0):
        self.data = {"envelope": {"duration": dur}}


def art(dur=3600.0):
    return _Art(dur)


def r(i, time, conf, spread=0.0, pinned=False):
    return {"index": i, "time": time, "confidence": conf,
            "spread": spread, "pinned": pinned}


def test_skips_first_pinned_and_unplaced():
    al = {"results": [
        r(0, 0.0, 1.0),
        r(1, 100.0, 1.0, pinned=True),
        r(2, None, 0.0),
        r(3, 300.0, 0.5),
    ]}
    out = suggest_measurements(art(), al, n=10)
    assert [o["index"] for o in out] == [3]


def test_uncertainty_times_leverage_ordered():
    # chapter far from any pin with low confidence scores highest
    al = {"results": [
        r(0, 0.0, 1.0),
        r(1, 100.0, 0.9),                 # near the pin -> low leverage
        r(2, 3000.0, 0.2, spread=12.0),   # far away, very uncertain
    ]}
    out = suggest_measurements(art(), al, n=5)
    assert [o["index"] for o in out] == [2, 1]


def test_spreads_keep_distance_apart():
    al = {"results": [
        r(0, 0.0, 1.0),
        r(1, 100.0, 0.0),
        r(2, 500.0, 0.0),
        r(3, 900.0, 0.0),
    ]}
    out = suggest_measurements(art(dur=900.0), al, n=2)
    picks = [o["index"] for o in out]
    assert picks[0] in (1, 2, 3)
    assert len(picks) == 2
    times = {o["index"]: o["time"] for o in out}
    a, b = list(times.values())
    assert abs(a - b) >= 900.0 * 0.1 - 1e-9


def test_near_confident_skipped():
    al = {"results": [
        r(0, 0.0, 1.0),
        r(1, 100.0, 0.99, spread=0.0),   # unc < 0.02 -> skip
    ]}
    out = suggest_measurements(art(), al, n=5)
    assert out == []
