"""Stage navigation, escalation ladder and emit staleness (pure, headless)."""
from chaptertools.envelope.emit import alignment_fingerprint, select_chapters
from chaptertools.gui.stages import (
    DiskFacts,
    next_stage,
    recommend,
    resolve_evidence,
    stage_states,
)


def measured(**kw):
    d = {"envelope": {"duration": 3600.0}, "calibration": {"x": 1},
         "gaps": {"gaps": [1]}, "fingerprint": {"x": 1},
         "source": {"timeline_trustworthy": True}}
    d.update(kw)
    return d


def res(i, time, conf, pinned=False):
    return {"index": i, "title": f"c{i}", "published": float(i * 600),
            "time": time, "confidence": conf, "pinned": pinned}


IDENT = {"best_trial": {"score": 6.0}, "chapters": [{}, {}, {}]}


def test_dependencies_are_blocked_not_pending():
    states = stage_states(measured(), 0.5, DiskFacts())
    assert states["align"]["state"] == "blocked"
    assert states["align"]["why"] == "identify first"
    assert states["resolve"]["state"] == "blocked"
    assert states["emit"]["state"] == "blocked"
    assert states["finalize"]["state"] == "blocked"
    assert next_stage(states) == "identify"


def test_weak_alignment_opens_resolve_not_align():
    al = {"results": [res(0, 0.0, 1.0), res(1, 600.0, 0.2)]}
    states = stage_states(measured(identification=IDENT, alignment=al),
                          0.5, DiskFacts())
    assert states["align"]["state"] == "attention"  # warning still shown
    assert next_stage(states) == "resolve"


def test_clean_alignment_opens_emit():
    al = {"results": [res(0, 0.0, 1.0), res(1, 600.0, 0.9)]}
    states = stage_states(measured(identification=IDENT, alignment=al),
                          0.5, DiskFacts())
    assert next_stage(states) == "emit"


def test_bad_header_keeps_measure_open():
    states = stage_states(measured(source={"timeline_trustworthy": False}),
                          0.5, DiskFacts())
    assert next_stage(states) == "measure"


def test_emit_goes_stale_when_alignment_changes():
    al = {"results": [res(0, 0.0, 1.0), res(1, 600.0, 0.9)]}
    emitted = {"count": 2, "last": 600.0,
               "alignment_sig": alignment_fingerprint(al)}
    a = measured(identification=IDENT, alignment=al, emitted=emitted)
    ok = DiskFacts(chapters_file_match=True)
    assert stage_states(a, 0.5, ok)["emit"]["state"] == "done"
    al["results"][1]["time"] = 610.0  # re-aligned elsewhere
    st = stage_states(a, 0.5, ok)
    assert st["emit"]["state"] == "pending"
    assert "changed" in st["emit"]["detail"]
    # Finish waits on Place, not Emit; Place is still done.
    assert st["finalize"]["state"] == "pending"
    assert st["finalize"]["detail"] == "needs a dry run"


def test_select_chapters_matches_emit_rule():
    results = [res(0, 0.0, 0.1), res(1, 100.0, 0.9), res(2, 101.0, 0.9),
               res(3, None, 0.0), res(4, 500.0, 0.1, pinned=True),
               res(5, 900.0, 0.3)]
    kept, withheld = select_chapters(results, 0.5)
    assert [r["index"] for r in kept] == [0, 1, 4]  # 2 is a <2s shadow
    assert withheld == {3, 4, 6}


def clip(start, length, sweep=False):
    return f"{start:.2f}|{length:.2f}|{'sweep:' if sweep else ''}base#w2"


def test_ladder_listen_then_probe_or_range():
    weak = [res(2, 1200.0, 0.1)]
    assert recommend({}, []) == "By ear"
    assert recommend({}, weak) == "Listen"
    heard_nothing = {"heard": {"clips": {clip(10, 13): {}}, "positions": []}}
    assert recommend(heard_nothing, weak) == "Sweep probe"
    heard = {"heard": {"clips": {clip(10, 13): {}}, "positions": [
        {"heading": "Chapter 1", "source": "listen"}]}}
    assert recommend(heard, weak) == "Sweep range"


def test_ladder_after_sweeps():
    weak = [res(2, 1200.0, 0.1), res(5, 3000.0, 0.1)]
    base = {clip(10, 13): {}}
    covered = {"heard": {"clips": {**base, clip(900, 2400, True): {}},
                         "positions": [{"heading": "x", "source": "sweep"}]}}
    assert recommend(covered, weak) == "By ear"
    partial = {"heard": {"clips": {**base, clip(900, 600, True): {}},
                         "positions": [{"heading": "x", "source": "sweep"}]}}
    assert recommend(partial, weak) == "Full sweep"
    silent = {"heard": {"clips": {**base, clip(900, 600, True): {}},
                        "positions": []}}
    assert recommend(silent, weak) == "By ear"
    ev = resolve_evidence(partial)
    assert ev["swept_minutes"] == 10.0 and ev["sweep_heard"] == 1
