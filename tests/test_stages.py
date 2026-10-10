"""Stage state machine tests (pure, headless)."""
from chaptertools.gui.stages import DiskFacts, stage_states


def base_analysis(**kw):
    d = {
        "envelope": {"frames": 1}, "calibration": {"threshold_db": -60},
        "gaps": {"gaps": [1, 2, 3]}, "fingerprint": {"longest_gap": 1},
        "source": {"timeline_trustworthy": True},
    }
    d.update(kw)
    return d


def test_all_pending_without_book():
    states = stage_states({}, 0.5, DiskFacts())
    assert states["measure"]["state"] == "pending"
    assert not states["measure"]["blocked"]  # first stage is never blocked
    assert states["emit"]["blocked"]
    assert states["finalize"]["blocked"]


def test_measure_done_and_bad_header():
    states = stage_states(base_analysis(), 0.5, DiskFacts())
    assert states["measure"]["state"] == "done"
    bad = base_analysis(source={"timeline_trustworthy": False})
    states = stage_states(bad, 0.5, DiskFacts())
    assert states["measure"]["state"] == "attention"
    assert "header timing is wrong" in states["measure"]["detail"]


def test_identify_verdicts():
    # the user chose the source: identify is done the moment it runs,
    # whatever the fit score says - the warning belongs to Align
    ident = {"best_trial": {"score": 6.0}, "chapters": [1, 2]}
    states = stage_states(base_analysis(identification=ident), 0.5,
                          DiskFacts())
    assert states["identify"]["state"] == "done"

    weak = base_analysis(identification={"best_trial": {"score": 3.0},
                                         "chapters": [1, 2]})
    states = stage_states(weak, 0.5, DiskFacts())
    assert states["identify"]["state"] == "done"
    assert states["align"]["state"] == "pending"  # not aligned yet

    rejected = base_analysis(identification={"best_trial": {"score": 1.0},
                                             "chapters": [1, 2]})
    states = stage_states(rejected, 0.5, DiskFacts())
    assert states["identify"]["state"] == "done"


def test_align_carries_the_fit_warning():
    ident = {"best_trial": {"score": 3.5}, "chapters": [1, 2]}
    results = [
        {"index": 0, "time": 0.0, "confidence": 1.0, "pinned": False},
        {"index": 1, "time": 100.0, "confidence": 0.9, "pinned": False},
    ]
    states = stage_states(
        base_analysis(identification=ident,
                      alignment={"results": results}), 0.5, DiskFacts())
    assert states["align"]["state"] == "attention"
    assert "weak" in states["align"]["detail"]

    # rejected edition: still runnable, louder warning
    ident["best_trial"]["score"] = 1.0
    states = stage_states(
        base_analysis(identification=ident,
                      alignment={"results": results}), 0.5, DiskFacts())
    assert states["align"]["state"] == "attention"
    assert "rejected" in states["align"]["detail"]


def test_align_resolve_weak_counts():
    ident = {"best_trial": {"score": 6.0}}
    results = [
        {"index": 0, "time": 0.0, "confidence": 1.0, "pinned": False},
        {"index": 1, "time": 100.0, "confidence": 0.9, "pinned": False},
        {"index": 2, "time": 200.0, "confidence": 0.1, "pinned": False},
        {"index": 3, "time": None, "confidence": 0.0, "pinned": False},
    ]
    al = {"results": results}
    states = stage_states(base_analysis(identification=ident, alignment=al),
                          0.5, DiskFacts())
    assert states["align"]["state"] == "attention"   # align carries warnings
    assert "2 to check" in states["align"]["detail"]
    assert states["resolve"]["state"] == "attention"

    good = [r for r in results if r["index"] in (0, 1)]
    states = stage_states(
        base_analysis(identification=ident,
                      alignment={"results": good}), 0.5, DiskFacts())
    assert states["resolve"]["state"] == "done"


def test_emit_done_via_chapters_file():
    ident = {"best_trial": {"score": 6.0}}
    al = {"results": [{"index": 0, "time": 0.0, "confidence": 1.0,
                       "pinned": False},
                      {"index": 1, "time": 3600.0, "confidence": 0.9,
                       "pinned": False}]}
    analysis = base_analysis(identification=ident, alignment=al,
                             emitted={"count": 2, "first": 0.0,
                                      "last": 3600.0})
    ok = DiskFacts(chapters_file_match=True)
    states = stage_states(analysis, 0.5, ok)
    assert states["emit"]["state"] == "done"
    states = stage_states(analysis, 0.5, ok, emit_stale=True)
    assert states["emit"]["state"] == "pending"

    # post-finalize: single file whose embedded chapters match emitted
    post = DiskFacts(audio_files=["Book.mp3"], embedded_chapters=2,
                     embedded_last_start=3600.0)
    states = stage_states(analysis, 0.5, post)
    assert states["emit"]["state"] == "done"
    assert states["finalize"]["state"] == "done"

    # stale: wrong count on disk
    wrong = DiskFacts(audio_files=["Book.mp3"], embedded_chapters=3,
                      embedded_last_start=3600.0)
    states = stage_states(analysis, 0.5, wrong)
    assert states["emit"]["state"] == "pending"


def test_finalize_requires_emit_done():
    # one chaptered file on disk, but no analysis at all
    full = DiskFacts(audio_files=["Book.mp3"], embedded_chapters=14)
    states = stage_states({}, 0.5, full)
    assert states["finalize"]["state"] == "blocked"
    assert states["measure"]["state"] == "pending"

    # Place is done; Finish waits on a dry run (not blocked)
    ident = {"best_trial": {"score": 6.0}, "chapters": [1, 2]}
    al = {"results": [{"index": 0, "time": 0.0, "confidence": 1.0,
                       "pinned": False},
                      {"index": 1, "time": 3600.0, "confidence": 0.9,
                       "pinned": False}]}
    analysis = base_analysis(identification=ident, alignment=al)
    states = stage_states(analysis, 0.5, full)
    assert states["emit"]["state"] == "pending"
    assert states["finalize"]["state"] == "pending"
    assert states["finalize"]["detail"] == "needs a dry run"

    # ...and once emit has run (artifact emitted + disk match), both done
    analysis["emitted"] = {"count": 2, "first": 0.0, "last": 3600.0}
    matched = DiskFacts(audio_files=["Book.mp3"], embedded_chapters=2,
                        embedded_last_start=3600.0)
    states = stage_states(analysis, 0.5, matched)
    assert states["emit"]["state"] == "done"
    assert states["finalize"]["state"] == "done"
