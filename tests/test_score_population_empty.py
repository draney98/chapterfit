"""score_population must survive a solver that places nothing."""
from __future__ import annotations

from chaptertools.envelope.alignment import score_population, solve


def test_solve_empty_assignment_includes_rate_median():
    chapters = [{"title": f"C{i}", "start": float(s)}
                for i, s in enumerate([0, 100, 200, 300])]
    assign, st = solve(chapters, cands=[], pins={}, max_off=40.0,
                       w_rate=8.0, skip_penalty=3.0)
    assert assign == {}
    assert st["assigned"] == 0
    assert "rate_median" in st
    assert st["rate_median"] is None


def test_score_population_with_no_candidates():
    """Extreme edition mismatch (e.g. 58m ASIN vs 6h file) can yield no
    assignable gaps; identify must not KeyError on rate_median."""
    chapters = [{"title": f"C{i}", "start": float(s)}
                for i, s in enumerate([0, 100, 200, 300, 400, 500])]
    sc = score_population(
        chapters, [], dur=400.0, pins={}, max_off=40.0,
        w_rate=8.0, skip_penalty=3.0, tol=4.0)
    assert sc["assigned"] == 0
    assert sc["rate_median"] is None
    assert sc["rate_spread"] is None
    assert sc["score"] == 0.0
