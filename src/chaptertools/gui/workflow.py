"""Main-area controller: step rail, timeline, and the two stacks.

WorkflowPanel owns the command plumbing (one method per chapterfit
subcommand, all subprocess-driven via app.runner). The log lives in the
app's drawer.
"""
from __future__ import annotations

import contextlib
import json
import os
import queue
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

from chaptertools.nfo import chapters_differ, read_nfo_chapters
from chaptertools.util import clock, hms, package_env, parse_time

from . import palette, tips
from .busy import job_is_modal, opening_sentence
from .commands import (
    META_FLAGS,
    align_argv,
    chapterfit_prefix,
    emit_argv,
    find_asin_argv,
    identify_argv,
    job_argv,
    measure_argv,
    near_spec,
    same_source,
    whisper_argv,
)
from .facts import MediaFactsLoader
from .edition_fit import (
    fit_cell,
    fit_from_identification,
    fit_sort_key,
    format_length,
    load_fit_cache,
    numbers_line,
    plain_found_summary,
    reason_sentence,
    save_fit_cache,
    source_group_label,
    verdict_headline,
)
from .harvest import placeholder_harvest
from .library import (
    analysis_path,
    asin_candidates,
    asin_emit_defaults,
    asin_query,
    asin_result_path,
    load_analysis,
    top_asin,
    work_dir_for,
)
from .panels import StagePanels
from .projection import WorkflowProjection
from .runner import Job
from .stages import (
    BLOCKED,
    DONE,
    STAGE_LABELS,
    DiskFacts,
    identify_verdict,
    next_stage,
    resolve_evidence,
    stage_states,
)
from .step_rail import STAGE_STEP, STEPS, StepRail, Workspace
from .table import CHAPTER_COLUMNS, TableViews
from .theme import accent_colors, ctk_frame, require_customtkinter, set_enabled
from .timeline import Timeline

ctk = require_customtkinter()


def resolve_chapterfit(python: str | None = None) -> list[str]:
    """chapterfit as a module of the package this GUI runs from; jobs get
    package_env() so any interpreter finds it (the whisper venv included).
    """
    return chapterfit_prefix(python)


def whisper_python() -> str | None:
    p = os.environ.get("WHISPER_PYTHON") \
        or os.path.expanduser("~/.venvs/whisper/bin/python")
    return p if os.path.isfile(p) and os.access(p, os.X_OK) else None


def _elide(text: str, font, room: int) -> str:
    """Drop characters from the middle until `text` fits in `room` px."""
    if font.measure(text) <= room:
        return text
    lo, hi = 0, len(text)
    while lo < hi:  # most characters kept that still fit
        mid = (lo + hi + 1) // 2
        cand = text[:(mid + 1) // 2] + "…" + text[len(text) - mid // 2:]
        if font.measure(cand) <= room:
            lo = mid
        else:
            hi = mid - 1
    return text[:(lo + 1) // 2] + "…" + text[len(text) - lo // 2:]


def _chapter_counts(audio: str, work_dir: str | None) -> dict[str, str]:
    path = asin_result_path(audio, work_dir) if audio else ""
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    counts = {}
    for cand in data.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        asin = str(cand.get("asin") or "").strip()
        chapters = cand.get("chapters")
        if isinstance(chapters, list):
            counts[asin] = str(len(chapters))
        elif cand.get("chapter_count") is not None:
            counts[asin] = str(cand["chapter_count"])
    return counts


def _stack(parent):
    """One frame per step, stacked in the same cell."""
    host = ctk_frame(parent, fg_color="transparent", corner_radius=0)
    host.pack(fill="both", expand=True)
    host.rowconfigure(0, weight=1)
    host.columnconfigure(0, weight=1)
    frames = {}
    for key in STEPS:
        frame = ctk_frame(host, fg_color="transparent", corner_radius=0)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frames[key] = frame
    return frames


def _t(v) -> str:
    """No empty time cells: a zero clock instead of '-' or blank."""
    if v is None or v == "" or v == "-":
        return "0:00:00.000"
    return str(v) if isinstance(v, str) and ":" in v else hms(v)


_MODE_LABEL = {
    "search": "Search", "asin": "ASIN", "json": "JSON",
    "emb": "Embedded", "nfo": "NFO",
}


class WorkflowPanel(TableViews, StagePanels, ctk.CTkFrame):
    """Coordinator: header, step rail, timeline, and the two stacks."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.app = app
        self.audio: str | None = None
        self.work_dir: str | None = None
        self.analysis: dict = {}
        self.harvest: dict = {}
        self._row_data: dict = {}
        self._cf = chapterfit_prefix()
        self._stage: str = "measure"
        self._disk: DiskFacts | None = None
        self.facts = MediaFactsLoader()
        self._facts_token = 0     # newest facts request
        self._harvest_token = 0   # newest request that also wants a harvest
        self._harvest_shown: dict = {}  # field values we filled in last
        self._refresh_after = None
        # audio path -> artifact mtime when its finalize dry run succeeded
        self._dry_ok: dict[str, float | None] = {}
        self._dry_hash: dict[str, tuple] = {}
        self._after_cleanup = None
        self._chaptered_for_apply: str | None = None
        self._pin_autofilled = False
        self._sweep_autofilled = False
        self._busy = False
        self._tips: dict = {}

        self._build()
        self._watch_finish_settings()
        self._refresh()

    def _tip(self, widget, text: str):
        self._tips[widget] = tip = tips.tooltip(widget, text)
        return tip

    def _set_identify_mode(self, mode: str) -> None:
        self.identify_mode.set(mode)
        seg = getattr(self, "_identify_seg", None)
        if seg is not None:
            seg.set(_MODE_LABEL.get(mode, "ASIN"))

    # ------------------------------------------------------------ widgets --
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._step = "scan"
        self._rescan_quality = False
        self.chapters_var = tk.StringVar(value="Chapters")

        from .ui_kit import C, body, number, title

        shell = ctk_frame(self, fg_color="transparent")
        shell.grid(row=0, column=0, sticky="nsew", padx=14, pady=(4, 0))
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1)

        head = ctk_frame(shell, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.book_var = tk.StringVar(value="(nothing selected)")
        row = ctk_frame(head, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew")
        self._header_row = row
        self._h_title = title(row, "(nothing selected)")
        self._h_author = body(row, text="")
        self._h_dur = number(row, text="")
        self._h_source = number(row, text="")
        self._h_file = body(row, text="")
        for label in (self._h_author, self._h_dur, self._h_source, self._h_file):
            label.configure(text_color=C["muted"], anchor="w")
        self._h_title.pack(side="left")
        for label in (self._h_author, self._h_dur, self._h_source, self._h_file):
            label.pack(side="left")
        self._book_label = self._h_title
        self._header_text = "(nothing selected)"
        self._header_bits = None
        def _schedule_header(event):
            pending = getattr(self, "_header_fit_after", None)
            if pending is not None:
                self.after_cancel(pending)
            width = event.width
            self._header_fit_after = self.after(
                30, lambda: self._fit_header(width))

        head.bind("<Configure>", _schedule_header)
        self.head_tooltip = None
        for widget in (row, self._h_title, self._h_author, self._h_dur,
                       self._h_source, self._h_file):
            widget.bind("<Enter>", self._show_path_tip)
            widget.bind("<Leave>", self._hide_tip)
        self.found_var = tk.StringVar(value="")

        self.rail = StepRail(head, self.show_step)
        self.rail.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        self.timeline = Timeline(shell, self.app)
        self.timeline.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.gap_mode_var = self.timeline.mode

        self.workspace = Workspace(shell)
        self.workspace.grid(row=2, column=0, sticky="nsew", pady=(4, 0))
        self._main_frames = _stack(self.workspace.main)
        self._insp_frames = _stack(self.workspace.inspector)
        self._wrap_labels = []
        self.workspace.inspector.bind("<Configure>", self._on_host_resize)
        place = ttk.Panedwindow(self._insp_frames["place"], orient="vertical")
        place.grid(row=0, column=0, sticky="nsew")
        match_host = self._insp_frames["match"]
        match_host.rowconfigure(0, weight=1)
        match_host.columnconfigure(0, weight=1)
        from .ui_kit import C as kit_colors
        self._match_scroll = ctk.CTkScrollableFrame(
            match_host, fg_color=kit_colors["bg"], corner_radius=0)
        self._match_scroll.grid(row=0, column=0, sticky="nsew")
        self._match_scroll.columnconfigure(0, weight=1)
        inner = getattr(self._match_scroll, "_scrollable_frame", None)
        if inner is not None:
            inner.bind("<Configure>", self._fit_match_scroll, add="+")
        self._panels = {
            "measure": self._panel_measure(self._main_frames["scan"]),
            "identify": self._panel_identify(self._match_scroll),
            "align": self._panel_align(place),
            "resolve": self._panel_resolve(place),
            "emit": self._panel_emit(self._main_frames["finish"]),
            "finalize": self._panel_finalize(self._insp_frames["finish"]),
        }
        for key in ("measure", "emit", "finalize"):
            self._panels[key].grid(row=0, column=0, sticky="nsew")
        self._panels["identify"].grid(row=0, column=0, sticky="ew")
        self._build_book_info(self._match_scroll)
        self._build_editions(self._main_frames["match"])
        place.add(self._panels["align"], weight=1)
        place.add(self._panels["resolve"], weight=2)

        def _keep_align_visible(_event=None):
            try:
                if place.winfo_height() > 220 and place.sashpos(0) < 160:
                    place.sashpos(0, 160)
            except tk.TclError:
                return

        place.bind("<Configure>", _keep_align_visible, add="+")
        place.after_idle(_keep_align_visible)
        self._build_chapters_tab(self._main_frames["place"])
        self._build_quality_tab(self._main_frames["cleanup"])
        from .multifile_book import MultifileBook
        self.multifile = MultifileBook(shell, self.app, on_cleanup=self._open_multifile_cleanup)
        self._multifile_mode = False
        self.section_files: list[str] = []
        self.section_starts: list[float] = []
        self.section_durations: list[float] = []
        self._section_index = 0
        self._concat_path: str | None = None
        self._librivox_folder: str | None = None
        self._librivox_pins_done = False
        self._bind_step_keys()
        self.show_step("scan")

    TABLE_MIN_ROWS = 4

    def _panel_need(self, key) -> tuple[int, int]:
        """(width, height) a stage panel needs; Resolve counted stacked."""
        if key != "resolve":
            p = self._panels[key]
            return p.winfo_reqwidth(), p.winfo_reqheight()
        left = self._resolve_left
        summary = self._panels["resolve"].grid_slaves(row=0)[0]
        w = max(left.winfo_reqwidth(), summary.winfo_reqwidth()) + 8
        h = summary.winfo_reqheight() + left.winfo_reqheight() + 16
        return w, h

    def min_width(self) -> int:
        self.update_idletasks()
        table = sum(int(self.tree.column(c, "minwidth"))
                    for c, *_ in CHAPTER_COLUMNS) + 24
        return max([self._panel_need(k)[0] for k in self._panels]
                   + [self.rail.winfo_reqwidth(), table]) + 16

    def min_height(self) -> int:
        """Header, timeline, and enough rows that the table is usable."""
        self.update_idletasks()
        rowh = int(ttk.Style(self).lookup("Chapter.Treeview", "rowheight")
                   or 24)
        chrome = self.grid_slaves(row=0)[0].winfo_reqheight()
        chrome += self.timeline.winfo_reqheight()
        return chrome + rowh * (self.TABLE_MIN_ROWS + 1) + 80

    def _on_host_resize(self, event):
        """Long text wraps to the panel width instead of widening it."""
        if not hasattr(self, "_resolve_left"):
            return
        width = event.width
        for lbl in self._wrap_labels:
            with contextlib.suppress(tk.TclError):
                lbl.configure(wraplength=max(width - 24, 200))
        self._layout_resolve(side_by_side=True)

    # ---------------------------------------------------------- stage panels
    def _copy_header_fix(self):
        fix = self._header_fix()
        if fix:
            self.clipboard_clear()
            self.clipboard_append(fix)
            self.app.log("copied: " + fix)

    # ------------------------------------------------------------- tabs --
    def _build_quality_tab(self, parent):
        from chaptertools.gui.quality_tab import QualityTab
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        # At 1366x768 the chain is taller than the pane. Scrolling keeps
        # every control reachable instead of painting past the window.
        scroll = ctk.CTkScrollableFrame(
            parent, fg_color="transparent", corner_radius=0, height=120)
        scroll.grid(row=0, column=0, sticky="nsew")
        self.quality = QualityTab(scroll, app=self.app, workflow=self)
        self.quality.pack(fill="both", expand=True)

    def _bind_step_keys(self) -> None:
        root = self.winfo_toplevel()
        for index, key in enumerate(STEPS, start=1):
            root.bind_all(f"<Control-Key-{index}>",
                          lambda _e, step=key: self.show_step(step))

    # -------------------------------------------------------------- state --
    def show_step(self, step: str) -> None:
        if step not in STEPS:
            return
        self._auto_stage = False
        self._step = step
        for key, frame in self._main_frames.items():
            if key == step:
                frame.grid()
            else:
                frame.grid_remove()
        for key, frame in self._insp_frames.items():
            if key == step:
                frame.grid()
            else:
                frame.grid_remove()
        self.workspace.set_inspector(
            step in ("match", "place"), table=step == "place")
        self.timeline.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self._sync_rail()
        bar = getattr(self.app, "player_bar", None)
        if bar is not None:
            bar._show_hear()

    def set_stage(self, key: str):
        if key in STAGE_STEP:
            self._stage = key
            self.show_step(STAGE_STEP[key])
            return
        if key in STEPS:
            self.show_step(key)

    def _finish_rail_detail(self, states: dict) -> str:
        """Sub-status for Finish: Place gate, dry run, ready, or applied."""
        from .stages import finish_step_status, place_step_status
        min_conf = self.min_conf.get() if hasattr(self, "min_conf") else 0.5
        place = place_step_status(self.analysis or {}, min_conf)
        fin = finish_step_status(
            place, states.get("finalize") or {}, dry_ok=self._finalize_dry_ok)
        if self._finalize_dry_ok and place.get("state") == DONE:
            applied = getattr(self, "_applied_at", None)
            if applied and (states.get("finalize") or {}).get("state") == DONE:
                return f"applied {applied}"
        return fin.get("detail") or "needs a dry run"

    def _sync_rail(self, states: dict | None = None) -> None:
        from .stages import finish_step_status, place_step_status
        states = states or self._compute_states()
        preset = self.app.session.cleanup.get("preset") or ""
        on = int(self.app.session.cleanup.get("stages_on") or 0)
        written = self.app.session.cleanup.get("written") or ""
        if written:
            cleanup = f"{preset or 'cleanup'} · written {written}"
        elif on:
            name = preset or "Custom"
            if name.lower() == "off":
                name = "Custom"
            elif name and name[0].islower():
                name = name[:1].upper() + name[1:]
            cleanup = f"{name} · {on} stages"
        else:
            cleanup = "off · optional"
        min_conf = self.min_conf.get() if hasattr(self, "min_conf") else 0.5
        place = place_step_status(
            self.analysis or {}, min_conf,
            nfo_chapters=(self.harvest or {}).get("nfo_chapters") or [])
        finish = finish_step_status(
            place, states.get("finalize") or {}, dry_ok=self._finalize_dry_ok)
        detail = self._finish_rail_detail(states)
        self.rail.update(
            states, self._step, cleanup, finish_detail=detail,
            place_status=place, finish_status=finish)

    # ------------------------------------------------------ media facts --
    def _request_facts(self, harvest: bool = False):
        """Ask the worker for folder/ffprobe facts; they arrive via
        poll_facts, so nothing here blocks the Tk thread."""
        if not self.audio:
            return
        self._facts_token += 1
        if harvest:
            self._harvest_token = self._facts_token
        self.facts.request(self._facts_token, self.audio,
                           (self.analysis or {}).get("emitted"), harvest)

    def poll_facts(self) -> bool:
        """Apply whatever the worker has finished (called by the pump)."""
        applied = False
        while True:
            try:
                token, audio, disk, harvest, err = \
                    self.facts.results.get_nowait()
            except queue.Empty:
                return applied
            self._apply_facts(token, audio, disk, harvest, err)
            applied = True

    def wait_for_facts(self, timeout: float = 10.0) -> None:
        """Block until the newest request is answered (tests, scripts)."""
        deadline = time.monotonic() + timeout
        while self._facts_pending():
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("media facts did not arrive")
            try:
                item = self.facts.results.get(timeout=left)
            except queue.Empty:
                continue
            self._apply_facts(*item)

    def _facts_pending(self) -> bool:
        return self._facts_seen < self._facts_token \
            or self._harvest_seen < self._harvest_token

    _facts_seen = 0
    _harvest_seen = 0

    def _apply_facts(self, token, audio, disk, harvest, err):
        if audio != self.audio:
            return  # a book we have since left
        self._facts_seen = max(self._facts_seen, token)
        if harvest is not None or err is not None:
            self._harvest_seen = max(self._harvest_seen, token)
        if err is not None:
            self.app.log(f"! could not read {os.path.basename(audio)}: {err}")
            return
        if token >= self._facts_token and disk is not None:
            self._disk = disk
        if harvest is not None and token >= self._harvest_token:
            self._set_harvest(harvest)
        self._refresh()
        self._maybe_identify_from_nfo()
        if self._auto_stage:
            self._auto_stage = False
            self.set_stage(next_stage(self._compute_states()))

    def _set_harvest(self, harvest: dict):
        """Fill Book info, leaving any field the user has edited."""
        self.harvest = harvest
        self.found_var.set(plain_found_summary(harvest))
        self._apply_book_info()
        self._update_header()

    def _apply_book_info(self):
        """Emit fields: harvest suggestion, then the identified ASIN."""
        if not hasattr(self, "meta_vars"):
            return
        suggested = dict((self.harvest or {}).get("suggested") or {})
        label = ((self.analysis or {}).get("identification") or {}).get(
            "label") or ""
        for key, value in asin_emit_defaults(
                self.audio or "", self.work_dir, label).items():
            if value:
                suggested[key] = value
        for key, var in self.meta_vars.items():
            new = suggested.get(key, "")
            if var.get() == self._harvest_shown.get(key, var.get()):
                var.set(new)
            self._harvest_shown[key] = new

    _auto_stage = False

    def _compute_states(self) -> dict:
        states = stage_states(self.analysis,
                              self.min_conf.get() if hasattr(self, "min_conf")
                              else 0.5,
                              self._disk or DiskFacts())
        return states

    def _artifact_mtime(self) -> float | None:
        if not self.audio:
            return None
        try:
            return os.path.getmtime(analysis_path(self.audio, self.work_dir))
        except OSError:
            return None

    @property
    def _finalize_dry_ok(self) -> bool:
        """A dry run succeeded for this book and the settings still match."""
        return bool(self.audio) and self.audio in self._dry_ok \
            and self._dry_ok[self.audio] == self._artifact_mtime() \
            and self._dry_hash.get(self.audio) == self._finish_hash()

    def set_book(self, audio: str):
        from .multifile_book import is_multifile_book, list_audio, load_librivox

        path = os.path.abspath(audio)
        self._leave_multifile()
        self.section_files = []
        self.section_starts = []
        self.section_durations = []
        self._section_index = 0
        self._concat_path = None
        self._librivox_folder = None
        self._librivox_pins_done = False

        if is_multifile_book(path):
            folder = path if os.path.isdir(path) else os.path.dirname(path)
            files = list_audio(folder)
            self._librivox_folder = folder
            self.section_files = files
            self.audio = files[0] if files else folder
            self.work_dir = work_dir_for(self.audio if files else folder)
            self._load_section_timeline(folder, files, load_librivox(folder))
            if files and self.work_dir:
                from .librivox_book import write_ffconcat
                self._concat_path = write_ffconcat(
                    files, os.path.join(self.work_dir, "book.ffconcat"))
            self.app.session.book = folder
            self.book_var.set(folder)
            if hasattr(self, "embed_var"):
                self.embed_var.set(False)
        else:
            self.audio = path
            self.work_dir = work_dir_for(self.audio)
            self.app.session.book = self.audio
            self.book_var.set(self.audio)
            if hasattr(self, "embed_var"):
                self.embed_var.set(True)

        self.app.session.playhead = 0.0
        self.app.session.selection = None
        self.app.session.preview_marks = []
        self.app.session.emit("book")
        if hasattr(self, "timeline"):
            self.timeline.view = None
        folder = (self._librivox_folder
                  or os.path.dirname(self.audio or "") or "")
        title = os.path.basename(folder) or os.path.basename(self.audio or "")
        author = os.path.basename(os.path.dirname(folder)) if folder else ""
        rule = "────────"
        self.app.log(rule)
        self.app.log(f"{author} / {title}" if author else title)
        self.app.log(rule)
        # per-book field provenance: the next row selection fills them again
        self.pin_ch.set("")
        self.pin_time.set("0:00:00.000")
        self.sweep_start.set("")
        self.sweep_end.set("")
        self._pin_autofilled = False
        self._sweep_autofilled = False
        self._disk = None
        self._harvest_shown = {}
        self._nfo_decided = False
        self._set_harvest(placeholder_harvest(self.audio))
        self._refresh()
        if getattr(self, "quality", None) is not None:
            if self.section_files and hasattr(self.quality, "set_multifile"):
                self.quality.set_multifile(self._librivox_folder, self.work_dir)
            else:
                self.quality.set_book(self.audio, self.work_dir)
        self._prefill_identify()
        self._fill_asin_list()
        self._fill_asin_query()
        self.set_stage(next_stage(self._compute_states()))
        # the stage is chosen again once disk facts are in, unless the
        # user picks one first
        self._auto_stage = True
        self._request_facts(harvest=True)
        self._maybe_autoscan()
        if hasattr(self.app, "sync_library_strip"):
            self.app.sync_library_strip()

    def _load_section_timeline(self, folder: str, files: list[str],
                               meta: dict | None) -> None:
        """Probe each section (fall back to librivox.json playtimes)."""
        from chaptertools.media import probe_duration

        from .librivox_book import cumulative_starts

        meta = meta or {}
        sections = meta.get("sections") or []
        durs: list[float] = []
        for i, path in enumerate(files):
            probed = probe_duration(path)
            if probed and probed > 0:
                durs.append(probed)
                continue
            sec = sections[i] if i < len(sections) else {}
            durs.append(float(sec.get("duration") or 0.0))
        self.section_durations = durs
        self.section_starts = cumulative_starts(durs)
        if durs and any(d > 0 for d in durs):
            self._refresh_librivox_nfo(folder, files, meta, durs)

    def _refresh_librivox_nfo(self, folder: str, files: list[str],
                              meta: dict, durs: list[float]) -> None:
        """Rewrite album.nfo starts from probed file lengths when they differ."""
        from chaptertools.nfo import (
            chapters_differ,
            read_nfo_chapters,
            render_nfo,
        )

        from .librivox_book import chapters_from_durations

        sections = meta.get("sections") or []
        titles = []
        for i, path in enumerate(files):
            sec = sections[i] if i < len(sections) else {}
            titles.append(
                (sec.get("title") or os.path.splitext(
                    os.path.basename(path))[0]).strip())
        chapters = chapters_from_durations(titles, durs)
        nfo_path = os.path.join(folder, "album.nfo")
        existing_ch = read_nfo_chapters(nfo_path)
        if existing_ch and not chapters_differ(existing_ch, chapters):
            return
        existing_text = None
        if os.path.isfile(nfo_path):
            try:
                with open(nfo_path) as handle:
                    existing_text = handle.read()
            except OSError:
                existing_text = None
        text = render_nfo(
            existing_text, chapters,
            meta.get("title") or os.path.basename(folder),
            meta.get("author") or "")
        try:
            with open(nfo_path, "w") as handle:
                handle.write(text)
        except OSError as exc:
            self.app.log(f"! could not refresh album.nfo: {exc}")

    def _enter_multifile(self, folder: str) -> None:
        self._multifile_mode = True
        self.rail.grid_remove()
        self.timeline.grid_remove()
        self.workspace.grid_remove()
        self.multifile.grid(row=2, column=0, sticky="nsew", pady=(4, 0))
        self.multifile.load(folder)
        self._step = "multifile"
        quality = getattr(self, "quality", None)
        if quality is not None and hasattr(quality, "set_multifile"):
            quality.set_multifile(folder, self.work_dir)

    def _leave_multifile(self) -> None:
        if not getattr(self, "_multifile_mode", False):
            return
        self._multifile_mode = False
        quality = getattr(self, "quality", None)
        if quality is not None and hasattr(quality, "show_multifile_bar"):
            quality.show_multifile_bar(False)
            if hasattr(quality, "_multifile"):
                quality._multifile = False
        self.multifile.grid_remove()
        self.rail.grid()
        self.timeline.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.workspace.grid(row=2, column=0, sticky="nsew", pady=(4, 0))

    def _open_multifile_cleanup(self) -> None:
        """Show Audio cleanup for a multi-file book (no Scan / Finish)."""
        if not self._multifile_mode:
            return
        self.multifile.grid_remove()
        self.workspace.grid(row=2, column=0, sticky="nsew", pady=(4, 0))
        self.rail.grid_remove()
        self.timeline.grid_remove()
        self.show_step("cleanup")
        # Back affordance via status strip on multifile; quality gets a back button
        quality = getattr(self, "quality", None)
        if quality is not None and hasattr(quality, "show_multifile_bar"):
            quality.show_multifile_bar(True)

    def return_from_multifile_cleanup(self) -> None:
        if not self._multifile_mode:
            return
        folder = self.multifile.folder
        self.workspace.grid_remove()
        self.multifile.grid(row=2, column=0, sticky="nsew", pady=(4, 0))
        if folder:
            self.multifile.load(folder)
        self._step = "multifile"

    def _pick_stage(self, key: str):
        """A stage chosen by the user: no automatic re-choice after that."""
        self._auto_stage = False
        self.set_stage(key)

    def _prefill_identify(self):
        """Show which source the last identify used - without clobbering
        anything the user is typing."""
        label = (self.analysis.get("identification") or {}).get("label") \
            or ""
        current = self.identify_value.get().strip()
        if current and not getattr(self, "_identify_autofilled", False):
            return
        if label.startswith("Audnexus "):
            self._set_identify_mode("asin")
            self.identify_value.set(label.split(" ", 1)[1].strip())
            self._identify_autofilled = True
        elif label:
            if os.path.basename(label) == "chapters.embedded.json":
                self._set_identify_mode("emb")
            elif os.path.basename(label) == "chapters.nfo.json":
                self._set_identify_mode("nfo")
            elif os.path.isfile(label):
                self._set_identify_mode("json")
            else:
                return
            self.identify_value.set(label)
            self._identify_autofilled = True
        elif (asin := top_asin(self.audio or "", self.work_dir)):
            self._set_identify_mode("asin")
            self.identify_value.set(asin)
            self._identify_autofilled = True
        elif getattr(self, "_identify_autofilled", False):
            self.identify_value.set("")
            self._identify_autofilled = False
            return
        else:
            return
        self._identify_mode_changed()

    def _reharvest(self):
        """Re-read button: read the folder again and refill every Book info
        field (an explicit request, so edits are replaced)."""
        if not self.audio:
            return
        self._harvest_shown = {}
        self.found_var.set("Reading tags and chapters from the file…")
        self._request_facts(harvest=True)

    def _update_header(self):
        """Title is bold. Author, duration, edition and file stay regular."""
        from .projection import audio_duration
        from .stages import short_edition_label
        s = self.harvest.get("suggested") or {}
        title = s.get("title") or "(nothing selected)"
        author = s.get("author") or ""
        dur = audio_duration(self.analysis)
        ident = self.analysis.get("identification") or {}
        source = short_edition_label(
            ident.get("label") or ident.get("candidate"))
        if not source:
            source = short_edition_label(
                top_asin(self.audio or "", self.work_dir) or "")
        name = os.path.basename(self.audio) if self.audio else ""
        self._header_bits = {
            "title": title,
            "author": author,
            "duration": hms(dur) if dur else "",
            "source": source,
            "name": name,
        }
        self._header_paths = self._header_path_lines()
        self._fit_header(self._header_row.master.winfo_width())

    def _header_path_lines(self) -> str:
        """Full paths for the header tooltip (audio + chapter sources)."""
        lines = []
        if self.audio:
            lines.append(self.audio)
            folder = os.path.dirname(self.audio)
            for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
                low = name.lower()
                if low.startswith("chapters") and (
                        low.endswith(".json") or low.endswith(".nfo")):
                    path = os.path.join(folder, name)
                    if path not in lines:
                        lines.append(path)
        return "\n".join(lines) if lines else ""

    def _fit_header(self, width: int):
        """The header stays one line: the file name is elided from the
        middle first, then the title (full paths live in the tooltip)."""
        bits = getattr(self, "_header_bits", None) or {
            "title": self.book_var.get() or "(nothing selected)",
            "author": "", "duration": "", "source": "", "name": "",
        }
        title = bits["title"]
        name = bits["name"]
        room = max(width - 8, 200) if width > 1 else 10 ** 6

        def piece(label, text):
            return label.cget("font").measure(text) if text else 0

        def line_width(title_text, name_text):
            total = piece(self._h_title, title_text)
            total += piece(self._h_author, f"  ·  {bits['author']}" if bits["author"] else "")
            total += piece(self._h_dur, f"  ·  {bits['duration']}" if bits["duration"] else "")
            total += piece(self._h_source, f"  ·  {bits['source']}" if bits["source"] else "")
            total += piece(self._h_file, f"  ·  {name_text}" if name_text else "")
            return total

        if line_width(title, name) > room and name:
            rest = line_width(title, "")
            name = _elide(name, self._h_file.cget("font"),
                          max(room - rest - piece(self._h_file, "  ·  "), 48))
        if line_width(title, name) > room:
            rest = line_width("", name)
            title = _elide(title, self._h_title.cget("font"), max(room - rest, 80))
        def _set(label, text):
            if str(label.cget("text") or "") != text:
                label.configure(text=text)

        _set(self._h_title, title)
        _set(self._h_author, f"  ·  {bits['author']}" if bits["author"] else "")
        _set(self._h_dur, f"  ·  {bits['duration']}" if bits["duration"] else "")
        _set(self._h_source, f"  ·  {bits['source']}" if bits["source"] else "")
        _set(self._h_file, f"  ·  {name}" if name else "")
        shown = [title]
        if bits["author"]:
            shown.append(bits["author"])
        if bits["duration"]:
            shown.append(bits["duration"])
        if bits["source"]:
            shown.append(bits["source"])
        if name:
            shown.append(name)
        self.book_var.set("  ·  ".join(shown))

    def _show_path_tip(self, _event=None):
        self._hide_tip()
        tip_text = getattr(self, "_header_paths", "") or self.audio
        if not tip_text:
            return
        x = self.winfo_rootx() + 8
        y = self.winfo_rooty() + self.winfo_height() + 2
        self.head_tooltip = tk.Toplevel(self)
        self.head_tooltip.wm_overrideredirect(True)
        self.head_tooltip.wm_geometry(f"+{x}+{y}")
        self.head_tooltip.configure(background=palette.BORDER)
        ttk.Label(self.head_tooltip, text=tip_text, style="Tip.TLabel",
                  font="TkDefaultFont", justify="left",
                  takefocus=0).pack(padx=1, pady=1)

    def _fit_match_scroll(self, _event=None, frame=None):
        """Hide a scrollbar when its content fits."""
        if frame is None or not hasattr(frame, "_scrollbar"):
            frame = getattr(self, "_match_scroll", None)
        if frame is None:
            return
        bar = getattr(frame, "_scrollbar", None)
        canvas = getattr(frame, "_parent_canvas", None)
        if bar is None or canvas is None:
            return
        bbox = canvas.bbox("all")
        height = canvas.winfo_height()
        if height < 2:
            return
        fits = not bbox or bbox[3] <= height + 1
        shown = bool(bar.winfo_ismapped())
        if fits and shown:
            bar.grid_remove()
        elif not fits and not shown:
            bar.grid()

    def _hide_tip(self, _event=None):
        if getattr(self, "head_tooltip", None) is not None:
            self.head_tooltip.destroy()
            self.head_tooltip = None

    def _meta_overrides(self) -> dict:
        """Book info fields -> emit flag values (empty = keep original)."""
        return {"title": self.meta_vars["title"].get().strip(),
                "artist": self.meta_vars["author"].get().strip(),
                "album": self.meta_vars["album"].get().strip(),
                "date": self.meta_vars["year"].get().strip(),
                "genre": self.meta_vars["genre"].get().strip(),
                "comment": self.meta_vars["comment"].get().strip()}

    REFRESH_DEBOUNCE_MS = 250

    def _refresh_soon(self):
        """Spinbox arrows and typing fire a write per step: redraw once
        they pause."""
        if self._refresh_after is not None:
            self.after_cancel(self._refresh_after)
        self._refresh_after = self.after(self.REFRESH_DEBOUNCE_MS,
                                         self._debounced_refresh)

    def _debounced_refresh(self):
        self._refresh_after = None
        self._refresh()

    def flush_refresh(self):
        """Run a pending debounced refresh now."""
        if self._refresh_after is not None:
            self.after_cancel(self._refresh_after)
            self._debounced_refresh()

    def _refresh(self):
        """Read the analysis artifact and update every view. Never runs a
        subprocess: disk facts come from the MediaFactsLoader."""
        self.analysis = {}
        if self.audio:
            self.analysis = load_analysis(self.audio, self.work_dir)
        session = self.app.session
        session.scan = self.analysis or None
        from .projection import audio_duration
        session.duration = float(audio_duration(self.analysis) or 0.0)
        bar = getattr(self.app, "player_bar", None)
        if bar is not None:
            bar.update_time(None)
        session.chapters = list((self.analysis or {}).get("chapters") or [])
        session.emit("scan")
        self._apply_book_info()

        proj = WorkflowProjection(
            self.analysis,
            self.min_conf.get() if hasattr(self, "min_conf") else 0.5,
            self._disk or DiskFacts(),
            audio=self.audio,
            nfo_chapters=(self.harvest or {}).get("nfo_chapters") or [])
        self._projection = proj
        sel = self.tree.selection()
        scroll = self.tree.yview()
        for i in self.tree.get_children():
            self.tree.delete(i)
        self._conflicts_unknown = proj.conflicts_unknown
        self._row_data = {}
        open_indexes = getattr(self, "_open_indexes", set())
        opened = False
        for r in proj.rows:
            if not self._row_visible(r):
                continue
            iid = self.tree.insert("", "end", tags=r.tags, values=(
                r.mark, r.title, hms(r.published), _t(r.placed),
                r.gap,
                "" if r.confidence is None else f"{r.confidence:.2f}",
                r.pin, self._heard_of(r), self._status_of(r)))
            self._row_data[iid] = r.source
            problem = bool(set(r.tags) & {"conflict", "unplaced", "lowconf"})
            if problem and r.index in open_indexes:
                self._fill_problem_bar(r)
                opened = True
            elif problem and not open_indexes and not getattr(self, "_did_auto_open", False):
                self._open_indexes = {r.index}
                self._did_auto_open = True
                self._fill_problem_bar(r)
                opened = True
        if not opened:
            self._clear_problem_bar()
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])
        self.tree.yview_moveto(scroll[0] if scroll else 0)

        self.conflict_note.set(
            "pins from an older Align: re-run Align to detect conflicting "
            "pins" if proj.conflicts_unknown else "")
        if proj.conflicts_unknown:
            self.conflict_label.grid(row=4, column=0, columnspan=2,
                                     sticky="w")
        else:
            self.conflict_label.grid_remove()
        self.chapters_var.set(
            f"Chapters {proj.confident_count}/{len(proj.rows)}")

        states = proj.states
        self._sync_rail(states)
        self._update_panel_results(states)
        self._update_resolve()
        self._update_header()
        self.timeline.redraw()
        self._update_finish_cards()
        self._fill_asin_list()
        self._states = states
        self._update_controls()

    def set_busy(self, working: bool):
        """Called when a job starts or ends: lock conflicting controls."""
        if working != self._busy:
            self._busy = working
            self._update_controls()

    def _job_controls(self) -> list:
        """Everything that starts a job or edits what the next job reads."""
        controls = [self.measure_btn, self.identify_btn, self.identify_entry,
                    self.identify_browse, *self.identify_radios, self.align_btn,
                    self.min_conf_spin,
                    self.hint_btn, self.listen_btn, self.sweep_start_entry,
                    self.sweep_end_entry, self.range_btn, self.probe_btn,
                    self.full_btn, self.reread_btn,
                    self.finalize_btn, self.apply_btn,
                    self.asin_search_btn]
        if hasattr(self, "identify_found_btn"):
            controls.append(self.identify_found_btn)
        if hasattr(self, "use_edition_btn"):
            controls.append(self.use_edition_btn)
        if hasattr(self, "use_anyway_btn"):
            controls.append(self.use_anyway_btn)
        return controls

    def _update_controls(self):
        """One place decides what is enabled, and tooltips say why not."""
        states = getattr(self, "_states", None) or self._compute_states()
        reasons: dict = {}

        def off(widgets, why):
            for w in widgets:
                reasons.setdefault(w, why)

        if self._busy:
            off(self._job_controls(), "a job is running - wait or Cancel job")
        if not self.audio:
            off(self._job_controls(), "select a book in the library first")
        stage_btns = {"align": [self.align_btn],
                      "resolve": [self.hint_btn, self.listen_btn,
                                  self.range_btn, self.probe_btn,
                                  self.full_btn]}
        for key, btns in stage_btns.items():
            if states[key]["state"] == BLOCKED:
                off(btns, f"{STAGE_LABELS[key]} is blocked: "
                          f"{states[key]['why']}")
        if whisper_python() is None:
            off([self.listen_btn, self.range_btn, self.probe_btn,
                 self.full_btn], "no Whisper interpreter found "
                                 "(set WHISPER_PYTHON)")
        if not self._finalize_dry_ok:
            off([self.apply_btn], "Apply needs a dry run of these settings")
            if hasattr(self, "apply_hint"):
                self.apply_hint.set("Apply needs a dry run of these settings")
        elif hasattr(self, "apply_hint"):
            self.apply_hint.set("")
        if hasattr(self, "bitrate_combo"):
            if self.include_cleanup.get():
                self.bitrate_combo.configure(state="readonly")
            else:
                self.bitrate_combo.configure(state="disabled")
        mode = self.identify_mode.get()
        if mode in ("emb", "nfo", "search"):
            off([self.identify_entry],
                "this mode does not use the ASIN / path field")
        if mode != "json":
            off([self.identify_browse], "Browse picks a chapters JSON file")
        if mode != "search":
            off([self.asin_search_btn], "Search is for the Search catalog mode")
        for w in self._job_controls():
            why = reasons.get(w)
            set_enabled(w, not why)
            tip = self._tips.get(w)
            if tip is not None:
                tip.reason = why
        self._update_verdict_actions()

    def _header_fix(self) -> str | None:
        """The ffmpeg command that rewrites a wrong Xing header."""
        proj = getattr(self, "_projection", None)
        if proj is None:
            proj = WorkflowProjection(
                self.analysis,
                self.min_conf.get() if hasattr(self, "min_conf") else 0.5,
                self._disk or DiskFacts(), audio=self.audio)
        return proj.header_fix

    def _update_panel_results(self, states: dict):
        proj = getattr(self, "_projection", None)
        if proj is None or proj.analysis is not self.analysis:
            proj = WorkflowProjection(
                self.analysis,
                self.min_conf.get() if hasattr(self, "min_conf") else 0.5,
                self._disk or DiskFacts(), audio=self.audio)
            self._projection = proj
        self.measure_result.set(proj.measure_text)
        if proj.header_fix:
            self.copy_fix_btn.grid()
        else:
            self.copy_fix_btn.grid_remove()
        if hasattr(self, "identify_result"):
            self.identify_result.set(proj.identify_text)
        self.align_result.set(proj.align_text)
        self.emit_result.set(proj.emit_text)

    def _update_resolve(self):
        """Summary line, per-option statuses, and the recommendation."""
        proj = getattr(self, "_projection", None)
        if proj is None:
            proj = WorkflowProjection(
                self.analysis, self.min_conf.get(),
                self._disk or DiskFacts(), audio=self.audio)
        weak = proj.weak
        dur = (self.analysis.get("envelope") or {}).get("duration") or 0.0

        # summary line with clickable chapter numbers
        for w in self.unresolved_frame.winfo_children():
            w.destroy()
        if weak:
            from .ui_kit import BODY, font
            ctk.CTkLabel(self.unresolved_frame,
                         text=f"{len(weak)} chapters unresolved:").pack(
                side="left")
            for r in weak:
                n = r["index"] + 1
                lbl = ctk.CTkLabel(
                    self.unresolved_frame, text=str(n),
                    text_color=palette.LINK, cursor="hand2",
                    font=font(BODY, underline=True))
                lbl.pack(side="left", padx=2)
                lbl.bind("<Button-1>",
                         lambda e, r=r: self._select_unresolved(r))
        else:
            ctk.CTkLabel(self.unresolved_frame,
                         text="nothing unresolved - spot-check by ear").pack(
                side="left")
        accent = proj.recommendation
        self.next_var.set(f"next: {accent}")

        # statuses from the artifact
        ev = resolve_evidence(self.analysis)
        self.listen_status.set(
            f"heard {ev['listen_heard']}" if ev["listen_heard"]
            else "heard nothing" if ev["listen_ran"] else "not run")
        self.swept_status.set(
            f"{ev['swept_minutes']:.0f} min swept"
            + (f", heard {ev['sweep_heard']}" if ev["sweep_heard"] else "")
            if ev["sweep_spans"] else "not run")
        self.probe_status.set("run" if ev["sweep_spans"] else "not run")
        self.hint_status.set(f"hint: {self.near_var.get()}"
                             if self.near_var.get() else "")

        try:
            s = parse_time(self.sweep_start.get())
            e = parse_time(self.sweep_end.get())
            self.range_cost.set(f"~{(e - s) / 60:.0f} min" if e > s else "")
        except ValueError:
            self.range_cost.set("")
        self.full_cost.set(f"~{dur / 600:.0f} min" if dur else "")

        # exactly one accent button
        chosen = {"Listen": self.listen_btn, "Sweep probe": self.probe_btn,
                  "Sweep range": self.range_btn, "Full sweep": self.full_btn}
        for name, btn in chosen.items():
            btn.configure(**accent_colors(name == accent))

    def _select_unresolved(self, r):
        for iid, data in self._row_data.items():
            if data.get("index") == r["index"]:
                self.tree.see(iid)
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                break

    # ------------------------------------------------------- command plumbing
    def _argv(self, *extra: str, prefix: list[str] | None = None) -> list[str]:
        assert self.audio is not None
        return job_argv(prefix or self._cf, self.work_dir, *extra,
                        audio=self.audio)

    def _run(self, name: str, argv: list[str], then=None, stage: str | None = None,
             on_line=None):
        if self.app.runner.busy:
            self.app.log(f"! {name} not started: another job is still running")
            return
        self.app.log(f"$ {' '.join(argv)}")
        self.app.session.set_job(
            True, name, opening_sentence(name, stage), job_is_modal(name, stage))
        self.set_busy(True)
        self.app.runner.run(Job(
            name, argv, on_line=on_line, env=package_env(),
            on_done=lambda j, code: self._on_done(
                code, then=then, stage=stage, name=j.name,
                cancelled=getattr(j, "cancelled", False))))

    def _on_done(self, code, then=None, stage: str | None = None,
                 name: str = "the last step", cancelled: bool = False):
        self.set_busy(False)
        if hasattr(self.app, "_apply_search"):
            self.app._apply_search()
        if code is None:
            self.app.log(f"! {name} could not start (see the line above)")
        else:
            self.app.log(f"exit {code}")
        self._refresh()
        self._request_facts()
        if cancelled:
            self.app.log(f"! {name} cancelled")
            return
        if code != 0:
            why = "could not start" if code is None else f"exited with {code}"
            messagebox.showwarning(
                "chapterfit", f"{name} {why} - see the Log for details",
                parent=self)
            return
        if then:
            then()
            return
        if stage == "finalize-dry":
            self._remember_dry_run()
            self._update_controls()
            return
        if stage == "identify":
            if self.section_files:
                # LibriVox: edition is the section files; pin and place next.
                self._maybe_pin_section_starts()
                return
            # Stay on Match so an edition can be chosen. Use this edition
            # is what opens Place. A later facts refresh must not jump either.
            self._auto_stage = False
            return
        if stage:
            target = next_stage(self._compute_states())
            if target != self._stage:
                self.set_stage(target)
            self._auto_stage = True  # again once fresh disk facts land
        if stage == "measure" and code == 0:
            self._write_playlist_sig()
            self._maybe_identify_from_nfo()
            if self._rescan_quality:
                self._rescan_quality = False
                self._scan_quality()

    def _write_playlist_sig(self) -> None:
        """Remember the section playlist so autoscan skips when unchanged."""
        if not self.section_files or not self.work_dir or not self.audio:
            return
        from .librivox_book import playlist_sig
        path = analysis_path(self.audio, self.work_dir)
        try:
            with open(path) as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        data["playlist_sig"] = list(playlist_sig(self.section_files))
        try:
            with open(path, "w") as handle:
                json.dump(data, handle, separators=(",", ":"))
        except OSError:
            return
        self.analysis = data

    def _maybe_pin_section_starts(self) -> None:
        """Pin every chapter at its file start, then align (LibriVox)."""
        if self._librivox_pins_done or not self.section_starts:
            return
        from .librivox_book import pin_specs
        specs = pin_specs(self.section_starts)
        if not specs:
            return
        self._librivox_pins_done = True
        argv = [*self._argv("pin")]
        for spec in specs:
            argv += ["--at", spec]
        self._run("pin", argv, then=self.cmd_align, stage="resolve")

    # ------------------------------------------------------------- actions --
    def cmd_measure(self):
        self._run("measure", measure_argv(
            self._cf, self.work_dir, self.audio,
            concat=self._concat_path), stage="measure")

    def cmd_rescan(self):
        """Measure again, then refresh the loudness tiles from that file."""
        if not self.audio:
            return
        self._rescan_quality = True
        self._run("measure", measure_argv(
            self._cf, self.work_dir, self.audio,
            concat=self._concat_path, redo="envelope"), stage="measure")

    def _large_audio(self) -> bool:
        try:
            return bool(self.audio) and os.path.getsize(self.audio) >= 4096
        except OSError:
            return False

    def _measure_current(self) -> bool:
        env = (self.analysis or {}).get("envelope") or {}
        if not env.get("duration"):
            return False
        if self.section_files:
            from .librivox_book import playlist_sig
            stored = self.analysis.get("playlist_sig")
            return stored == list(playlist_sig(self.section_files))
        stored = (self.analysis.get("file") or {}).get("size")
        try:
            return stored == os.path.getsize(self.audio or "")
        except OSError:
            return False

    def _quality_current(self) -> bool:
        quality = getattr(self, "quality", None)
        report = getattr(quality, "report", None) or {}
        if not report.get("chunks") and report.get("integrated_lufs") is None:
            return False
        size = report.get("source_size")
        if size is None:
            return True
        try:
            return size == os.path.getsize(self.audio or "")
        except OSError:
            return False

    def _maybe_autoscan(self) -> None:
        """Measure when this file size has no envelope. The mtime-only
        rule stays in the artifact: a tag rewrite does not count as new."""
        if not self._large_audio() or self.app.runner.busy:
            return
        if not self._measure_current():
            self._rescan_quality = not self._quality_current()
            self.cmd_measure()
            return
        if not self._quality_current():
            self._scan_quality()

    def _scan_quality(self) -> None:
        quality = getattr(self, "quality", None)
        if quality is None or not getattr(quality, "available", False):
            return
        quality.cmd_scan()

    # identify ----------------------------------------------------------
    def _identify_mode_changed(self):
        mode = self.identify_mode.get()
        if hasattr(self, "apply_btn"):
            self._update_controls()
        if mode == "json" and not self.identify_value.get():
            self.identify_value.set(os.path.join(
                os.path.dirname(self.audio or ".") or ".", "chapters.json"))
        if hasattr(self, "asin_frame"):
            frames = {
                "search": getattr(self, "asin_frame", None),
                "asin": getattr(self, "_identify_entry_frame", None),
                "json": getattr(self, "_identify_entry_frame", None),
                "emb": getattr(self, "_identify_found_frame", None),
                "nfo": getattr(self, "_identify_found_frame", None),
            }
            for frame in {f for f in frames.values() if f is not None}:
                frame.grid_remove()
            shown = frames.get(mode)
            if shown is not None:
                shown.grid(row=0, column=0, sticky="ew")
            if mode == "json":
                self.identify_browse.grid()
            elif hasattr(self, "identify_browse"):
                self.identify_browse.grid_remove()
            if mode in ("asin", "json") and hasattr(self, "identify_btn"):
                self.identify_btn.grid()
            self._refresh_identify_found_line()

    def _refresh_identify_found_line(self):
        if not hasattr(self, "identify_found_var"):
            return
        mode = self.identify_mode.get()
        harvest = self.harvest or {}
        if mode == "emb":
            n = len(harvest.get("embedded_chapters") or [])
            self.identify_found_var.set(
                f"{n} chapters in the file's tags" if n
                else "No embedded chapters in this file")
        elif mode == "nfo":
            n = len(harvest.get("nfo_chapters") or [])
            self.identify_found_var.set(
                f"{n} chapters in album.nfo" if n
                else "No chapter tracks in album.nfo")
        else:
            self.identify_found_var.set("")

    def _identify_browse(self):
        path = filedialog.askopenfilename(
            parent=self, title="Chapters JSON",
            filetypes=[("JSON", "*.json"), ("All files", "*")])
        if path:
            self.identify_value.set(path)

    def _same_source_as_last(self) -> bool:
        mode = self.identify_mode.get()
        if mode == "search":
            mode = "asin"
        return same_source(
            mode,
            self.identify_value.get().strip(),
            (self.analysis.get("identification") or {}).get("label") or "")

    def _fill_asin_query(self, *, keep_keywords: bool = False,
                         prefer_tags: bool = True):
        """Show the title and author that Search will send."""
        if not hasattr(self, "asin_title"):
            return
        query = asin_query(self.audio or "", self.work_dir, prefer_tags=prefer_tags)
        self.asin_title.set(query["title"])
        self.asin_author.set(query["author"])
        if not keep_keywords:
            self.asin_keywords.set("")

    def _in_use_asin(self) -> str:
        label = (self.analysis.get("identification") or {}).get("label") or ""
        if label.startswith("Audnexus "):
            return label.split(" ", 1)[1].strip()
        return ""

    def _edition_fit_map(self) -> dict[str, dict]:
        fits = dict(load_fit_cache(self.audio or "", self.work_dir))
        checking = getattr(self, "_fit_checking", set()) or set()
        for asin in checking:
            fits.setdefault(asin, {})
        ident = self.analysis.get("identification") or {}
        in_use = self._in_use_asin()
        if in_use and ident:
            dur = (self.analysis.get("envelope") or {}).get("duration")
            fits[in_use] = fit_from_identification(ident, file_runtime=dur)
        return fits

    def _fill_asin_list(self):
        if not hasattr(self, "asin_tree"):
            return
        counts = _chapter_counts(self.audio or "", self.work_dir)
        fits = self._edition_fit_map()
        checking = getattr(self, "_fit_checking", set()) or set()
        in_use = self._in_use_asin()
        rows = []
        for row in asin_candidates(self.audio or "", self.work_dir):
            asin = row["asin"]
            fit = fits.get(asin)
            ch = ""
            if fit and fit.get("chapter_count"):
                ch = str(fit["chapter_count"])
            elif counts.get(asin):
                ch = counts[asin]
            runtime_min = None
            raw = row.get("runtime") or ""
            if isinstance(raw, str) and raw.endswith(" min"):
                with contextlib.suppress(ValueError):
                    runtime_min = float(raw[:-4].strip())
            rows.append({
                **row,
                "runtime_min": runtime_min,
                "ch": ch,
                "fit": fit if fit else None,
                "checking": asin in checking and not (fit and fit.get("verdict")),
            })
        rows.sort(key=fit_sort_key)
        sources = {r.get("source") or "find-asin" for r in rows}
        group = len(sources) > 1
        self._filling_editions = True
        self.asin_tree.delete(*self.asin_tree.get_children())
        self._edition_tooltips = {}
        last_source = None
        for row in rows:
            source = row.get("source") or "find-asin"
            if group and source != last_source:
                last_source = source
                self.asin_tree.insert(
                    "", "end", values=("", source_group_label(source),
                                       "", "", "", "", ""),
                    tags=("divider",))
            marker = "●" if row["asin"] == in_use else ""
            title = row["title"]
            narrator = row["narrator"]
            title_shown = title if len(title) <= 48 else title[:47] + "…"
            narr_shown = (narrator if len(narrator) <= 28
                          else narrator[:27] + "…")
            tags = ("in_use",) if row["asin"] == in_use else ()
            iid = self.asin_tree.insert("", "end", values=(
                marker, row["asin"], title_shown, narr_shown,
                format_length(row.get("runtime_min")),
                row["ch"],
                fit_cell(row.get("fit"), checking=row["checking"])),
                tags=tags)
            self._edition_tooltips[iid] = {
                "title": title, "narrator": narrator}
        if hasattr(self, "editions_count_var"):
            self.editions_count_var.set(f"{len(rows)}" if rows else "")
        self._filling_editions = False
        self._show_edition_marks()
        self._update_verdict_card()
        self._update_book_conflicts()

    def _fit_label(self, asin: str) -> str:
        return fit_cell(self._edition_fit_map().get(asin))

    def _show_edition_marks(self) -> None:
        chapters = (self.analysis.get("identification") or {}).get("chapters") or []
        marks = [float(c["start"]) for c in chapters
                 if isinstance(c, dict) and isinstance(c.get("start"), (int, float))]
        self.app.session.preview_marks = marks
        if hasattr(self, "timeline"):
            self.timeline.redraw()
        if hasattr(self, "fit_var"):
            self.fit_var.set("")

    def _selected_edition_asin(self) -> str:
        if not hasattr(self, "asin_tree"):
            return ""
        sel = self.asin_tree.selection()
        if not sel:
            return ""
        values = self.asin_tree.item(sel[0], "values")
        if not values or len(values) < 2:
            return ""
        asin = str(values[1]).strip()
        if asin.startswith("From "):
            return ""
        return asin

    def _update_verdict_card(self) -> None:
        if not hasattr(self, "verdict_headline_var"):
            return
        asin = self._selected_edition_asin() or self._in_use_asin()
        fits = self._edition_fit_map()
        checking = asin in (getattr(self, "_fit_checking", set()) or set())
        fit = fits.get(asin) if asin else None
        from chaptertools.gui.ui_kit import C
        if checking and not (fit and fit.get("verdict")):
            self.verdict_headline_var.set("Checking this edition…")
            self.verdict_headline.configure(text_color=C["muted"])
            self.verdict_reason_var.set("")
            self.verdict_numbers_var.set("")
            self._update_verdict_actions()
            return
        if not fit or not fit.get("verdict"):
            self.verdict_headline_var.set(
                "Select an edition" if asin_candidates(
                    self.audio or "", self.work_dir) else "")
            self.verdict_headline.configure(text_color=C["muted"])
            self.verdict_reason_var.set("")
            self.verdict_numbers_var.set("")
            self._update_verdict_actions()
            return
        verdict = fit.get("verdict")
        self.verdict_headline_var.set(verdict_headline(verdict))
        color = C["ok"] if verdict == "consistent" else C["bad_fg"]
        self.verdict_headline.configure(text_color=color)
        ident = self.analysis.get("identification") or {}
        if self._in_use_asin() == asin and ident:
            self.verdict_reason_var.set(reason_sentence(ident))
            self.verdict_numbers_var.set(numbers_line(ident))
        else:
            self.verdict_reason_var.set(reason_sentence(fit))
            bits = []
            if fit.get("chapter_count"):
                bits.append(f"{fit['chapter_count']} chapters")
            if fit.get("score") is not None:
                bits.append(f"fit {float(fit['score']):.1f}")
            if fit.get("rate_spread") is not None:
                bits.append(
                    f"rate spread {float(fit['rate_spread']) * 100:.1f} %")
            if fit.get("duration_ratio") is not None:
                bits.append(
                    f"runtime ratio {float(fit['duration_ratio']):.2f}")
            self.verdict_numbers_var.set(" · ".join(bits))
        self._update_verdict_actions()

    def _update_verdict_actions(self) -> None:
        if not hasattr(self, "use_edition_btn"):
            return
        asin = self._selected_edition_asin() or self._in_use_asin()
        fit = self._edition_fit_map().get(asin) if asin else None
        verdict = (fit or {}).get("verdict")
        busy = getattr(self, "_busy", False)
        if verdict == "consistent" and not busy:
            self.use_edition_btn.pack(side="left")
            set_enabled(self.use_edition_btn, True)
            self.use_anyway_btn.pack_forget()
        elif verdict in ("rejected", "weak") and not busy:
            self.use_edition_btn.pack_forget()
            self.use_anyway_btn.pack(side="left")
            set_enabled(self.use_anyway_btn, True)
        else:
            self.use_edition_btn.pack(side="left")
            set_enabled(self.use_edition_btn, False)
            self.use_anyway_btn.pack_forget()

    def _update_book_conflicts(self) -> None:
        if not hasattr(self, "_meta_conflict"):
            return
        asin = self._selected_edition_asin() or self._in_use_asin()
        defaults = asin_emit_defaults(
            self.audio or "", self.work_dir,
            f"Audnexus {asin}") if asin else {}
        if asin:
            for row in asin_candidates(self.audio or "", self.work_dir):
                if row["asin"] == asin and row.get("narrator"):
                    defaults["narrator"] = row["narrator"]
                    break
        for key, widgets in self._meta_conflict.items():
            edition_val = (defaults.get(key) or "").strip()
            current = (self.meta_vars.get(key).get() if self.meta_vars.get(key)
                       else "").strip()
            frame = widgets["frame"]
            if edition_val and edition_val != current:
                widgets["note"].configure(
                    text=f'Edition says "{edition_val}"')
                widgets["use"].configure(
                    command=lambda k=key, v=edition_val: self.meta_vars[k].set(v))
                frame.grid()
            else:
                frame.grid_remove()

    def _on_asin_select(self, _event=None):
        if getattr(self, "_filling_editions", False):
            return
        asin = self._selected_edition_asin()
        if not asin:
            self._update_verdict_card()
            return
        self.identify_value.set(asin)
        self._identify_autofilled = True
        self._update_verdict_card()
        self._update_book_conflicts()
        label = (self.analysis.get("identification") or {}).get("label") or ""
        if label.endswith(asin):
            self._show_edition_marks()
            return
        self.cmd_identify_asin(asin)

    def cmd_use_edition(self):
        """Fill empty book-info fields, align, and open Place."""
        self._use_edition(confirm=False)

    def cmd_use_edition_anyway(self):
        if not messagebox.askyesno(
                "Use anyway?",
                "This edition does not fit the audio. Use it anyway?",
                parent=self):
            return
        self._use_edition(confirm=True)

    def _use_edition(self, *, confirm: bool):
        del confirm
        asin = self._selected_edition_asin()
        if not asin:
            return
        defaults = asin_emit_defaults(
            self.audio or "", self.work_dir, f"Audnexus {asin}")
        for row in asin_candidates(self.audio or "", self.work_dir):
            if row["asin"] == asin and row.get("narrator"):
                defaults.setdefault("narrator", row["narrator"])
                break
        filled = []
        for key, value in defaults.items():
            var = self.meta_vars.get(key)
            if var is None or not value or var.get().strip():
                continue
            var.set(value)
            filled.append(key)
        if filled:
            self.found_var.set(
                plain_found_summary(self.harvest)
                + " · filled: " + ", ".join(filled))
        self.show_step("place")
        label = (self.analysis.get("identification") or {}).get("label") or ""
        if not label.endswith(asin):
            self._run("identify", identify_argv(
                self._cf, self.work_dir, self.audio, "asin", asin),
                stage="identify", then=self.cmd_align)
            return
        self.cmd_align()

    def cmd_search_asin(self):
        if not self.audio or not self.work_dir:
            return
        os.makedirs(self.work_dir, exist_ok=True)
        out = asin_result_path(self.audio, self.work_dir)
        provider = "find-asin"
        if hasattr(self, "asin_provider"):
            provider = self.asin_provider.get() or "find-asin"
        self._run("find-asin", find_asin_argv(
            self.audio, out,
            title=self.asin_title.get(),
            author=self.asin_author.get(),
            keywords=self.asin_keywords.get(),
            region=self.asin_region.get(),
            provider=provider),
            then=self._after_asin_search)

    def _after_asin_search(self):
        self._fill_asin_query(keep_keywords=True, prefer_tags=False)
        self._fill_asin_list()
        rows = asin_candidates(self.audio or "", self.work_dir)
        if not rows:
            return
        if not self.identify_value.get().strip() \
                or getattr(self, "_identify_autofilled", False):
            self.identify_value.set(rows[0]["asin"])
            self._identify_autofilled = True
        self._start_fit_queue([r["asin"] for r in rows])

    def _start_fit_queue(self, asins: list[str]) -> None:
        """Queue sequential identify probes; write to edition_fits cache."""
        if not self.audio or not self.work_dir or not asins:
            return
        # Identify needs measure gaps; skip until Scan has run.
        if not (self.analysis.get("gaps") or {}).get("gaps"):
            self._fit_queue = list(asins)
            self._fit_checking = set(asins)
            self._fill_asin_list()
            self.after(800, self._pump_fit_queue)
            return
        cached = load_fit_cache(self.audio, self.work_dir)
        pending = [a for a in asins if a not in cached]
        self._fit_queue = pending
        self._fit_checking = set(pending)
        self._fill_asin_list()
        self._pump_fit_queue()

    def _pump_fit_queue(self) -> None:
        if getattr(self, "_fit_running", False):
            return
        queue_list = getattr(self, "_fit_queue", None) or []
        if not queue_list:
            self._fit_checking = set()
            self._fill_asin_list()
            return
        if not (self.analysis.get("gaps") or {}).get("gaps"):
            self.after(800, self._pump_fit_queue)
            return
        if self.app.runner.busy:
            self.after(400, self._pump_fit_queue)
            return
        cached = load_fit_cache(self.audio or "", self.work_dir)
        while queue_list and queue_list[0] in cached:
            done = queue_list.pop(0)
            checking = getattr(self, "_fit_checking", set()) or set()
            checking.discard(done)
            self._fit_checking = checking
        self._fit_queue = queue_list
        if not queue_list:
            self._fill_asin_list()
            return
        asin = queue_list[0]
        self._fit_running = True
        self._fit_current = asin

        def after_probe():
            self._fit_running = False
            ident = self.analysis.get("identification") or {}
            label = ident.get("label") or ""
            if label.endswith(asin):
                dur = (self.analysis.get("envelope") or {}).get("duration")
                fits = load_fit_cache(self.audio, self.work_dir)
                fits[asin] = fit_from_identification(ident, file_runtime=dur)
                save_fit_cache(self.audio, self.work_dir, fits)
            checking = getattr(self, "_fit_checking", set()) or set()
            checking.discard(asin)
            self._fit_checking = checking
            q = getattr(self, "_fit_queue", None) or []
            if q and q[0] == asin:
                q = q[1:]
            self._fit_queue = q
            self._fill_asin_list()
            self._pump_fit_queue()

        self._run("fit-probe", identify_argv(
            self._cf, self.work_dir, self.audio, "asin", asin),
            stage="identify", then=after_probe)

    def cmd_identify_primary(self):
        mode = self.identify_mode.get()
        if mode == "search":
            self.cmd_search_asin()
            return
        verdict = identify_verdict(self.analysis.get("identification") or {})
        if verdict == "rejected" and self._same_source_as_last():
            self.identify_entry.focus_set()
            self.identify_entry.select_range(0, "end")
            self.app.log("enter a different ASIN or chapters JSON file, "
                         "then Check")
            return
        if mode == "asin":
            asin = self.identify_value.get().strip()
            if asin:
                self.cmd_identify_asin(asin)
            else:
                self.identify_entry.focus_set()
                self.app.log("enter an Audible ASIN to identify from")
        elif mode == "json":
            path = self.identify_value.get().strip()
            if path and os.path.isfile(path):
                self.cmd_identify_json(path)
            else:
                messagebox.showinfo("Identify", "choose an existing JSON file",
                                    parent=self)
        elif mode == "nfo":
            self.cmd_identify_nfo()
        else:
            self.cmd_identify_embedded()

    def cmd_identify_asin(self, asin=None):
        asin = asin or simpledialog.askstring("Identify", "Audible ASIN:",
                                              parent=self)
        if asin:
            self._run("identify", identify_argv(
                self._cf, self.work_dir, self.audio, "asin", asin.strip()),
                      stage="identify")

    def cmd_identify_json(self, path=None):
        path = path or filedialog.askopenfilename(
            parent=self, title="Chapters JSON",
            filetypes=[("JSON", "*.json"), ("All files", "*")])
        if path:
            self._run("identify", identify_argv(
                self._cf, self.work_dir, self.audio, "json", path),
                      stage="identify")

    def cmd_identify_embedded(self):
        """Use the chapters already embedded in the file as the source."""
        if self.harvest.get("loading"):
            self.app.log("still reading the file's chapters - try again in "
                         "a moment")
            return
        chapters = self.harvest.get("embedded_chapters") or []
        if len(chapters) < 2:
            messagebox.showinfo("Identify (embedded)",
                                "the file has fewer than 2 embedded chapters",
                                parent=self)
            return
        assert self.work_dir is not None
        dump = os.path.join(self.work_dir, "chapters.embedded.json")
        os.makedirs(self.work_dir, exist_ok=True)
        with open(dump, "w") as f:
            json.dump({"chapters": [
                {"startOffsetMs": int(round(c["start"] * 1000)),
                 "title": c["title"]} for c in chapters]}, f, indent=1)
        self.app.log(f"  dumped {len(chapters)} embedded chapters -> {dump}")
        self._run("identify", identify_argv(
            self._cf, self.work_dir, self.audio, "json", dump),
                  stage="identify")

    def cmd_identify_nfo(self) -> bool:
        """Use album.nfo chapter tracks as the source. Returns whether a
        job was started."""
        if self.harvest.get("loading"):
            self.app.log("still reading album.nfo - try again in a moment")
            return False
        chapters = self.harvest.get("nfo_chapters") or []
        if len(chapters) < 2:
            messagebox.showinfo("Identify (NFO)",
                                "album.nfo has fewer than 2 chapter tracks",
                                parent=self)
            return False
        if self.app.runner.busy:
            return False
        assert self.work_dir is not None
        dump = os.path.join(self.work_dir, "chapters.nfo.json")
        os.makedirs(self.work_dir, exist_ok=True)
        with open(dump, "w") as f:
            json.dump({"chapters": [
                {"startOffsetMs": int(round(c["start"] * 1000)),
                 "title": c["title"]} for c in chapters]}, f, indent=1)
        self.app.log(f"  dumped {len(chapters)} NFO chapters -> {dump}")
        self._run("identify", identify_argv(
            self._cf, self.work_dir, self.audio, "json", dump),
                  stage="identify")
        return True

    def _maybe_identify_from_nfo(self):
        """Load NFO chapters once measure exists. Ask only when that would
        replace an identification already on the artifact."""
        if getattr(self, "_nfo_decided", False) or not self.audio:
            return
        if self.harvest.get("loading"):
            return
        chapters = self.harvest.get("nfo_chapters") or []
        if len(chapters) < 2:
            return
        if self._compute_states()["measure"]["state"] == "pending":
            return
        ident = (self.analysis.get("identification") or {}).get("chapters") or []
        if ident and not chapters_differ(ident, chapters):
            self._nfo_decided = True
            if self.section_files:
                self._maybe_pin_section_starts()
            return
        # LibriVox fetch writes album.nfo: take it without asking when
        # nothing is identified yet.
        if ident and not messagebox.askyesno(
                "album.nfo chapters",
                f"album.nfo has {len(chapters)} chapters that differ from "
                "the current identification.\n\nReplace the chapter list "
                "with the NFO tracks?",
                parent=self):
            self._nfo_decided = True
            return
        if self.cmd_identify_nfo():
            self._nfo_decided = True

    # align -------------------------------------------------------------
    def _near_spec(self, raw: str) -> str | None:
        return near_spec(raw, self.analysis)

    def _row_visible(self, row) -> bool:
        mode = self.chapter_filter.get() if hasattr(self, "chapter_filter") else "all"
        tags = set(row.tags)
        if mode == "problems":
            return bool(tags & {"conflict", "unplaced", "lowconf"})
        if mode == "lowconf":
            return "lowconf" in tags
        if mode == "unplaced":
            return "unplaced" in tags
        return True

    def _status_of(self, row) -> str:
        if "conflict" in row.tags:
            return "conflict"
        if "unplaced" in row.tags:
            return "unplaced"
        if "lowconf" in row.tags:
            return "low"
        return "placed" if row.placed is not None else ""

    # "low" fits the Status column; the amber row already says why.

    def _heard_of(self, row) -> str:
        import re
        pattern = self.grep_var.get() if hasattr(self, "grep_var") else ""
        try:
            rx = re.compile(pattern, re.I) if pattern else None
        except re.error:
            rx = None
        best = ""
        best_d = 45.0
        target = row.placed
        for pos in (self.analysis.get("heard") or {}).get("positions") or []:
            if not isinstance(pos, dict):
                continue
            text = str(pos.get("heading") or pos.get("text") or "")
            if not text or (rx and not rx.search(text)):
                continue
            moment = pos.get("time")
            if target is None or not isinstance(moment, (int, float)):
                best = best or text
                continue
            delta = abs(moment - target)
            if delta < best_d:
                best, best_d = text, delta
        return best

    def _problem_sentence(self, row) -> str:
        if "conflict" in row.tags:
            return "This pin disagrees with the published time."
        if "unplaced" in row.tags:
            return "Not placed yet."
        return "Below the minimum confidence, so it would be withheld."

    def _candidate_gaps(self, row) -> list[dict]:
        gaps = (self.analysis.get("gaps") or {}).get("gaps") or []
        anchor = row.placed if row.placed is not None else row.published
        if anchor is None:
            return []
        ranked = sorted(
            (g for g in gaps if isinstance(g, dict) and g.get("mid") is not None),
            key=lambda g: abs(g["mid"] - anchor))
        return ranked[:3]

    def _clear_problem_bar(self) -> None:
        bar = getattr(self, "problem_bar", None)
        if bar is None:
            return
        for child in bar.winfo_children():
            child.destroy()
        bar.grid_remove()

    def _fill_problem_bar(self, row) -> None:
        """One line under the table. Not a tree child: a child hid rows."""
        bar = self.problem_bar
        self._clear_problem_bar()
        chapter = row.index + 1
        ctk.CTkLabel(bar, text=self._problem_sentence(row), anchor="w").grid(
            row=0, column=0, sticky="w")
        col = 1
        menu = tk.Menu(bar, tearoff=0)
        anchor = row.placed if row.placed is not None else row.published
        if anchor is not None:
            menu.add_command(
                label="Play from 5 s before",
                command=lambda a=float(anchor): self._play_before(a))
        menu.add_command(
            label="Pin at playhead",
            command=lambda ch=chapter: self._pin_at(
                ch, self.app.session.playhead or 0.0))
        menu.add_command(
            label="Remove pin",
            command=lambda ch=chapter: self._remove_pin(ch))
        for gap in self._candidate_gaps(row)[:1]:
            mid = float(gap["mid"])
            dur = float(gap.get("dur") or 0)
            ctk.CTkLabel(bar, text=f"{clock(mid)} · {dur:.2f} s").grid(
                row=0, column=col, sticky="w", padx=(8, 2))
            col += 1
            ctk.CTkButton(
                bar, text="Use this", height=28, width=80,
                command=lambda m=mid, ch=chapter: self._pin_at(ch, m),
            ).grid(row=0, column=col, sticky="w")
            col += 1
        ttk.Menubutton(bar, text="More", menu=menu).grid(
            row=0, column=col, sticky="w", padx=(8, 0))
        self._problem_menu = menu
        bar.grid()

    def _play_before(self, seconds: float) -> None:
        if not self.audio:
            return
        self.app.play(self.audio, start=max(0.0, seconds - 5.0), length=0)

    def _pin_at(self, chapter: int, seconds: float) -> None:
        self.pin_ch.set(str(chapter))
        self.pin_time.set(clock(seconds))
        self.cmd_pin()

    def _remove_pin(self, chapter: int) -> None:
        self.pin_ch.set(str(chapter))
        self.cmd_unpin()

    def _toggle_open_row(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return "break"
        iid = sel[0]
        parent = self.tree.parent(iid)
        if parent:
            iid = parent
        row = self._row_data.get(iid) or {}
        index = row.get("index")
        if index is None:
            return "break"
        open_indexes = set(getattr(self, "_open_indexes", set()))
        if index in open_indexes:
            open_indexes.discard(index)
        else:
            open_indexes.add(index)
        self._open_indexes = open_indexes
        self._did_auto_open = True
        self._refresh()
        return "break"

    def _sync_hint_box(self) -> None:
        if not hasattr(self, "hints_box"):
            return
        lines = [line.strip() for line in
                 self.hints_box.get("1.0", "end").splitlines() if line.strip()]
        if not lines:
            return
        self.near_var.set(lines[0])
        self.hint_var.set(lines[0])

    def cmd_align(self):
        self._sync_hint_box()
        near = self.near_var.get().strip()
        spec = None
        if near:
            try:
                spec = self._near_spec(near)
            except ValueError as e:
                messagebox.showerror("Align", str(e), parent=self)
                return
            if spec:
                self.hint_status.set(f"hint: {spec}")
        self._run("align", align_argv(self._cf, self.work_dir, self.audio,
                                      self.min_conf.get(), spec),
                  stage="align")

    # resolve -----------------------------------------------------------
    def _resolve_hint(self):
        self._sync_hint_box()
        near = self.hint_var.get().strip()
        if not near:
            near = simpledialog.askstring(
                "Position hint",
                "roughly where a chapter starts: CH=TIME, or just a time",
                parent=self)
        if not near:
            return
        try:
            spec = self._near_spec(near)
        except ValueError as e:
            messagebox.showerror("Position hint", str(e), parent=self)
            return
        if spec:
            self.near_var.set(spec)
            self.cmd_align()

    def _sweep_probe(self):
        dur = ((self.analysis.get("envelope") or {}).get("duration"))
        if not dur:
            messagebox.showinfo("Sweep", "run Measure first", parent=self)
            return
        start = dur * 0.25
        self.cmd_sweep(start=start, end=start + 600)

    def apply_timeline_selection(self, span: tuple[float, float] | None) -> None:
        """The timeline selection is the sweep range, the learn span, and
        the preview start."""
        self.app.session.set_selection(span)
        if span and hasattr(self, "sweep_start"):
            start, end = span
            self.sweep_start.set(clock(start))
            self.sweep_end.set(clock(end))
            self._sweep_autofilled = True
        quality = getattr(self, "quality", None)
        if quality is not None and getattr(quality, "footer", None) is not None:
            if span:
                quality._on_range(span[0], span[1])
            else:
                quality._on_range(0.0, None)

    def _sweep_range(self):
        span = self.app.session.selection
        if span:
            s, e = span
        else:
            try:
                s = parse_time(self.sweep_start.get().strip() or "0")
                e = parse_time(self.sweep_end.get().strip() or "0")
            except ValueError:
                messagebox.showinfo("Sweep range",
                                    "range needs S–E times like 0:44:54",
                                    parent=self)
                return
        if e <= s:
            messagebox.showinfo("Sweep range", "end must be after start",
                                parent=self)
            return
        self.cmd_sweep(start=s, end=e)

    def _sweep_full(self):
        self.cmd_sweep()

    def cmd_sweep(self, start=None, end=None):
        wpy = whisper_python()
        if wpy is None:
            messagebox.showinfo("Sweep", "no whisper venv found (set "
                                "WHISPER_PYTHON)", parent=self)
            return
        self._run("sweep", whisper_argv(
            resolve_chapterfit(wpy), self.work_dir, self.audio, "sweep",
            model=os.environ.get("WHISPER_MODEL", "base"),
            start=start, end=end), stage="resolve", then=self.cmd_align)

    def cmd_listen(self):
        wpy = whisper_python()
        if wpy is None:
            messagebox.showinfo("Listen", "no whisper venv found (set "
                                "WHISPER_PYTHON)", parent=self)
            return
        self._run("listen", whisper_argv(
            resolve_chapterfit(wpy), self.work_dir, self.audio, "listen",
            model=os.environ.get("WHISPER_MODEL", "base")),
                  stage="resolve", then=self.cmd_align)

    # chapter row ---------------------------------------------------------
    def _selected_row(self):
        sel = self.tree.selection()
        if not sel:
            return None
        r = self._row_data.get(sel[0])
        if r is not None:
            return dict(r, _iid=sel[0])
        vals = self.tree.item(sel[0], "values")
        return {"index": int(str(vals[0]).lstrip("?")) - 1, "title": vals[1],
                "published": vals[2], "time": vals[3],
                "confidence": vals[5], "pinned": vals[6], "_iid": None}

    def preview_row(self):
        row = self._selected_row()
        if not row or not self.audio:
            return
        start = None
        for key in ("time", "published"):
            v = row.get(key)
            if v is not None and str(v) not in ("", "-"):
                start = max(0.0, parse_time(str(v)) - 3.0)
                break
        self.play_at_book_time(start or 0.0)

    def play_at_book_time(self, book_t: float) -> None:
        """Play from a book-relative time (maps across section files)."""
        if not self.audio:
            return
        if not self.section_files:
            self.app.play(self.audio, start=book_t, length=0)
            self.app.session.set_playhead(book_t)
            return
        from .librivox_book import book_to_file
        index, offset = book_to_file(
            book_t, self.section_starts, self.section_durations)
        index = max(0, min(index, len(self.section_files) - 1))
        self._section_index = index
        self.app.session.chapter_index = index
        self.app.play(self.section_files[index], start=offset, length=0)
        self.app.session.set_playhead(book_t)

    def book_time_from_file(self, file_t: float | None) -> float | None:
        """Convert the player's file-local clock to book time."""
        if file_t is None:
            return None
        if not self.section_files:
            return file_t
        from .librivox_book import file_to_book
        return file_to_book(self._section_index, file_t, self.section_starts)

    def play_next_section(self) -> bool:
        """Advance to the next section file. True when one was started."""
        if not self.section_files:
            return False
        nxt = self._section_index + 1
        if nxt >= len(self.section_files):
            return False
        self._section_index = nxt
        self.app.session.chapter_index = nxt
        start = (self.section_starts[nxt]
                 if nxt < len(self.section_starts) else 0.0)
        self.app.play(self.section_files[nxt], start=0.0, length=0)
        self.app.session.set_playhead(start)
        return True

    def _key_playpause(self, _event=None):
        if self.app.player.alive:
            self.toggle_pause()
        else:
            self.preview_row()
        self._sync_play_btn()
        return "break"

    def _key_seek(self, delta):
        if self.app.player.alive:
            if self.section_files:
                t = (self.app.session.playhead or 0.0) + delta
                self.play_at_book_time(max(0.0, t))
            else:
                self.app.player.seek(delta)
        return "break"

    def _sync_play_btn(self):
        return None

    def _fill_from_selection(self, _event=None):
        """Selecting a row fills the pin and sweep fields."""
        row = self._selected_row()
        if not row:
            return
        if not self.pin_ch.get().strip() or self._pin_autofilled:
            self.pin_ch.set(str(row["index"] + 1))
            self.pin_time.set(_t(row.get("time") or row.get("published")))
            self._pin_autofilled = True
        if not self.sweep_start.get().strip() or self._sweep_autofilled:
            self._prefill_sweep(row)
        self._update_controls()

    def _sweep_edited(self, event):
        if event.char and event.char.isprintable() \
                or event.keysym in ("BackSpace", "Delete"):
            self._sweep_autofilled = False

    def _prefill_sweep(self, row):
        """Estimate listening spots: ±5 min around the chapter's time."""
        v = row.get("time") or row.get("published")
        if v is None or str(v) in ("", "-"):
            return
        try:
            seconds = parse_time(str(v))
        except ValueError:
            return
        dur = (self.analysis.get("envelope") or {}).get("duration") or 0.0
        self.sweep_start.set(clock(max(0.0, seconds - 300)))
        self.sweep_end.set(clock(min(dur, seconds + 300) or seconds + 300))
        self._sweep_autofilled = True
        span = (min(dur, seconds + 300) if dur else seconds + 300) \
            - max(0.0, seconds - 300)
        self.range_cost.set(f"~{span / 60:.0f} min")

    def toggle_pause(self):
        self.app.player.toggle_pause()
        self._sync_play_btn()

    def update_playback(self, t):
        """The player bar owns the clock. This keeps the pump call."""
        del t

    # pin/unpin -------------------------------------------------------------
    def cmd_pin(self):
        ch = self.pin_ch.get().strip()
        t = self.pin_time.get().strip()
        if not ch or not t:
            messagebox.showinfo("Pin", "enter a chapter and a time", parent=self)
            return
        try:
            seconds = parse_time(t)
        except ValueError:
            messagebox.showerror("Pin", f"cannot read time: {t}", parent=self)
            return
        self._run("pin", [*self._argv("pin"),
                          "--at", f"{ch}={clock(seconds)}"],
                  then=self.cmd_align, stage="resolve")

    def cmd_unpin(self):
        ch = self.pin_ch.get().strip()
        if not ch:
            row = self._selected_row()
            ch = str(row["index"] + 1) if row else ""
        if not ch:
            messagebox.showinfo(
                "Remove pin", "select a chapter (or type its number) first",
                parent=self)
            return
        try:
            idx = int(ch) - 1
        except ValueError:
            messagebox.showerror("Remove pin",
                                 f"{ch!r} is not a chapter number",
                                 parent=self)
            return
        if self.audio and str(idx) not in (self.analysis.get("pins") or {}):
            messagebox.showinfo("Remove pin",
                                f"chapter {ch} has no pin", parent=self)
            return
        self._run("pin", [*self._argv("pin"), "--remove", str(idx + 1)],
                  then=self.cmd_align, stage="resolve")

    # emit / finalize -----------------------------------------------------
    def _watch_finish_settings(self):
        def touch(*_args):
            self._update_controls()

        for var in (self.embed_var, self.include_cleanup, self.allow_delete_var,
                    self.bitrate_var, *self.meta_vars.values()):
            var.trace_add("write", touch)

    def _finish_hash(self) -> tuple:
        meta = tuple((key, var.get()) for key, var in self.meta_vars.items())
        preset = ""
        stages = 0
        quality = getattr(self, "quality", None)
        if quality is not None:
            preset = quality._preset_name
            stages = self.app.session.cleanup.get("stages_on") or 0
        return (meta, bool(self.embed_var.get()), bool(self.include_cleanup.get()),
                bool(self.allow_delete_var.get()), self.bitrate_var.get(),
                preset, stages)

    def _remember_dry_run(self):
        if not self.audio:
            return
        self._dry_ok[self.audio] = self._artifact_mtime()
        digest = self._finish_hash()
        self._dry_hash[self.audio] = digest
        self.app.session.last_dry_run = digest

    def _chapters_placed(self) -> bool:
        proj = getattr(self, "_projection", None)
        return bool(proj and any(row.placed is not None for row in proj.rows))

    def _cleanup_only(self) -> bool:
        return bool(self.include_cleanup.get()) and not self._chapters_placed()

    def _sync_cleanup_choice(self):
        self.app.session.cleanup["include"] = bool(self.include_cleanup.get())
        self.app.session.emit("cleanup")
        self._update_finish_cards()

    def _update_finish_cards(self):
        if not hasattr(self, "chapters_card"):
            return
        proj = getattr(self, "_projection", None)
        if proj is None or not self._chapters_placed():
            self.chapters_card.set("0 of 0 placed · 0 withheld · 0 pins")
        else:
            pins = sum(1 for row in proj.rows if row.pin)
            withheld = len(proj.rows) - proj.confident_count
            total = len(proj.rows)
            self.chapters_card.set(
                f"{proj.confident_count} of {total} placed · "
                f"{withheld} withheld · {pins} pins")
        bits = [self.meta_vars[key].get().strip()
                for key in ("title", "author", "album", "year")
                if self.meta_vars[key].get().strip()]
        self.tags_card.set(" · ".join(bits) if bits else "title · author · album · year")
        stages = self.app.session.cleanup.get("stages_on") or 0
        quality = getattr(self, "quality", None)
        preset = "custom"
        if quality is not None and quality._preset_name:
            preset = quality._preset_name
        if self.include_cleanup.get() and stages:
            self.cleanup_card.set(f"{preset} · {stages} stages")
        else:
            self.cleanup_card.set(
                "Not included — the audio is copied untouched.")

    def _clear_dry_log(self):
        self.dry_log.configure(state="normal")
        self.dry_log.delete("1.0", "end")
        self.dry_log.configure(state="disabled")
        if hasattr(self, "_plan_empty"):
            self._plan_empty = True
            if hasattr(self, "_plan_empty_frame"):
                self._plan_empty_frame.grid()
                self.dry_log.grid_remove()

    def _dry_line(self, line: str):
        if hasattr(self, "_show_plan_log"):
            self._show_plan_log()
        self.dry_log.configure(state="normal")
        self.dry_log.insert("end", line.rstrip() + "\n")
        self.dry_log.configure(state="disabled")
        self.dry_log.see("end")

    def _planned_actions(self) -> list[str]:
        lines = []
        if self.include_cleanup.get():
            if self.section_files:
                lines.append(
                    f"clean {len(self.section_files)} section files in place")
            else:
                lines.append("render cleaned audio")
        if self._chapters_placed():
            if self.section_files:
                lines.append("write chapters.txt and chapters.json")
                lines.append("refresh album.nfo (section starts)")
                lines.append("reload Audiobookshelf")
            elif self.embed_var.get():
                lines.append("embed chapters and tags into the file")
                lines.append("move that file into the library")
                lines.append("write album.nfo")
                lines.append("reload Audiobookshelf")
                lines.append("keep the first original")
            else:
                lines.append("write chapters.txt and chapters.json")
                lines.append("move that file into the library")
                lines.append("write album.nfo")
                lines.append("reload Audiobookshelf")
                lines.append("keep the first original")
        elif self.include_cleanup.get():
            lines.append("replace the library file with the cleaned audio")
            lines.append("keep the first original")
        else:
            lines.append("chapters: not written")
        return lines

    def _library_chaptered(self) -> str:
        root, ext = os.path.splitext(self.audio or "")
        return f"{root}.chapters{ext}"

    def _cleaned_audio(self) -> str:
        from chaptertools.audio_quality.worker import output_mp3_path
        return output_mp3_path(self.work_dir or "", self.audio or "")

    def cmd_emit(self):
        assert self.work_dir is not None
        self._emit_into(self.audio or "")

    def _ensure_alignment_for_emit(self) -> bool:
        """Emit needs an alignment stage. Seed one from placed NFO/pins."""
        from chaptertools.envelope.artifact import Artifact
        from chaptertools.nfo import read_nfo_chapters

        from .stages import placement_rows, seed_alignment

        if not self.audio or not self.work_dir:
            return False
        art = Artifact(self.audio, self.work_dir)
        if (art.data.get("alignment") or {}).get("results"):
            self.analysis = load_analysis(self.audio, self.work_dir)
            return True
        analysis = art.data
        nfo = (self.harvest or {}).get("nfo_chapters") or []
        if not nfo:
            nfo = read_nfo_chapters(
                os.path.join(os.path.dirname(self.audio), "album.nfo"))
        rows = placement_rows(analysis, nfo)
        min_conf = self.min_conf.get() if hasattr(self, "min_conf") else 0.5
        seeded = seed_alignment(rows, min_conf)
        if seeded is None:
            messagebox.showerror(
                "Align first",
                "Chapters are not placed yet. On Place, run Align so Finish "
                "can write them.",
                parent=self)
            return False
        art.data["alignment"] = seeded
        art.save()
        self.analysis = load_analysis(self.audio, self.work_dir)
        return True

    def _emit_into(self, audio: str, then=None, source: str | None = None):
        assert self.work_dir is not None
        if not self._ensure_alignment_for_emit():
            return
        meta = self._meta_overrides()
        overrides = {flag: meta.get(key, "") for flag, key in META_FLAGS.items()}
        self._run("emit", emit_argv(
            self._cf, self.work_dir, audio, self.min_conf.get(),
            self.embed_var.get(), overrides, source=source), then=then, stage="emit")

    def cmd_finalize_dry(self):
        self._clear_dry_log()
        for line in self._planned_actions():
            self._dry_line(line)
        chaptered = self._library_chaptered()
        cleaned = os.path.splitext(self._cleaned_audio())
        cleaned_chaptered = f"{cleaned[0]}.chapters{cleaned[1]}"
        existing = next((path for path in (cleaned_chaptered, chaptered)
                         if os.path.isfile(path)), None)
        if existing and self._chapters_placed() and not self._cleanup_only():
            extra = ["finalize", "--chaptered", existing]
            self._run("finalize", self._argv(*extra), stage="finalize-dry",
                      on_line=self._dry_line)
            return
        self._remember_dry_run()
        self._update_controls()

    def _confirm_finalize(self) -> bool:
        nfo_path = os.path.join(os.path.dirname(self.audio or ""), "album.nfo")
        existing = read_nfo_chapters(nfo_path)
        prompt = ("Move the original aside and reload the book in "
                  "Audiobookshelf?")
        if len(existing) >= 2:
            prompt += (f"\n\nalbum.nfo already has {len(existing)} chapter "
                       "tracks. They will be replaced if they differ from "
                       "the chapters being written.")
        return messagebox.askyesno("Apply to library", prompt, parent=self)

    def cmd_apply_library(self):
        if not self.audio or not self._finalize_dry_ok:
            return
        if self._cleanup_only():
            if not messagebox.askyesno(
                    "Apply to library",
                    "Copy the cleaned audio over the library file?\n\n"
                    "The first original stays in the work folder.",
                    parent=self):
                return
            self._start_cleanup(self._replace_cleaned)
            return
        if not self._confirm_finalize():
            return
        if self.include_cleanup.get():
            self._start_cleanup(self._emit_then_finalize)
            return
        self._emit_then_finalize()

    def _start_cleanup(self, then):
        self._after_cleanup = then
        if not self.quality.cmd_process():
            self._after_cleanup = None

    def _replace_cleaned(self):
        self.quality.cmd_replace(confirmed=True)

    def _emit_then_finalize(self):
        audio = self.audio or ""
        source = None
        self._chaptered_for_apply = None
        if self.include_cleanup.get():
            source = audio
            audio = self._cleaned_audio()
            root, ext = os.path.splitext(audio)
            self._chaptered_for_apply = f"{root}.chapters{ext}"
        if not self._chapters_placed():
            self._replace_cleaned()
            return
        self._emit_into(audio, then=self._run_finalize_apply, source=source)

    def _run_finalize_apply(self):
        extra = ["finalize", "--apply", "-y", "--force-nfo"]
        if self._chaptered_for_apply:
            extra += ["--chaptered", self._chaptered_for_apply]
        if self.allow_delete_var.get():
            extra.append("--allow-delete")
        self._chaptered_for_apply = None
        self._run("finalize", self._argv(*extra), stage="finalize")

    def cmd_finalize_apply(self):
        self.cmd_apply_library()
