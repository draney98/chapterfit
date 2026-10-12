"""The one playback bar. Resolve and Quality use this player."""

from __future__ import annotations

import tkinter as tk

from chaptertools.util import clock

from . import tips
from .theme import ctk_frame, equalize_segments, require_customtkinter

ctk = require_customtkinter()


class PlayerBar(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, corner_radius=0, fg_color="transparent")
        self.app = app
        self.columnconfigure(8, weight=1)
        self._build()
        session = app.session
        session.subscribe("playhead", self._show_time)
        session.subscribe("cleanup", self._show_hear)
        session.subscribe("job", self._show_job)
        self._bind_keys()

    def _build(self) -> None:
        for delta, text in ((-10, "−10"), (-5, "−5")):
            b = ctk.CTkButton(self, text=text, width=44, height=28,
                              command=lambda d=delta: self._seek(d))
            b.grid(row=0, column=0 if delta == -10 else 1, padx=1, pady=2)
        self.play_btn = ctk.CTkButton(
            self, text="▶", width=40, height=28, command=self.toggle)
        self.play_btn.grid(row=0, column=2, padx=2, pady=2)
        tips.tooltip(self.play_btn, "play or pause (Space)")
        for delta, text, col in ((5, "+5", 3), (10, "+10", 4)):
            ctk.CTkButton(self, text=text, width=44, height=28,
                          command=lambda d=delta: self._seek(d)).grid(
                row=0, column=col, padx=1, pady=2)
        self.source_var = tk.StringVar(self, value="edited")
        self._source = ctk_frame(self, fg_color="transparent")
        self.time_var = tk.StringVar(value="0:00:00 / —")
        ctk.CTkLabel(self, textvariable=self.time_var, text="").grid(
            row=0, column=6, padx=(8, 4))
        self.chapter_var = tk.StringVar(value="")
        ctk.CTkLabel(self, textvariable=self.chapter_var, text="").grid(row=0, column=7)
        self._source_seg = ctk.CTkSegmentedButton(
            self._source, values=["Edited", "Original"],
            command=self._hear_seg, height=28, border_width=None,
            dynamic_resizing=False, width=180)
        self._source_seg.set("Edited")
        self._source_seg.pack(side="left")
        equalize_segments(self._source_seg)
        self._source_seg.configure(state="disabled")
        # Keep attribute names tests and click logging already use.
        self.edited_btn = self._source_seg
        self.original_btn = self._source_seg
        self.source_buttons = (self._source_seg,)
        tips.tooltip(self._source_seg,
                     "switch the preview between edited and original audio")
        self.status_var = tk.StringVar(value="Idle")
        ctk.CTkLabel(self, textvariable=self.status_var, anchor="e", text="").grid(
            row=0, column=9, sticky="ew", padx=4)
        self.busy_label = ctk.CTkLabel(self, textvariable=self.app.busy_var, text="")
        self.busy_label.grid(row=0, column=12, padx=(6, 0))
        self._busy_tip = tips.tooltip(self.busy_label, "")
        self.log_btn = ctk.CTkButton(
            self, text="Log", width=64, height=28, command=self.app.toggle_log)
        self.log_btn.grid(row=0, column=10, padx=2, pady=2)
        tips.tooltip(self.log_btn, "open the log (Ctrl+Shift+L)")
        self.cancel_btn = ctk.CTkButton(
            self, text="Cancel job", width=96, height=28,
            command=self.app._cancel)
        tips.tooltip(self.cancel_btn, "stop the running job and any playback")
        # Shown only while a job runs (_show_job).

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
        # CTkEntry / CTkTextbox draw on an inner canvas or entry.
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
        self.log_btn.configure(text=f"Log {count}" if count else "Log")

    def update_time(self, seconds: float | None) -> None:
        session = self.app.session
        if seconds is not None:
            session.playhead = seconds
        total = session.duration
        shown = clock(session.playhead) if session.playhead else "0:00:00"
        end = clock(total) if total else "—"
        ch = ""
        if session.chapter_index is not None:
            ch = f"  · ch {session.chapter_index + 1}"
        self.time_var.set(f"{shown} / {end}{ch}")
        self._sync_button()

    def _show_time(self) -> None:
        self.update_time(None)

    def _show_job(self) -> None:
        job = self.app.session.job
        self._busy_tip.text = job.detail if job.running else ""
        if job.running:
            self.cancel_btn.grid(row=0, column=11, padx=(2, 0), pady=2)
            if not self.status_var.get() or self.status_var.get() == "Idle":
                self.status_var.set(job.detail or "Working…")
        else:
            self.cancel_btn.grid_remove()
            if not self.app.busy_var.get():
                self.status_var.set("Idle")

    def _workflow(self):
        try:
            return self.app.workflow
        except AttributeError:
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
        # Also show while a cleanup preview is loaded (stages may be mid-edit)
        loaded = (
            getattr(workflow, "_step", "") == "cleanup"
            and quality is not None
            and getattr(quality, "_preview", None) is not None
        )
        show = on > 0 or loaded
        if show:
            self._source.grid(row=0, column=5, padx=(8, 4))
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
