"""Quality tab coordinator. DSP and preset files stay where they are."""

from __future__ import annotations

import os
import queue
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from chaptertools.audio_quality import quality_status
from chaptertools.gui import palette
from chaptertools.gui.theme import equalize_segments, require_customtkinter
from chaptertools.util import hms

from .analysis_view import AnalysisFrame
from .chain_view import ChainPanel
from .detail_view import DetailPanel
from .footer_view import FooterBar
from .styles import install

ctk = require_customtkinter()

PREVIEW_SECONDS = 30.0
_MODIFIED = "Modified \u2014 Save as\u2026 to keep these settings"


class QualityTab(ctk.CTkFrame):
    """Notebook page. ``chain_rows`` is one frame per stage, in chain order."""

    def __init__(self, master, app, workflow):
        install(master)
        super().__init__(master, fg_color=palette.Q_GROUND, corner_radius=0)
        self.app = app
        self.workflow = workflow
        self.audio = None
        self.work_dir = None
        self.chain = None
        self.chain_rows = []
        self.detail = None
        self._ready = False
        self._loading = False
        self._save_after = None
        self._preview = None
        self._player = _ABPlayer()
        self.report = None
        self.selection = None
        self._baseline = None
        self._preset_name = ""
        self._processed_snapshot = None
        self._processed_at = None
        self._inflight_snapshot = None
        ok, reason = quality_status()
        self.available = ok
        if not ok:
            ctk.CTkLabel(self, text=reason, wraplength=640, justify="left", anchor="w").pack(
                anchor="w", padx=8, pady=8)
            self.jobs = _IdleJobs()
            return
        from chaptertools.audio_quality.presets import load_session
        from chaptertools.audio_quality.stages import default_chain
        from chaptertools.audio_quality.worker import QualityJobs

        self.jobs = QualityJobs()
        self.chain = default_chain()
        saved = load_session()
        if saved:
            self.chain.apply(saved)
        _lock_missing(self.chain)
        self._baseline = self.chain.snapshot()
        self._preset_name = _matching_preset(self.chain)
        self.hear_var = tk.BooleanVar(value=False)
        self.enabled_vars = {
            stage.name: tk.BooleanVar(value=not stage.bypass)
            for stage in self.chain.stages
        }
        for stage in self.chain.stages:
            var = self.enabled_vars[stage.name]
            var.trace_add("write", lambda *_a, s=stage, v=var: self._on_enabled(s, v))
        self._build()
        self._ready = True

    @property
    def busy(self) -> bool:
        return bool(self.jobs.busy)

    def set_book(self, audio, work_dir) -> None:
        self.audio = audio
        self.work_dir = work_dir
        self._player.stop()
        self._preview = None
        self.selection = None
        self._note_preview()
        if not self.available:
            return
        self._show_source()
        self._load_saved_report()

    def poll(self) -> None:
        if not self.available:
            return
        while True:
            try:
                kind, name, payload = self.jobs.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                fraction, eta, message = payload
                self._show_progress(fraction, eta, message)
            elif kind == "done":
                self._finish(name, payload)
            elif kind == "cancelled":
                self._show_progress(0, None, "cancelled")
                self._log("quality cancelled")
                if self.workflow is not None:
                    self.workflow._after_cleanup = None
            elif kind == "error":
                self._show_progress(0, None, "failed")
                self._log(f"quality: {payload}")
                if self.workflow is not None:
                    self.workflow._after_cleanup = None
                messagebox.showerror("Audio quality", str(payload),
                                     parent=self.winfo_toplevel())
            elif kind == "refused":
                self._log(f"quality: {payload}")
        self._set_running(self.busy)
        self._refresh_status()

    def cancel(self) -> None:
        self.jobs.cancel()
        from chaptertools.audio_quality import neural
        neural.abort_active()
        self._player.stop()

    def select_stage(self, index: int) -> None:
        self.chain_panel.select(index)
        self._show_stage(index)

    def apply_named(self, name: str) -> None:
        """Factory defaults, then this preset, so the name matches the chain."""
        from chaptertools.audio_quality.presets import all_presets
        blob = all_presets().get(name)
        if not blob or self.chain is None:
            return
        from chaptertools.audio_quality.stages import default_chain
        fresh = default_chain()
        fresh.apply(blob)
        _copy_chain(fresh, self.chain)
        _lock_missing(self.chain)
        self._preset_name = name
        self._baseline = self.chain.snapshot()
        self._push_enabled()
        self._fill_presets()
        self._refresh()
        self._save_session()

    def reset_stage(self, stage) -> None:
        base = (self._baseline or {}).get("stages", {}).get(stage.name)
        if not base:
            return
        stage.load(base)
        _lock_missing(self.chain)
        self._loading = True
        self.enabled_vars[stage.name].set(not stage.bypass)
        self._loading = False
        self._refresh()
        self._save_session()

    def reset_param(self, name: str, key: str) -> None:
        stage = self.chain.stage(name)
        base = (self._baseline or {}).get("stages", {}).get(name, {})
        params = base.get("params", {})
        if key not in params:
            return
        stage.params[key] = params[key]
        self._refresh()
        self._save_session()

    # -------------------------------------------------------------- layout --
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.analysis = AnalysisFrame(self, self.cmd_rescan, self._turn_on_denoise,
                                      self._on_range)
        self.analysis.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 2))
        body = ttk.Panedwindow(self, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew", padx=4)
        self.chain_panel = ChainPanel(
            body, self.chain, self.enabled_vars, self._show_stage,
            self._on_preset_picked, self.cmd_save_preset, self.cmd_restore,
            self.cmd_rename_preset, self.cmd_delete_preset,
        )
        self.detail = DetailPanel(
            body, self.enabled_vars, self._changed, self.reset_stage, self._learn_noise,
            on_choice=self._stop_playback,
        )
        body.add(self.chain_panel, weight=0)
        body.add(self.detail, weight=1)
        self.chain_rows = [row.frame for row in self.chain_panel.rows]
        self.footer = FooterBar(
            self, self.hear_var, self.cmd_play, self._player.stop, self._toggle_hear,
            self.cmd_process, self.cmd_replace, self.cancel,
        )
        self.footer.grid(row=2, column=0, sticky="ew")
        self._include_at_finish = None
        if self.workflow is not None and hasattr(self.workflow, "bitrate_var"):
            self.footer.bitrate_var = self.workflow.bitrate_var
            self._include_at_finish = ctk.CTkSwitch(
                self.chain_panel, text="Include at Finish",
                variable=self.workflow.include_cleanup,
                command=self.workflow._sync_cleanup_choice,
            )
            self._include_at_finish.grid(row=4, column=0, sticky="w", pady=(2, 0))
        self._multifile = False
        self._multifile_folder = ""
        self._multifile_files: list[str] = []
        self._multifile_reports: dict[str, dict] = {}
        self._across_files = tk.StringVar(value="each")  # each | whole
        self._mf_dry_ok = False
        self._mf_dry_hash: tuple | None = None
        self._build_multifile_bar()
        self._fill_presets()
        self._show_stage(0)
        self._refresh_status()

    def _build_multifile_bar(self) -> None:
        """Write bar for multi-file books (Part 4). Hidden unless multifile."""
        bar = ctk.CTkFrame(self, fg_color=palette.Q_GROUND, corner_radius=0)
        self._mf_bar = bar
        bar.columnconfigure(1, weight=1)
        self._mf_back = ctk.CTkButton(
            bar, text="← Book", command=self._back_to_multifile, height=28, width=80)
        self._mf_back.grid(row=0, column=0, sticky="w", padx=4, pady=4)
        self._mf_status = tk.StringVar(value="")
        ctk.CTkLabel(bar, textvariable=self._mf_status, anchor="w", text="").grid(
            row=0, column=1, sticky="w", padx=8)
        ctk.CTkLabel(bar, text="MP3").grid(row=0, column=2, sticky="e")
        bitrates = ("48k", "64k", "96k", "128k", "160k", "192k", "256k", "320k")
        self._mf_bitrate = tk.StringVar(value="128k")
        ttk.Combobox(bar, textvariable=self._mf_bitrate, values=bitrates,
                     width=6, state="readonly").grid(row=0, column=3, padx=4)
        ctk.CTkButton(bar, text="Dry run", command=self.cmd_multifile_dry,
                      height=28, width=80).grid(row=0, column=4, padx=4)
        self._mf_clean_btn = ctk.CTkButton(
            bar, text="Clean all files", command=self.cmd_multifile_clean,
            state="disabled", height=28)
        self._mf_clean_btn.grid(row=0, column=5, sticky="e", padx=4)
        # Across-files loudness choice
        across = ctk.CTkFrame(bar, fg_color=palette.Q_GROUND, corner_radius=0)
        across.grid(row=1, column=0, columnspan=6, sticky="w", pady=(4, 0), padx=4)
        ctk.CTkLabel(across, text="Across files").pack(side="left")
        self._across_seg = ctk.CTkSegmentedButton(
            across,
            values=["Level each file to the target", "One gain for the whole book"],
            command=self._on_across_seg, height=28, border_width=None,
            dynamic_resizing=False, width=480)
        self._across_seg.set("Level each file to the target")
        self._across_seg.pack(side="left", padx=(8, 0))
        equalize_segments(self._across_seg)
        self._mf_strip = tk.Canvas(bar, height=28, bg=palette.RAIL_IDLE,
                                   highlightthickness=0)
        self._mf_strip.grid(row=2, column=0, columnspan=6, sticky="ew",
                            pady=(4, 0))
        self._mf_file_states: dict[str, str] = {}

    def _show_stage(self, index: int) -> None:
        stage = self.chain.stages[index]
        base = (self._baseline or {}).get("stages", {}).get(stage.name, {})
        self.detail.show(
            stage, base, selection=self.selection,
            recommend=self._noise_varies(),
        )

    def _on_range(self, start: float, end: float | None) -> None:
        self.selection = (start, end) if end is not None else None
        if self.detail.stage is not None and self.detail.stage.name == "denoise":
            self._show_stage(self.chain_panel._selected)

    def _begin_job(self, name: str) -> None:
        app = getattr(self, "app", None)
        if app is None:
            return
        from chaptertools.gui.busy import job_is_modal, opening_sentence
        app.session.set_job(True, name, opening_sentence(name), job_is_modal(name))

    def _end_job(self) -> None:
        app = getattr(self, "app", None)
        if app is not None:
            app.session.set_job(False)

    def _note_preview(self) -> None:
        bar = getattr(getattr(self, "app", None), "player_bar", None)
        if bar is not None:
            bar._show_hear()

    def _remember_render(self) -> None:
        started = getattr(self, "_render_started", None)
        app = getattr(self, "app", None)
        duration = float(getattr(getattr(app, "session", None), "duration", 0) or 0)
        if started is None or duration <= 0:
            return
        elapsed = time.monotonic() - started
        if elapsed > 0:
            self._render_factor = elapsed / duration

    def _turn_on_denoise(self) -> None:
        stage = self.chain.stage("denoise")
        stage.params["per_segment"] = True
        index = next(i for i, item in enumerate(self.chain.stages) if item.name == "denoise")
        self.select_stage(index)
        self._changed()

    # --------------------------------------------------------------- jobs --
    def _guard(self) -> bool:
        runner = getattr(self.app, "runner", None)
        if (runner is not None and runner.busy) or self.busy:
            messagebox.showinfo(
                "Busy", "Wait for the current job to finish.",
                parent=self.winfo_toplevel())
            return False
        if not self.audio or not self.work_dir:
            messagebox.showinfo(
                "Audio quality", "Select a book first.",
                parent=self.winfo_toplevel())
            return False
        return True

    def cmd_rescan(self) -> None:
        workflow = getattr(self, "workflow", None)
        if workflow is not None:
            workflow.cmd_rescan()
            return
        self.cmd_scan()

    def cmd_scan(self) -> None:
        if not self._guard():
            return
        audio, work = self.audio, self.work_dir
        self._set_running(True)
        self._begin_job("scan")
        if not self.jobs.start(
            "scan", lambda cancel, progress: self._scan(audio, work, cancel, progress),
        ):
            self._end_job()

    def cmd_play(self, start: float = 0.0) -> None:
        if not self._guard():
            return
        start = max(0.0, float(start))
        audio, work = self.audio, self.work_dir
        snapshot = self.chain.snapshot()
        self._set_running(True)
        self._begin_job("preview")
        if not self.jobs.start(
            "preview",
            lambda cancel, progress: self._preview_job(
                audio, work, snapshot, start, cancel, progress),
        ):
            self._end_job()

    def cmd_process(self) -> bool:
        if not self._guard():
            return False
        audio, work = self.audio, self.work_dir
        snapshot = self.chain.snapshot()
        self._inflight_snapshot = snapshot
        bitrate = self.footer.bitrate_var.get()
        self._set_running(True)
        self._render_started = time.monotonic()
        self._begin_job("process")
        if not self.jobs.start(
            "process",
            lambda cancel, progress: self._process_job(
                audio, work, snapshot, bitrate, cancel, progress),
        ):
            self._end_job()
            return False
        return True

    def cmd_replace(self, confirmed: bool = False) -> None:
        if not self._guard():
            return
        from chaptertools.audio_quality.worker import output_mp3_path
        cleaned = output_mp3_path(self.work_dir, self.audio)
        if not os.path.isfile(cleaned):
            return
        if not confirmed:
            ok = messagebox.askyesno(
                "Replace library file",
                "Copy the cleaned MP3 over the library file?\n\n"
                "The current library file is kept in the work folder "
                "as the original, the first time you do this.",
                parent=self.winfo_toplevel())
            if not ok:
                return
        audio, work = self.audio, self.work_dir
        self._begin_job("replace")
        if not self.jobs.start(
            "replace",
            lambda cancel, progress: self._replace_job(audio, cleaned, work),
        ):
            self._end_job()

    def _scan(self, audio, work, cancel, progress):
        from chaptertools.audio_quality.analysis import (
            quality_json_path,
            save_report,
            scan_wav,
        )
        from chaptertools.audio_quality.worker import ensure_decoded

        wav = ensure_decoded(audio, work, cancel, progress)
        report = scan_wav(wav, cancel=cancel, progress=progress)
        if os.path.isfile(audio):
            report["source_size"] = os.path.getsize(audio)
        save_report(quality_json_path(work, audio), report)
        return report

    def _preview_job(self, audio, work, snapshot, start, cancel, progress):
        from chaptertools.audio_quality.stages import default_chain
        from chaptertools.audio_quality.worker import ensure_decoded, read_segment

        progress(0.1, None, "decoding")
        wav = ensure_decoded(audio, work, cancel, progress)
        dry, sr = read_segment(wav, start, PREVIEW_SECONDS)
        chain = default_chain()
        chain.apply(snapshot)
        chain.stage("loudness").extra.pop("fixed_gain", None)
        progress(0.4, None, "preview")
        from chaptertools.audio_quality.analysis import LogPaused
        while True:
            try:
                wet = chain.process(dry, sr)
                break
            except LogPaused:
                continue
        progress(1.0, 0.0, "preview ready")
        return {"dry": dry, "wet": wet, "sr": sr}

    def _process_job(self, audio, work, snapshot, bitrate, cancel, progress):
        from chaptertools.audio_quality.worker import process_file
        return process_file(audio, work, snapshot, bitrate, cancel, progress)

    def _replace_job(self, audio, cleaned, work):
        from chaptertools.audio_quality.worker import replace_library
        return replace_library(audio, cleaned, work)

    def _finish(self, name: str, payload) -> None:
        self._show_progress(1, 0, "done" if name != "preview" else "playing")
        if name == "scan":
            self._apply_report(payload)
            self._log("quality scan finished")
        elif name == "mf-scan":
            self._apply_mf_reports((payload or {}).get("reports") or {})
            self._log(f"analysed {len(self._multifile_reports)} files")
        elif name == "mf-clean":
            written = (payload or {}).get("written") or ""
            for path in (payload or {}).get("cleaned") or []:
                self._mf_file_states[path] = "done"
            self._draw_mf_strip()
            self._mf_status.set(f"Gentle cleanup · written {written}")
            self.app.session.cleanup["written"] = written
            self.app.session.cleanup["preset"] = self._preset_name or "Gentle cleanup"
            self.app.session.emit("cleanup")
            self._log(f"cleaned {len((payload or {}).get('cleaned') or [])} files")
            if self.workflow is not None and hasattr(self.workflow, "multifile"):
                self.workflow.multifile.load(self._multifile_folder)
        elif name == "preview":
            self._preview = payload
            self._note_preview()
            try:
                self._player.play(payload["dry"], payload["wet"], payload["sr"])
                self._player.bypass = bool(self.hear_var.get())
                source = "original" if self.hear_var.get() else "edited"
                self._log(f"Playing {source} audio")
                self._sync_transport()
            except Exception as exc:
                messagebox.showerror("Preview", str(exc), parent=self.winfo_toplevel())
        elif name == "process":
            self._remember_render()
            self._processed_snapshot = self._inflight_snapshot
            self._processed_at = time.time()
            gain = payload.get("gain_db")
            level = payload.get("integrated_lufs")
            if isinstance(gain, float) and isinstance(level, float):
                target = float(self._loudness_target())
                shown = f"{target:.0f}" if target == round(target) else f"{target:.1f}"
                self._log(
                    f"quality wrote {payload.get('mp3')} "
                    f"(gain {gain:+.1f} dB, {level:.1f} LUFS toward {shown})"
                )
            else:
                self._log("quality wrote the MP3")
            follow = getattr(self.workflow, "_after_cleanup", None)
            if self.workflow is not None:
                self.workflow._after_cleanup = None
            if follow:
                follow()
        elif name == "replace":
            self._log(f"library file replaced; original kept at {payload}")
        elif name == "learn":
            self.chain.stage("denoise").extra["noise_profile"] = payload
            self._save_session()
            self._log(f"noise profile saved ({payload})")
        self._refresh_status()

    def _learn_noise(self) -> None:
        if self.selection is None or not self._guard():
            return
        start, end = self.selection
        audio, work = self.audio, self.work_dir
        self._begin_job("learn")
        if not self.jobs.start(
            "learn",
            lambda cancel, progress: self._learn_job(
                audio, work, start, max(0.05, end - start), cancel, progress),
        ):
            self._end_job()

    def _learn_job(self, audio, work, start, duration, cancel, progress):
        import numpy as np

        from chaptertools.audio_quality.worker import (
            ensure_decoded,
            noise_profile_path,
            read_segment,
        )

        wav = ensure_decoded(audio, work, cancel, progress)
        segment, _sr = read_segment(wav, start, duration)
        dest = noise_profile_path(work, audio)
        array = np.asarray(segment, dtype=np.float32)
        if array.ndim == 1:
            array = array[:, None]
        np.save(dest, array)
        return dest

    # ------------------------------------------------------------ controls --
    def _on_enabled(self, stage, var) -> None:
        if self._loading:
            return
        ok, _reason = stage.availability()
        if var.get() and not ok:
            self._loading = True
            var.set(False)
            self._loading = False
            return
        stage.bypass = not bool(var.get())
        if self.detail is not None and self.detail.stage is stage:
            self.detail.set_dim(stage.bypass and ok)
        self._changed()

    def _on_preset_picked(self, name: str) -> None:
        if not name or name == self._preset_name:
            return
        if self._is_modified() and os.environ.get("CHAPTERFIT_CONFIRM_PRESET") == "1":
            keep = messagebox.askyesno(
                "Apply preset",
                "Discard the changes that aren't saved as a preset?",
                parent=self.winfo_toplevel())
            if not keep:
                self.chain_panel.preset_var.set(self._preset_name)
                return
        self._stop_playback()
        self.apply_named(name)

    def _stop_playback(self) -> None:
        self._player.stop()
        app = getattr(self, "app", None)
        player = getattr(app, "player", None)
        if player is not None and getattr(player, "alive", False):
            player.stop()
        self._sync_transport()

    def _sync_transport(self) -> None:
        bar = getattr(getattr(self, "app", None), "player_bar", None)
        sync = getattr(bar, "_sync_button", None)
        if sync is not None:
            sync()

    def cmd_save_preset(self) -> None:
        from chaptertools.audio_quality.presets import save_user_preset
        name = simpledialog.askstring(
            "Save preset", "Name for this preset:", parent=self.winfo_toplevel())
        if not name or not name.strip():
            return
        name = name.strip()
        save_user_preset(name, self.chain.snapshot())
        self._preset_name = name
        self._baseline = self.chain.snapshot()
        self._fill_presets()
        self._refresh()

    def cmd_restore(self) -> None:
        from chaptertools.audio_quality.stages import default_chain
        fresh = default_chain()
        _copy_chain(fresh, self.chain)
        _lock_missing(self.chain)
        self._preset_name = ""
        self._baseline = self.chain.snapshot()
        self._push_enabled()
        self._fill_presets()
        self._refresh()
        self._save_session()

    def cmd_rename_preset(self) -> None:
        from chaptertools.audio_quality.presets import load_user, rename_user_preset
        old = self._preset_name
        if old not in load_user():
            return
        name = simpledialog.askstring(
            "Rename preset", "New name:", initialvalue=old, parent=self.winfo_toplevel())
        if not name or not name.strip() or name.strip() == old:
            return
        if not rename_user_preset(old, name.strip()):
            messagebox.showerror("Rename preset", "That name is already used.",
                                 parent=self.winfo_toplevel())
            return
        self._preset_name = name.strip()
        self._fill_presets()

    def cmd_delete_preset(self) -> None:
        from chaptertools.audio_quality.presets import delete_user_preset, load_user
        name = self._preset_name
        if name not in load_user():
            return
        ok = messagebox.askyesno(
            "Delete preset", f"Delete the saved preset \u201c{name}\u201d?",
            parent=self.winfo_toplevel())
        if not ok:
            return
        delete_user_preset(name)
        self._preset_name = ""
        self._fill_presets()
        self._refresh()

    def _changed(self) -> None:
        if not self._ready or self._loading:
            return
        self._refresh()
        if self._save_after is not None:
            self.after_cancel(self._save_after)
        self._save_after = self.after(400, self._save_session)

    def _refresh(self) -> None:
        if self.chain_panel is None:
            return
        self.chain_panel.refresh()
        for stage in self.chain.stages:
            self.chain_panel.mark(stage.name, self._stage_modified(stage.name))
        self.chain_panel.set_status(_MODIFIED if self._is_modified() else "")
        self._refresh_advice()
        self.analysis.set_target(self._loudness_target())
        self._refresh_status()
        self._publish_cleanup()

    def _publish_cleanup(self) -> None:
        app = self.winfo_toplevel()
        session = getattr(app, "session", None)
        if session is None or self.chain is None:
            return
        on = sum(1 for stage in self.chain.stages if not stage.bypass)
        session.cleanup["stages_on"] = on
        session.cleanup["preset"] = self._preset_name
        if self.workflow is not None and hasattr(self.workflow, "include_cleanup"):
            session.cleanup["include"] = bool(self.workflow.include_cleanup.get())
        session.emit("cleanup")

    def _save_session(self) -> None:
        self._save_after = None
        if self.chain is None:
            return
        from chaptertools.audio_quality.presets import save_session
        save_session(self.chain.snapshot())

    def _push_enabled(self) -> None:
        self._loading = True
        for stage in self.chain.stages:
            self.enabled_vars[stage.name].set(not stage.bypass)
        self._loading = False
        if self.chain_panel is not None:
            self._show_stage(self.chain_panel._selected)

    def _fill_presets(self) -> None:
        from chaptertools.audio_quality.presets import all_presets
        self.chain_panel.set_preset_names(list(all_presets()), self._preset_name)

    def _toggle_hear(self) -> None:
        self._player.bypass = bool(self.hear_var.get())

    def _show_source(self) -> None:
        from chaptertools.audio_quality.worker import nearest_bitrate, source_facts
        facts = source_facts(self.audio) if self.audio else {}
        name = os.path.basename(self.audio) if self.audio else ""
        duration = facts.get("duration")
        rate = facts.get("sample_rate")
        channels = facts.get("channels")
        blank = "\u2014"
        clock = hms(duration) if duration else blank
        rate_text = rate or blank
        channel_text = channels or blank
        self.analysis.set_source(
            f"{name}    {clock}    {rate_text} Hz    {channel_text} ch")
        if facts.get("bit_rate"):
            self.footer.bitrate_var.set(nearest_bitrate(facts["bit_rate"]))
        self._refresh_status()

    def _load_saved_report(self) -> None:
        from chaptertools.audio_quality.analysis import load_report, quality_json_path
        if not self.audio or not self.work_dir:
            self._apply_report(None)
            return
        self._apply_report(load_report(quality_json_path(self.work_dir, self.audio)))

    def _apply_report(self, report) -> None:
        self.report = report or {}
        self.selection = None
        self.analysis.selection = None
        self.analysis.set_report(self.report, target_lufs=self._loudness_target())
        self._refresh_advice()

    def _noise_varies(self) -> bool:
        span = (self.report or {}).get("noise_floor_span_db")
        return isinstance(span, (int, float)) and span > 6

    def _refresh_advice(self) -> None:
        per_segment = bool(self.chain.stage("denoise").params.get("per_segment"))
        self.analysis.show_advice(self._noise_varies() and not per_segment)

    def _loudness_target(self):
        if self.chain is None:
            return None
        return float(self.chain.stage("loudness").params["target_lufs"])

    def _refresh_status(self) -> None:
        if not hasattr(self, "footer"):
            return
        self.footer.set_status(self._status_text())
        self.footer.set_replace(self._replace_ready())

    def _status_text(self) -> str:
        if self.busy:
            return self.footer.status_var.get()
        if self._processed_snapshot is None:
            if self._cleaned_exists():
                return "Cleaned file is ready"
            return ""
        if _same_settings(self.chain.snapshot(), self._processed_snapshot):
            elapsed = max(0, int(time.time() - (self._processed_at or time.time())))
            return f"Processed {_ago(elapsed)} ago \u00b7 matches current settings"
        return "Settings changed since last process"

    def _replace_ready(self) -> bool:
        if not self._cleaned_exists():
            return False
        if self._processed_snapshot is None:
            return True
        return _same_settings(self.chain.snapshot(), self._processed_snapshot)

    def _cleaned_exists(self) -> bool:
        from chaptertools.audio_quality.worker import output_mp3_path
        return bool(
            self.audio and self.work_dir
            and os.path.isfile(output_mp3_path(self.work_dir, self.audio))
        )

    def _show_progress(self, fraction, eta, message: str) -> None:
        extra = ""
        if isinstance(eta, (int, float)) and eta > 1:
            extra = f" \u00b7 about {_eta(eta)} left"
        percent = int(round(max(0.0, min(1.0, float(fraction or 0))) * 100))
        if message in {"scanning", "decoding", "preview", "processing"} or (
            isinstance(fraction, (int, float)) and 0 < fraction < 1
        ):
            sentence = f"{message.capitalize()}\u2026 {percent}%{extra}"
        else:
            sentence = f"{message}{extra}"
        app = getattr(self, "app", None)
        if app is not None:
            app.session.set_job_detail(sentence)
            if (
                message not in {"done", "playing", "cancelled", "failed"}
                and sentence != getattr(self, "_logged_progress", None)
            ):
                self._logged_progress = sentence
                log = getattr(app, "log", None)
                if log is not None:
                    log(sentence)
        if hasattr(self, "footer"):
            self.footer.set_progress(fraction)
            self.footer.set_status(sentence)

    def _set_running(self, running: bool) -> None:
        if hasattr(self, "footer"):
            self.footer.set_running(running)

    def _is_modified(self) -> bool:
        if self._baseline is None or self.chain is None:
            return False
        return not _same_settings(self.chain.snapshot(), self._baseline)

    def _stage_modified(self, name: str) -> bool:
        if self._baseline is None:
            return False
        current = _stage_view(self.chain.snapshot(), name)
        base = _stage_view(self._baseline, name)
        return current != base

    def _log(self, text: str) -> None:
        log = getattr(self.app, "log", None)
        if log is not None:
            log(text)

    # ------------------------------------------------------ multi-file ------

    def set_multifile(self, folder: str, work_dir: str) -> None:
        """Prepare cleanup for a LibriVox multi-file book folder."""
        from chaptertools.gui.multifile_book import list_audio, load_librivox

        if not self.available:
            return
        self._multifile = True
        self._multifile_folder = folder
        self.work_dir = work_dir
        self._multifile_files = list_audio(folder)
        self.audio = self._multifile_files[0] if self._multifile_files else None
        self._multifile_reports = {}
        self._mf_file_states = {p: "waiting" for p in self._multifile_files}
        self._mf_dry_ok = False
        meta = load_librivox(folder) or {}
        set_name = str(meta.get("set") or "")
        bitrate = "128k"
        if "64" in set_name:
            bitrate = "64k"
        elif "128" in set_name or "VBR" in set_name.upper():
            bitrate = "128k"
        self._mf_bitrate.set(bitrate)
        if self.audio:
            self._show_source()
            self._load_saved_report()

    def show_multifile_bar(self, show: bool) -> None:
        if not self.available or not hasattr(self, "_mf_bar"):
            return
        if show:
            self.footer.grid_remove()
            if self._include_at_finish is not None:
                self._include_at_finish.grid_remove()
            self._mf_bar.grid(row=2, column=0, sticky="ew")
            self._mf_status.set(
                f"Write cleaned audio · {len(self._multifile_files)} files")
            self._mf_clean_btn.configure(
                text=f"Clean all {len(self._multifile_files)} files",
                state="normal" if self._mf_dry_ok else "disabled")
            self.detail.multifile = True
            self._draw_mf_strip()
            # Kick analysis of all files if empty
            if self._multifile_files and not self._multifile_reports:
                self.cmd_multifile_analyse()
        else:
            self._mf_bar.grid_remove()
            self.footer.grid()
            if self._include_at_finish is not None:
                self._include_at_finish.grid()
            self.detail.multifile = False

    def _back_to_multifile(self) -> None:
        self.show_multifile_bar(False)
        if self.workflow is not None:
            self.workflow.return_from_multifile_cleanup()

    def _on_across_seg(self, label: str) -> None:
        self._across_files.set(
            "whole" if label.startswith("One gain") else "each")
        self._mf_settings_changed()

    def _mf_settings_changed(self) -> None:
        self._mf_dry_ok = False
        self._mf_clean_btn.configure(state="disabled")
        cleanup = (getattr(self.app.session, "cleanup", None) or {})
        if cleanup.get("written"):
            self._mf_status.set("changed since last write")

    def _mf_hash(self) -> tuple:
        return (self.chain.snapshot() if self.chain else {},
                self._across_files.get(), self._mf_bitrate.get(),
                tuple(self._multifile_files))

    def cmd_multifile_analyse(self) -> None:
        if not self._guard() or not self._multifile_files:
            return
        files = list(self._multifile_files)
        work = self.work_dir
        self._set_running(True)
        self._begin_job("scan")
        if not self.jobs.start(
            "mf-scan",
            lambda cancel, progress: self._mf_scan_job(files, work, cancel, progress),
        ):
            self._end_job()

    def _mf_scan_job(self, files, work, cancel, progress):
        from chaptertools.audio_quality.analysis import (
            quality_json_path,
            save_report,
            scan_wav,
        )
        from chaptertools.audio_quality.worker import ensure_decoded

        reports = {}
        for i, path in enumerate(files):
            progress(i / max(len(files), 1), None, f"analysing {i + 1}/{len(files)}")
            # Cache by size + mtime
            try:
                st = os.stat(path)
                cache_key = f"{st.st_size}-{int(st.st_mtime)}"
            except OSError:
                cache_key = ""
            qpath = quality_json_path(work, path)
            existing = None
            if os.path.isfile(qpath):
                from chaptertools.audio_quality.analysis import load_report
                existing = load_report(qpath)
            if existing and existing.get("cache_key") == cache_key:
                reports[path] = existing
                continue
            wav = ensure_decoded(path, work, cancel, progress)
            report = scan_wav(wav, cancel=cancel, progress=progress)
            report["cache_key"] = cache_key
            if os.path.isfile(path):
                report["source_size"] = os.path.getsize(path)
            save_report(qpath, report)
            reports[path] = report
        progress(1.0, None, "done")
        return {"reports": reports}

    def cmd_multifile_dry(self) -> None:
        if not self._multifile_files:
            return
        n = len(self._multifile_files)
        mode = self._across_files.get()
        lines = [
            f"Would clean {n} files at {self._mf_bitrate.get()}.",
            f"Loudness: {'each file to target' if mode == 'each' else 'one gain for the book'}.",
            f"Originals kept in {self.work_dir} (first write only).",
            "album.nfo and librivox.json stay; tags are preserved.",
        ]
        for line in lines:
            self._log(line)
        self._mf_dry_ok = True
        self._mf_dry_hash = self._mf_hash()
        self._mf_clean_btn.configure(state="normal")
        self._mf_status.set("Dry run ready — Clean all is enabled")

    def cmd_multifile_clean(self) -> None:
        if not self._mf_dry_ok or self._mf_hash() != self._mf_dry_hash:
            self._mf_clean_btn.configure(state="disabled")
            self._mf_status.set("Dry run again — settings changed")
            return
        if not self._guard():
            return
        files = list(self._multifile_files)
        work = self.work_dir
        snapshot = self.chain.snapshot()
        bitrate = self._mf_bitrate.get()
        across = self._across_files.get()
        folder = self._multifile_folder
        self._mf_file_states = {p: "waiting" for p in files}
        self._draw_mf_strip()
        self._set_running(True)
        self._begin_job("process")
        if not self.jobs.start(
            "mf-clean",
            lambda cancel, progress: self._mf_clean_job(
                files, work, snapshot, bitrate, across, folder, cancel, progress),
        ):
            self._end_job()

    def _mf_clean_job(self, files, work, snapshot, bitrate, across, folder,
                      cancel, progress):
        from datetime import date

        from chaptertools.audio_quality.worker import (
            output_mp3_path,
            process_file,
            replace_library,
        )

        # Optional whole-book gain: measure first file's needed gain and reuse
        fixed_gain = None
        if across == "whole" and files:
            # process_file computes gain internally; stash via chain extra
            pass
        results = []
        for i, path in enumerate(files):
            progress(i / max(len(files), 1), None,
                     f"rendering {i + 1}/{len(files)}")
            # Mark state via side channel in result; UI updates on done events
            chain_snap = dict(snapshot)
            if fixed_gain is not None:
                stages = dict(chain_snap.get("stages") or {})
                loud = dict(stages.get("loudness") or {})
                extra = dict(loud.get("extra") or {})
                extra["fixed_gain"] = fixed_gain
                loud["extra"] = extra
                stages["loudness"] = loud
                chain_snap["stages"] = stages
            info = process_file(path, work, chain_snap, bitrate, cancel, progress)
            if across == "whole" and fixed_gain is None and info.get("gain_db") is not None:
                fixed_gain = info["gain_db"]
            cleaned = info.get("mp3") or output_mp3_path(work, path)
            replace_library(path, cleaned, work)
            results.append(path)
        # Update librivox.json cleanup entry
        meta_path = os.path.join(folder, "librivox.json")
        try:
            import json
            with open(meta_path) as handle:
                meta = json.load(handle)
        except (OSError, ValueError):
            meta = {}
        meta["cleanup"] = {
            "written": date.today().isoformat(),
            "preset": self._preset_name or "custom",
            "bitrate": bitrate,
            "across": across,
            "files": len(results),
        }
        with open(meta_path, "w") as handle:
            json.dump(meta, handle, indent=2)
            handle.write("\n")
        progress(1.0, None, "done")
        return {"cleaned": results, "written": meta["cleanup"]["written"]}

    def _draw_mf_strip(self) -> None:
        if not hasattr(self, "_mf_strip"):
            return
        c = self._mf_strip
        c.delete("all")
        files = self._multifile_files
        if not files:
            return
        w = max(c.winfo_width(), 100)
        bw = max(4, w / len(files))
        colors = {
            "waiting": palette.BORDER,
            "rendering": palette.RAIL_ACCENT,
            "done": palette.RAIL_DONE,
            "failed": palette.ERROR,
        }
        for i, path in enumerate(files):
            state = self._mf_file_states.get(path, "waiting")
            # Loudness height from report
            report = self._multifile_reports.get(path) or {}
            lufs = report.get("integrated_lufs")
            h = 20
            if isinstance(lufs, (int, float)):
                # map -30..-10 to 8..24
                h = max(8, min(24, int(24 + (lufs + 10))))
            x = i * bw
            c.create_rectangle(x, 28 - h, x + bw - 1, 28,
                               fill=colors.get(state, palette.BORDER), outline="")

    def _apply_mf_reports(self, reports: dict) -> None:
        self._multifile_reports = reports or {}
        if not reports:
            return
        lufs = [r.get("integrated_lufs") for r in reports.values()
                if isinstance(r.get("integrated_lufs"), (int, float))]
        floors = [r.get("noise_floor_db") for r in reports.values()
                  if isinstance(r.get("noise_floor_db"), (int, float))]
        clips = sum(1 for r in reports.values()
                    if (r.get("true_peak_db") or -99) > -1.0)
        bits = []
        if lufs:
            bits.append(f"loudness {min(lufs):.1f} to {max(lufs):.1f} LUFS")
        if floors:
            bits.append(f"noise floor {min(floors):.0f} to {max(floors):.0f} dB")
        if clips:
            bits.append(f"{clips} clip")
        self.analysis.set_source(
            f"{len(reports)} files · " + (" · ".join(bits) if bits else "analysed"))
        # Show "This file" for the first
        first = next(iter(reports.values()))
        self._apply_report(first)
        self._draw_mf_strip()


class _IdleJobs:
    busy = False
    events: list = []

    def cancel(self) -> None:
        return None

    def start(self, name, fn) -> bool:
        return False


class _ABPlayer:
    """Plays the dry or processed preview. The bypass flag is read live."""

    def __init__(self) -> None:
        self.bypass = False
        self._stream = None
        self._dry = None
        self._wet = None
        self._pos = 0

    def play(self, dry, wet, sr: int) -> None:
        import numpy as np
        import sounddevice as sd

        self.stop()
        self._dry = _columns(dry)
        self._wet = _columns(wet)
        n = min(len(self._dry), len(self._wet))
        self._dry = np.ascontiguousarray(self._dry[:n])
        self._wet = np.ascontiguousarray(self._wet[:n])
        self._pos = 0
        self._stream = sd.OutputStream(
            samplerate=int(sr),
            channels=self._dry.shape[1],
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def _callback(self, outdata, frames, _time, _status) -> None:
        import sounddevice as sd

        src = self._dry if self.bypass else self._wet
        end = self._pos + frames
        chunk = src[self._pos:end]
        if len(chunk) < frames:
            outdata[:len(chunk)] = chunk
            outdata[len(chunk):] = 0
            self._pos = len(src)
            raise sd.CallbackStop
        outdata[:] = chunk
        self._pos = end

    @property
    def alive(self) -> bool:
        stream = self._stream
        if stream is None:
            return False
        try:
            running = bool(stream.active)
        except Exception:
            running = False
        if running:
            return True
        self.stop()
        return False

    def stop(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            return None


def _columns(audio):
    import numpy as np
    array = np.asarray(audio, dtype=np.float32)
    if array.ndim == 1:
        array = array[:, None]
    return array


def _copy_chain(source, dest) -> None:
    for stage, new in zip(dest.stages, source.stages, strict=True):
        stage.bypass = new.bypass
        stage.params = dict(new.params)
        stage.extra = dict(new.extra)


def _lock_missing(chain) -> None:
    for stage in chain.stages:
        ok, _reason = stage.availability()
        if not ok:
            stage.bypass = True


def _matching_preset(chain) -> str:
    from chaptertools.audio_quality.presets import all_presets
    from chaptertools.audio_quality.stages import default_chain
    current = _comparable(chain.snapshot())
    for name, blob in all_presets().items():
        fresh = default_chain()
        fresh.apply(blob)
        _lock_missing(fresh)
        if _comparable(fresh.snapshot()) == current:
            return name
    return ""


def _same_settings(left: dict, right: dict) -> bool:
    return _comparable(left) == _comparable(right)


def _comparable(snapshot: dict) -> dict:
    stages = {}
    for name, blob in (snapshot.get("stages") or {}).items():
        stages[name] = _stage_view(snapshot, name) if blob else {}
    return stages


def _stage_view(snapshot: dict, name: str) -> dict:
    blob = (snapshot.get("stages") or {}).get(name) or {}
    params = {}
    for key, value in (blob.get("params") or {}).items():
        if isinstance(value, bool):
            params[key] = value
        elif isinstance(value, (int, float)):
            params[key] = round(float(value), 6)
        else:
            params[key] = value
    extra = blob.get("extra") or {}
    return {
        "bypass": bool(blob.get("bypass")),
        "params": params,
        "extra": {key: value for key, value in extra.items()},
    }


def _ago(seconds: int) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _eta(seconds: float) -> str:
    seconds = int(seconds)
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min"
    return f"{secs} s"


# close is used by the detail editor; re-exported for tests that compare values.
__all__ = ["QualityTab"]
