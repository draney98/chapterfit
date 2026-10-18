"""The one playback bar. Resolve and Quality use this player."""

from __future__ import annotations

import contextlib
import tkinter as tk

from chaptertools.util import clock

from . import tips
from .theme import ctk_frame, require_customtkinter
from .ui_kit import BODY, PAD, SMALL, C, font, number, secondary, small

ctk = require_customtkinter()


class PlayerBar(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, corner_radius=0, fg_color=C["bg"])
        self.app = app
        self.columnconfigure(8, weight=1)
        self._build()
        session = app.session
        session.subscribe("playhead", self._show_time)
        session.subscribe("cleanup", self._show_hear)
        session.subscribe("job", self._show_job)
        self._bind_keys()

    def _build(self) -> None:
        def fit(text: str) -> int:
            return font(BODY).measure(text) + 16

        col = 0
        for delta, text in ((-10, "−10"), (-5, "−5")):
            secondary(self, text, command=lambda d=delta: self._seek(d),
                      width=fit(text)).grid(
                row=0, column=col, padx=(0, 4), pady=PAD // 2)
            col += 1
        self.play_btn = secondary(self, "▶", command=self.toggle, width=fit("▶"))
        self.play_btn.grid(row=0, column=col, padx=(0, 4), pady=PAD // 2)
        tips.tooltip(self.play_btn, "play or pause (Space)")
        col += 1
        for delta, text in ((5, "+5"), (10, "+10")):
            secondary(self, text, command=lambda d=delta: self._seek(d),
                      width=fit(text)).grid(
                row=0, column=col, padx=(0, 4), pady=PAD // 2)
            col += 1
        self.source_var = tk.StringVar(self, value="edited")
        self._source = ctk_frame(self, fg_color="transparent")
        self.time_var = tk.StringVar(value="0:00:00 / —")
        self._time_lbl = number(self, text="")
        self._time_lbl.configure(textvariable=self.time_var, anchor="w")
        self._time_lbl.grid(row=0, column=6, padx=(PAD, PAD // 2))
        self.chapter_var = tk.StringVar(value="")
        small(self, text="").configure(textvariable=self.chapter_var)
        # chapter label kept for API; packed into time string instead
        self._source_seg = ctk.CTkSegmentedButton(
            self._source, values=["Edited", "Original"],
            command=self._hear_seg, height=28, border_width=None,
            dynamic_resizing=True,
            font=font(SMALL), fg_color=C["sunken"],
            selected_color=C["accent_soft"], selected_hover_color=C["accent_soft"],
            unselected_color=C["sunken"], unselected_hover_color=C["border"],
            text_color=C["text"])
        self._source_seg.set("Edited")
        self._source_seg.pack(side="left")
        from .theme import paint_segment_text
        paint_segment_text(self._source_seg)
        self._source_seg.configure(state="disabled")
        self.edited_btn = self._source_seg
        self.original_btn = self._source_seg
        self.source_buttons = (self._source_seg,)
        tips.tooltip(self._source_seg,
                     "switch the preview between edited and original audio")
        self.status_var = tk.StringVar(value="Idle")
        self._status_lbl = small(self, text="")
        self._status_lbl.configure(
            textvariable=self.status_var, anchor="e")
        self._status_lbl.grid(row=0, column=9, sticky="ew", padx=PAD // 2)
        self.progress = ctk.CTkProgressBar(
            self, width=120, height=8, progress_color=C["accent"],
            fg_color=C["border"])
        self.progress.set(0)
        self.busy_label = small(self, text="")
        self.busy_label.configure(textvariable=self.app.busy_var)
        self._busy_tip = tips.tooltip(self.busy_label, "")
        self.cancel_btn = secondary(
            self, "Cancel", command=self.app._cancel, width=80)
        tips.tooltip(self.cancel_btn, "stop the running job and any playback")
        self.log_btn = secondary(self, "Log", command=self.app.toggle_log,
                                 width=fit("Log"))
        self.log_btn.grid(row=0, column=13, padx=(PAD * 2, 0), pady=PAD // 2)
        tips.tooltip(self.log_btn, "open the log (Ctrl+Shift+L)")

    def _bind_keys(self) -> None:
        root = self.winfo_toplevel()
        root.bind_all("<space>", self._space, add="+")
        root.bind_all("<Left>", lambda e: self._arrow(e, -5))
        root.bind_all("<Right>", lambda e: self._arrow(e, 5))
        root.bind_all("<Shift-Left>", lambda e: self._arrow(e, -10))
        root.bind_all("<Shift-Right>", lambda e: self._arrow(e, 10))

    def _typing(self, event) -> bool:
        w = event.widget
        if not isinstance(w, tk.Misc):
            return False
        kind = w.winfo_class()
        if kind in ("Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox"):
            return True
        master = getattr(w, "master", None)
        while master is not None:
            name = type(master).__name__
            if name in ("CTkEntry", "CTkTextbox"):
                return True
            master = getattr(master, "master", None)
        return False

    def _space(self, event):
        if self._typing(event):
            return None
        self.toggle()
        return "break"

    def _arrow(self, event, delta):
        if self._typing(event):
            return None
        self._seek(delta)
        return "break"

    def toggle(self) -> None:
        workflow = self._workflow()
        quality = getattr(workflow, "quality", None)
        if getattr(workflow, "_step", "") == "cleanup" and quality is not None:
            preview = getattr(quality, "_player", None)
            if preview is not None and preview.alive:
                preview.stop()
                self._sync_button()
                return
            if workflow.audio:
                span = self.app.session.selection
                start = span[0] if span else (self.app.session.playhead or 0.0)
                quality.cmd_play(start)
            self._sync_button()
            return
        player = self.app.player
        if player.alive:
            player.toggle_pause()
        elif workflow is not None and workflow.audio:
            start = self.app.session.playhead or 0.0
            if getattr(workflow, "section_files", None):
                workflow.play_at_book_time(start)
            else:
                self.app.play(workflow.audio, start=start or None, length=0)
        self._sync_button()

    def _seek(self, delta: float) -> None:
        workflow = self._workflow()
        if workflow is not None and getattr(workflow, "section_files", None):
            t = (self.app.session.playhead or 0.0) + delta
            workflow.play_at_book_time(max(0.0, t))
            return
        if self.app.player.alive:
            self.app.player.seek(delta)

    def _hear_seg(self, label: str) -> None:
        self.source_var.set("original" if label == "Original" else "edited")
        self._hear()

    def _hear(self) -> None:
        quality = getattr(self.app.workflow, "quality", None)
        if quality is None or not hasattr(quality, "hear_var"):
            return
        quality.hear_var.set(self.source_var.get() == "original")
        if hasattr(quality, "_toggle_hear"):
            quality._toggle_hear()

    def show_status(self, line: str) -> None:
        self.status_var.set(line or "Idle")

    def set_log_errors(self, count: int) -> None:
        self.log_btn.configure(
            text=f"Log ({count})" if count else "Log")

    def update_time(self, seconds: float | None) -> None:
        session = self.app.session
        if seconds is not None:
            session.playhead = seconds
        total = session.duration
        shown = clock(session.playhead) if session.playhead else "0:00:00"
        end = clock(total) if total else "—"
        self.time_var.set(f"{shown} / {end}")
        self._sync_button()

    def _show_time(self) -> None:
        self.update_time(None)

    def _show_job(self) -> None:
        job = self.app.session.job
        self._busy_tip.text = job.detail if job.running else ""
        if job.running:
            self.progress.grid(row=0, column=10, padx=(2, 0), pady=PAD // 2)
            self.progress.configure(mode="indeterminate")
            self.progress.start()
            self.cancel_btn.grid(row=0, column=11, padx=(2, 0), pady=PAD // 2)
            # Progress ticks update the tooltip only. The bar keeps the
            # sentence from when this job started.
            if getattr(self, "_status_job", None) != job.name:
                self.status_var.set(job.detail or "Working…")
                self._status_job = job.name
            # Hide the idle busy glyph; progress bar is the signal.
            self.busy_label.grid_remove()
        else:
            stop = getattr(self.progress, "stop", None)
            if callable(stop):
                with contextlib.suppress(tk.TclError):
                    self.progress.stop()
            self.progress.grid_remove()
            self.cancel_btn.grid_remove()
            self._status_job = None
            if not self.app.busy_var.get():
                self.status_var.set("Idle")

    def _workflow(self):
        with contextlib.suppress(AttributeError):
            return self.app.workflow
        return None

    def _preview_player(self):
        workflow = self._workflow()
        quality = getattr(workflow, "quality", None)
        return getattr(quality, "_player", None)

    def _show_hear(self) -> None:
        """Edited/Original only when Audio cleanup has at least one stage on."""
        workflow = self._workflow()
        quality = getattr(workflow, "quality", None)
        on = int(self.app.session.cleanup.get("stages_on") or 0)
        loaded = (
            getattr(workflow, "_step", "") == "cleanup"
            and quality is not None
            and getattr(quality, "_preview", None) is not None
        )
        if on > 0 or loaded:
            self._source.grid(row=0, column=5, padx=(PAD, PAD // 2))
            state = "normal" if loaded else "disabled"
            self._source_seg.configure(state=state)
            want = "Original" if self.source_var.get() == "original" else "Edited"
            self._source_seg.set(want)
        else:
            self._source.grid_remove()

    def _sync_button(self) -> None:
        preview = self._preview_player()
        previewing = (
            getattr(self._workflow(), "_step", "") == "cleanup"
            and preview is not None and preview.alive
        )
        playing = previewing or (
            self.app.player.alive and not self.app.player.paused)
        self.play_btn.configure(text="❚❚" if playing else "▶")
