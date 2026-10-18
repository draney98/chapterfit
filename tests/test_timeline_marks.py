"""Pure helpers for timeline mark labels and silence ticks."""
from chaptertools.gui.timeline import (
    PLOT_INSET, label_indexes, plot_box, silence_pixel_xs, time_to_x)


def test_label_indexes_skip_when_closer_than_18px():
    xs = [10.0, 20.0, 40.0, 55.0, 80.0]
    assert label_indexes(xs, min_gap=18) == [0, 2, 4]


def test_label_indexes_keeps_first_of_cluster():
    xs = [100.0, 105.0, 110.0, 130.0]
    assert label_indexes(xs, min_gap=18) == [0, 3]


def test_silence_pixel_xs_dedupes_same_column():
    gaps = [
        {"mid": 0.0}, {"mid": 0.1}, {"mid": 10.0}, {"mid": 10.05},
        {"mid": 50.0},
    ]
    # span 0..100 mapped onto width 100 → mid becomes x
    xs = silence_pixel_xs(gaps, start=0.0, end=100.0, width=100)
    assert xs == [0, 10, 50]


def test_plot_box_uses_pane_padding():
    inset = PLOT_INSET
    x0, y0, plot_w, plot_h = plot_box(400, 72)
    assert (x0, y0) == (inset, inset)
    assert x0 + plot_w == 400 - inset
    assert y0 + plot_h == 72 - inset


def test_time_to_x_keeps_ends_off_the_border():
    inset = PLOT_INSET
    assert time_to_x(0, 0, 100, width=400, height=72) == inset
    assert time_to_x(100, 0, 100, width=400, height=72) == 400 - inset
    assert time_to_x(50, 0, 100, width=400, height=72) == 200
