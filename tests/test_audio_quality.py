"""DSP chain, analysis, presets, and chunked rendering. No GUI."""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from chaptertools.audio_quality import worker
from chaptertools.audio_quality.analysis import Cancelled, load_report, save_report, scan_wav
from chaptertools.audio_quality.presets import (
    all_presets,
    delete_user_preset,
    load_session,
    load_shipped,
    load_user,
    rename_user_preset,
    save_session,
    save_user_preset,
)
from chaptertools.audio_quality.signal import crossfade, gain_for_target
from chaptertools.audio_quality.stages import (
    DeclickStage,
    DenoiseStage,
    EnhanceStage,
    HighpassStage,
    default_chain,
)


def _sine(sr, seconds, freq=220.0, amp=0.2):
    t = np.arange(int(sr * seconds)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _write(path, audio, sr):
    sf.write(path, audio, sr)


def test_chain_order_and_bypass():
    chain = default_chain()
    assert [stage.name for stage in chain.stages] == [
        "highpass", "denoise", "enhance", "declick", "eq", "deesser",
        "compressor", "gate", "loudness", "limiter",
    ]
    sr = 16000
    audio = _sine(sr, 0.5)
    for stage in chain.stages:
        stage.bypass = True
    assert np.allclose(chain.process(audio, sr), audio)


def test_each_stage_default_is_finite_and_peak_safe():
    sr = 16000
    rng = np.random.default_rng(0)
    audio = (rng.standard_normal(sr).astype(np.float32) * 0.15)
    chain = default_chain()
    # The optional enhance model is not part of this check.
    chain.stage("enhance").bypass = True
    processed = chain.process(audio, sr)
    assert len(processed) == len(audio)
    assert np.all(np.isfinite(processed))
    assert float(np.max(np.abs(processed))) <= 1.0


def test_highpass_removes_dc():
    sr = 8000
    audio = np.full(sr, 0.4, dtype=np.float32)
    processed = HighpassStage().process(audio, sr)
    assert abs(float(np.mean(processed[sr // 2:]))) < 0.05


def test_declick_replaces_an_impulse():
    sr = 16000
    audio = _sine(sr, 0.25, amp=0.05)
    audio = audio.copy()
    audio[len(audio) // 2] = 0.95
    stage = DeclickStage()
    stage.params["sensitivity"] = 1.0
    stage.params["threshold"] = 0.2
    processed = stage.process(audio, sr)
    assert abs(float(processed[len(audio) // 2])) < 0.4


def test_crossfade_has_no_edge_spike():
    previous = np.zeros((80, 1), dtype=np.float32)
    following = np.ones((80, 1), dtype=np.float32)
    blended = crossfade(previous, following)
    hard = np.concatenate([previous, following])
    assert np.max(np.abs(np.diff(hard[:, 0]))) == pytest.approx(1.0)
    assert np.max(np.abs(np.diff(blended[:, 0]))) < 0.1
    same = crossfade(np.ones((40, 1)), np.ones((40, 1)))
    assert np.allclose(same, 1.0, atol=1e-5)


def test_chunked_identity_keeps_every_sample(tmp_path):
    sr = 8000
    audio = _sine(sr, 1.7, freq=50.0)
    path = tmp_path / "in.wav"
    _write(path, audio, sr)
    chain = default_chain()
    for stage in chain.stages:
        stage.bypass = True
    pieces = [
        emit[:, 0]
        for emit, _fraction in worker.render_chunked(
            str(path), chain, chunk_seconds=0.4, overlap_seconds=0.05)
    ]
    rendered = np.concatenate(pieces)
    assert len(rendered) == len(audio)
    assert np.max(np.abs(rendered - audio)) < 1e-4


def test_one_gain_keeps_the_relative_level(tmp_path):
    sr = 8000
    loud = _sine(sr, 1.0, amp=0.05)
    quiet = _sine(sr, 1.0, amp=0.005)
    audio = np.concatenate([loud, quiet])
    path = tmp_path / "levels.wav"
    _write(path, audio, sr)
    chain = default_chain()
    for stage in chain.stages:
        stage.bypass = True
    chain.stage("loudness").bypass = False
    chain.stage("limiter").bypass = False
    from chaptertools.audio_quality.signal import StreamingLoudness, lin_to_db, true_peak
    meter = StreamingLoudness(sr)
    meter.add(audio)
    gain = gain_for_target(
        meter.integrated(), lin_to_db(true_peak(audio, sr)), -19.0, -3.0)
    chain.stage("loudness").extra["fixed_gain"] = gain
    pieces = [
        emit[:, 0]
        for emit, _fraction in worker.render_chunked(
            str(path), chain, only={"loudness", "limiter"},
            chunk_seconds=0.5, overlap_seconds=0.05)
    ]
    rendered = np.concatenate(pieces)
    def rms(block):
        return float(np.sqrt(np.mean(np.square(block))))
    ratio_in = rms(audio[sr // 4:sr // 2]) / rms(audio[sr + sr // 4:sr + sr // 2])
    ratio_out = rms(rendered[sr // 4:sr // 2]) / rms(
        rendered[sr + sr // 4:sr + sr // 2])
    assert ratio_in > 5
    assert abs(ratio_out - ratio_in) / ratio_in < 0.25


def test_cancel_stops_before_encode(tmp_path, monkeypatch):
    sr = 8000
    path = tmp_path / "in.wav"
    _write(path, _sine(sr, 0.5), sr)
    called = []
    monkeypatch.setattr(worker, "_pass_two", lambda *args, **kwargs: called.append(True))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        worker.process_wav(
            str(path), str(path), str(tmp_path), default_chain().snapshot(),
            "64k", cancel, chunk_seconds=0.2, overlap_seconds=0.02)
    assert called == []
    assert not os.path.exists(worker.output_mp3_path(str(tmp_path), str(path)))


def test_presets_round_trip_and_gentle_cleanup(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAPTERFIT_CONFIG", str(tmp_path / "config"))
    shipped = load_shipped()
    assert "Gentle cleanup" in shipped
    assert "Podcast / broadcast" in shipped
    chain = default_chain()
    chain.apply(shipped["Gentle cleanup"])
    assert chain.stage("highpass").params["cutoff_hz"] == 60
    assert chain.stage("denoise").params["strength"] == 0.25
    assert chain.stage("declick").bypass is True
    blob = chain.snapshot()
    again = default_chain()
    again.apply(json.loads(json.dumps(blob)))
    assert again.snapshot() == blob
    save_user_preset("Mine", blob)
    assert "Mine" in all_presets()
    save_session(blob)
    assert load_session()["stages"]["highpass"]["params"]["cutoff_hz"] == 60


def test_stage_summary_for_the_chain_row():
    from chaptertools.gui.quality_tab.summary import stage_summary
    chain = default_chain()
    assert stage_summary(chain.stage("gate")) == "off"
    assert stage_summary(chain.stage("enhance")) == "not installed"
    chain.stage("highpass").params["cutoff_hz"] = 90
    assert stage_summary(chain.stage("highpass")) == "90 Hz"
    chain.stage("eq").params["presence_gain"] = 3
    assert stage_summary(chain.stage("eq")) == "1 band active"


def test_silence_is_left_out_of_the_noise_span(tmp_path):
    sr = 8000
    silence = np.zeros(sr, dtype=np.float32)
    tone = _sine(sr, 1.0, amp=0.05)
    audio = np.concatenate([silence, tone, tone])
    path = tmp_path / "span.wav"
    _write(path, audio, sr)
    report = scan_wav(str(path), chunk_seconds=1.0)
    assert report["noise_floor_span_db"] < 40
    assert report["chunks"][0]["noisy"] is False


def test_rename_and_delete_only_touch_saved_presets(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAPTERFIT_CONFIG", str(tmp_path / "config"))
    save_user_preset("Mine", default_chain().snapshot())
    assert rename_user_preset("Gentle cleanup", "Other") is False
    assert rename_user_preset("Mine", "Yours") is True
    assert "Yours" in load_user()
    assert "Mine" not in load_user()
    assert delete_user_preset("Yours") is True
    assert load_user() == {}


def test_scan_marks_clipping_and_noise(tmp_path):
    sr = 8000
    rng = np.random.default_rng(1)
    quiet = rng.normal(0, 0.001, sr).astype(np.float32)
    hiss = rng.normal(0, 0.08, sr).astype(np.float32)
    clipped = np.clip(_sine(sr, 1.0, amp=1.2), -1.0, 1.0)
    # Mostly quiet, so the hiss chunk sits well above the median floor.
    audio = np.concatenate([quiet, quiet, quiet, hiss, clipped])
    path = tmp_path / "scan.wav"
    _write(path, audio, sr)
    report = scan_wav(str(path), chunk_seconds=1.0)
    assert report["chunks"][4]["clipping"] is True
    assert report["chunks"][3]["noisy"] is True
    assert report["chunks"][0]["noisy"] is False
    assert report["integrated_lufs"] is not None
    assert report["true_peak_db"] is not None
    assert report["noise_floor_db"] is not None
    dest = tmp_path / "scan.quality.json"
    save_report(str(dest), report)
    assert load_report(str(dest))["chunks"][4]["clipping"] is True


def test_replace_library_keeps_the_first_original(tmp_path):
    library = tmp_path / "book.mp3"
    library.write_bytes(b"original-bytes")
    cleaned = tmp_path / "cleaned.mp3"
    cleaned.write_bytes(b"cleaned-bytes")
    work = tmp_path / "work"
    original = worker.replace_library(str(library), str(cleaned), str(work))
    assert library.read_bytes() == b"cleaned-bytes"
    assert Path(original).read_bytes() == b"original-bytes"
    cleaned.write_bytes(b"second-pass")
    worker.replace_library(str(library), str(cleaned), str(work))
    assert Path(original).read_bytes() == b"original-bytes"
    assert library.read_bytes() == b"second-pass"


def test_neural_stages_are_optional(monkeypatch):
    import chaptertools.audio_quality.neural as neural

    monkeypatch.setattr(neural, "deepfilter_available", lambda: False)
    monkeypatch.setattr(neural, "resemble_available", lambda: False)
    denoise = DenoiseStage()
    assert denoise.enabled_choices("mode") == ("spectral",)
    assert "install" in denoise.disabled_note("mode")
    ok, reason = EnhanceStage().availability()
    assert ok is False
    assert "install" in reason
    chain = default_chain()
    chain.stage("enhance").bypass = False
    with pytest.raises(Exception, match="resemble-enhance"):
        worker._reject_missing_neural(chain, __import__(
            "chaptertools.audio_quality.neural", fromlist=["neural"]))


def test_snapshot_skips_unknown_stage():
    chain = default_chain()
    chain.apply({"stages": {"not-a-stage": {"bypass": True},
                            "highpass": {"params": {"cutoff_hz": 40}}}})
    assert chain.stage("highpass").params["cutoff_hz"] == 40


def test_quality_tab_builds_one_row_per_stage(tmp_path, monkeypatch):
    tab, root = _quality_tab(tmp_path, monkeypatch)
    try:
        assert tab.chain is not None
        assert len(tab.chain_rows) == len(tab.chain.stages) == 10
        assert tab.detail is not None
        names = [stage.name for stage in tab.chain.stages]
        assert names[0] == "highpass"
        assert tab.chain_panel.rows[0].summary_var.get() != ""
    finally:
        root.destroy()


def test_chain_keys_move_and_toggle(tmp_path, monkeypatch):
    tab, root = _quality_tab(tmp_path, monkeypatch)
    try:
        tab.chain_panel.move(1)
        assert tab.detail.stage.name == "denoise"
        tab.chain_panel.move(-1)
        tab.chain_panel.toggle_selected()
        assert tab.chain.stage("highpass").bypass is True
    finally:
        root.destroy()


def test_enabled_checkbox_matches_bypass(tmp_path, monkeypatch):
    tab, root = _quality_tab(tmp_path, monkeypatch)
    try:
        tab.enabled_vars["highpass"].set(False)
        root.update_idletasks()
        assert tab.chain.stage("highpass").bypass is True
        tab.select_stage(0)
        assert tab.detail.enabled_var is tab.enabled_vars["highpass"]
        tab.enabled_vars["highpass"].set(True)
        assert tab.chain.stage("highpass").bypass is False
    finally:
        root.destroy()


def test_preset_apply_starts_from_defaults(tmp_path, monkeypatch):
    tab, root = _quality_tab(tmp_path, monkeypatch)
    try:
        tab.chain.stage("limiter").params["ceiling_db"] = -4
        tab.apply_named("Gentle cleanup")
        assert tab.chain.stage("highpass").params["cutoff_hz"] == 60
        assert tab.chain.stage("declick").bypass is True
        assert tab.chain.stage("limiter").params["ceiling_db"] == -1.0
        assert tab.chain_panel.preset_var.get() == "Gentle cleanup"
        assert tab.chain_panel.status_var.get() == ""
    finally:
        root.destroy()


def test_reset_restores_the_loaded_preset(tmp_path, monkeypatch):
    tab, root = _quality_tab(tmp_path, monkeypatch)
    try:
        tab.apply_named("Gentle cleanup")
        tab.chain.stage("highpass").params["cutoff_hz"] = 110
        tab._refresh()
        assert tab.chain_panel.status_var.get().startswith("Modified")
        assert tab.chain_panel.rows[0].summary_var.get() == "110 Hz"
        tab.reset_param("highpass", "cutoff_hz")
        assert tab.chain.stage("highpass").params["cutoff_hz"] == 60
        assert tab.chain_panel.status_var.get() == ""
    finally:
        root.destroy()


def test_true_peak_over_zero_uses_the_warning_tile(tmp_path, monkeypatch):
    tab, root = _quality_tab(tmp_path, monkeypatch)
    try:
        tab._apply_report({
            "integrated_lufs": -19.0,
            "true_peak_db": 2.1,
            "noise_floor_db": -51.5,
            "dynamic_range_db": 12.0,
            "noise_floor_span_db": 3.0,
            "duration": 1.0,
            "chunks": [{"lufs": -19.0, "clipping": True, "noisy": False}],
        })
        root.update_idletasks()
        tile = tab.analysis.tiles["true_peak_db"]
        assert tile.warning is True
        assert "clips" in tile.sub_var.get()
    finally:
        root.destroy()


def _quality_tab(tmp_path, monkeypatch):
    tkinter = pytest.importorskip("tkinter")
    try:
        root = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display available")
    monkeypatch.setenv("CHAPTERFIT_CONFIG", str(tmp_path / "config"))
    from chaptertools.gui.quality_tab import QualityTab
    tab = QualityTab(root, app=None, workflow=None)
    root.update_idletasks()
    return tab, root


@pytest.mark.integration
def test_decode_and_encode_a_short_file(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")
    sr = 8000
    wav = tmp_path / "src.wav"
    _write(wav, _sine(sr, 0.6, amp=0.1), sr)
    mp3 = tmp_path / "src.mp3"
    code = os.system(
        f"ffmpeg -y -v error -i {wav} -c:a libmp3lame -b:a 64k {mp3}")
    if code != 0 or not mp3.is_file():
        pytest.skip("ffmpeg has no mp3 encoder")
    cancel = threading.Event()
    decoded = worker.ensure_decoded(str(mp3), str(tmp_path / "work"), cancel)
    info = sf.info(decoded)
    assert info.samplerate == sr
    assert info.frames > sr // 2
    chain = default_chain()
    for stage in chain.stages:
        stage.bypass = True
    chain.stage("loudness").bypass = False
    chain.stage("limiter").bypass = False
    result = worker.process_wav(
        decoded, str(mp3), str(tmp_path / "work"), chain.snapshot(), "64k",
        cancel, chunk_seconds=0.3, overlap_seconds=0.02)
    assert os.path.isfile(result["mp3"])
    assert "gain_db" in result
