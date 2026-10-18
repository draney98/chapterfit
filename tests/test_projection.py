"""Pure WorkflowProjection: table rows, result copy, resolve ladder."""
from chaptertools.gui.projection import WorkflowProjection
from chaptertools.gui.stages import DiskFacts


def _ident(*starts):
    return {"best_trial": {"score": 6.0, "rate_spread": 0.01},
            "label": "Audnexus B00TEST",
            "duration_ratio": 1.0,
            "chapters": [{"title": f"Ch {i + 1}", "start": s}
                         for i, s in enumerate(starts)]}


def _row(i, title, published, time, conf, pinned=False):
    return {"index": i, "title": title, "published": published,
            "time": time, "confidence": conf, "pinned": pinned,
            "spread": 0.0}


def test_pre_align_rows_honor_pins():
    art = {
        "identification": _ident(0.0, 90.0, 180.0),
        "pins": {"1": 94.875},
    }
    p = WorkflowProjection(art, 0.5, DiskFacts())
    rows = p.rows
    assert [r.mark for r in rows] == ["1", "2", "3"]
    assert rows[1].placed == 94.875
    assert rows[1].pin == "pin"
    assert rows[1].tags == ()
    assert rows[0].tags == ("unplaced",)
    assert p.confident_count == 1
    assert p.conflicts_unknown is False


def test_aligned_rows_mark_withheld_and_conflicts():
    results = [
        _row(0, "One", 0.0, 0.0, 0.9),
        _row(1, "Two", 90.0, 95.0, 0.1),
        _row(2, "Three", 180.0, 185.0, 0.8, pinned=True),
    ]
    art = {
        "envelope": {"duration": 3600.0}, "calibration": {"x": 1},
        "fingerprint": {"x": 1},
        "identification": _ident(0.0, 90.0, 180.0),
        "alignment": {"results": results, "conflicts": [2], "rate": 1.0},
        "gaps": {"gaps": [{"mid": 95.0, "dur": 2.5}]},
    }
    p = WorkflowProjection(art, 0.5, DiskFacts())
    assert [r.mark for r in p.rows] == ["1", "?2", "3"]
    assert p.rows[1].tags == ("lowconf",)
    assert p.rows[1].gap == "2.50 s"
    assert p.rows[2].tags == ("conflict",)
    assert p.rows[2].pin == "! pin"
    assert p.confident_count == 2
    assert p.states["align"]["state"] == "attention"
    assert p.states["resolve"]["state"] == "attention"
    assert p.next == "resolve"


def test_old_artifact_without_conflicts_does_not_guess():
    results = [_row(0, "One", 0.0, 0.0, 0.9),
               _row(1, "Two", 90.0, 95.0, 0.9, pinned=True)]
    results[1]["spread"] = None
    art = {"identification": _ident(0.0, 90.0),
           "alignment": {"results": results, "rate": 1.0}}
    p = WorkflowProjection(art, 0.5, DiskFacts())
    assert p.rows[1].tags == ()
    assert p.conflicts_unknown is True


def test_measure_and_identify_copy():
    art = {
        "envelope": {"duration": 3600.0}, "calibration": {"x": 1},
        "fingerprint": {"x": 1},
        "gaps": {"gaps": [{"mid": 600.0, "dur": 2.0},
                          {"mid": 1200.0, "dur": 3.2}],
                 "populations": [{"k": 0, "n": 41, "centre": 3.2}]},
        "source": {"timeline_trustworthy": False,
                   "declared_duration": 3500.0,
                   "decoded_duration": 3600.0,
                   "header_error_sec": 100.0,
                   "header_error_ratio": 0.028},
        "identification": {
            **_ident(0.0, 90.0),
            "best_trial": {"score": 1.2, "rate_spread": 0.09},
            "duration_ratio": 0.71,
            "blocked": ["file is 71% of the published runtime."],
        },
    }
    p = WorkflowProjection(art, 0.5, DiskFacts(), audio="/tmp/Book.mp3")
    assert "2 pauses found" in p.measure_text
    assert "The longest 41 are about 3.2 s" in p.measure_text
    assert "-write_xing 1" in p.measure_text and "Book.fixed.mp3" in p.measure_text
    assert p.identify_button == "Check"
    assert "rate spread 9.0%" in p.identify_text
    assert "runtime ratio 0.710" in p.identify_text
    assert "rejected" in p.identify_text
    assert "71% of the published" in p.identify_text


def test_resolve_weak_order_and_recommendation():
    results = [
        _row(0, "One", 0.0, 0.0, 1.0),
        _row(1, "Two", 600.0, 600.0, 0.9),
        _row(2, "Three", 1200.0, 1210.0, 0.1),
    ]
    art = {"envelope": {"duration": 3600.0}, "calibration": {"x": 1},
           "gaps": {"gaps": []}, "fingerprint": {"x": 1},
           "identification": _ident(0.0, 600.0, 1200.0),
           "alignment": {"results": results, "rate": 1.0, "conflicts": []}}
    p = WorkflowProjection(art, 0.5, DiskFacts())
    assert [r["index"] for r in p.weak] == [2]
    assert p.recommendation == "Listen"
    assert p.next == "resolve"


def test_nfo_chapters_are_placed_ahead_of_the_artifact():
    nfo = [{"title": "Opening Credits", "start": 0.0, "end": 25.0},
           {"title": "One", "start": 25.0, "end": 2281.0}]
    p = WorkflowProjection({}, 0.5, DiskFacts(), nfo_chapters=nfo)
    assert [r.title for r in p.rows] == ["Opening Credits", "One"]
    assert p.rows[1].placed == 25.0
    assert p.rows[1].published is None
    assert p.rows[1].tags == ()

    identified = WorkflowProjection(
        {"identification": _ident(0.0, 90.0),
         "alignment": {"results": [
             _row(0, "Aligned", 0.0, 1.0, 0.9),
             _row(1, "Also", 90.0, 91.0, 0.9)],
             "rate": 1.0, "conflicts": []}},
        0.5, DiskFacts(), nfo_chapters=nfo)
    assert [r.title for r in identified.rows] == ["Opening Credits", "One"]
    assert identified.rows[1].placed == 25.0
    assert identified.rows[1].published == 90.0
    assert identified.rows[1].tags == ()

    no_nfo = WorkflowProjection(
        {"identification": _ident(0.0, 90.0),
         "alignment": {"results": [
             _row(0, "Aligned", 0.0, 1.0, 0.9),
             _row(1, "Also", 90.0, 91.0, 0.9)],
             "rate": 1.0, "conflicts": []}},
        0.5, DiskFacts())
    assert [r.title for r in no_nfo.rows] == ["Aligned", "Also"]
    assert no_nfo.rows[1].placed == 91.0


def test_one_track_nfo_does_not_replace_alignment():
    art = {"identification": _ident(0.0, 90.0),
           "alignment": {"results": [
               _row(0, "Aligned", 0.0, 1.0, 0.9),
               _row(1, "Also", 90.0, 91.0, 0.9)],
               "rate": 1.0, "conflicts": []}}
    one = [{"title": "The Whole Book", "start": 0.0, "end": 3600.0}]
    for nfo in (one, []):
        p = WorkflowProjection(art, 0.5, DiskFacts(), nfo_chapters=nfo)
        assert [r.title for r in p.rows] == ["Aligned", "Also"]
        assert p.rows[1].placed == 91.0


def test_transcript_text_from_heard_clips():
    from chaptertools.gui.projection import filter_transcript, transcript_line_time, transcript_text

    heard = {"heard": {"clips": {
        "12.5|3|base": '[{"s": 0.0, "t": "chapter two"}]'}}}
    assert "chapter two" in transcript_text(heard)
    assert transcript_text({}) == ""
    assert transcript_text({"heard": {}}) == ""

    body = transcript_text(heard) + "\n" + "     1:00:00.000  the road"
    assert "chapter two" in filter_transcript(body, "chapter")
    assert "the road" not in filter_transcript(body, "chapter")
    assert filter_transcript(body, "") == body
    assert filter_transcript(body, "chapt[").startswith("bad regex:")
    assert transcript_line_time(transcript_text(heard).splitlines()[0]) == 12.5
    assert transcript_line_time("no timestamp here") is None


def test_found_summary_names_nfo_chapters():
    from chaptertools.gui.harvest import found_summary

    base = {"tags": {}, "embedded_chapters": [], "nfo": {"title": "Book",
                                                         "tracks": 3},
            "has_chapters_json": False, "has_chapters_txt": False,
            "nfo_chapters": [{"title": "A"}, {"title": "B"}, {"title": "C"}]}
    assert "3 NFO chapters" in found_summary(base)
    one = {**base, "nfo_chapters": [], "nfo": {"title": "Dune", "tracks": 1}}
    assert "1 tracks" in found_summary(one)
    assert "NFO chapters" not in found_summary(one)
