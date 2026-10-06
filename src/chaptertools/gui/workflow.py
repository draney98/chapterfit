"""Main-area controller: step rail, timeline, and the two stacks.

WorkflowPanel owns the command plumbing (one method per chapterfit
subcommand, all subprocess-driven via app.runner). The log lives in the
app's drawer.
"""
from __future__ import annotations

import json
import os
import queue
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

from chaptertools.nfo import chapters_differ, read_nfo_chapters
from chaptertools.util import clock, hms, package_env, parse_time

from . import fonts, palette, tips
from .busy import job_is_modal, opening_sentence
from .commands import (
    META_FLAGS,
    align_argv,
    chapterfit_prefix,
    emit_argv,
    find_asin_argv,
    identify_argv,
    job_argv,
    near_spec,
    same_source,
    whisper_argv,
)
from .facts import MediaFactsLoader
from .harvest import found_summary, placeholder_harvest
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
    STAGE_LABELS,
    DiskFacts,
    identify_verdict,
    next_stage,
    resolve_evidence,
    stage_states,
)
from .step_rail import STAGE_STEP, STEPS, StepRail, Workspace
from .table import CHAPTER_COLUMNS, TableViews
from .timeline import Timeline


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
    host = ttk.Frame(parent)
    host.pack(fill="both", expand=True)
    host.rowconfigure(0, weight=1)
    host.columnconfigure(0, weight=1)
    frames = {}
    for key in STEPS:
        frame = ttk.Frame(host)
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


class WorkflowPanel(TableViews, StagePanels, ttk.Frame):
    """Coordinator: header, step rail, timeline, and the two stacks."""

    def __init__(self, master, app):
        super().__init__(master, padding=(8, 4))
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
        style = ttk.Style(self)
        style.configure("Accent.TButton",
                        font=fonts.derived(self, weight="bold"))
        self._refresh()

    def _tip(self, widget, text: str):
        self._tips[widget] = tip = tips.tooltip(widget, text)
        return tip

    # ------------------------------------------------------------ widgets --
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._step = "scan"
        self._rescan_quality = False
        self.chapters_var = tk.StringVar(value="Chapters")

        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.book_var = tk.StringVar(value="(nothing selected)")
        book_lbl = ttk.Label(head, textvariable=self.book_var,
                             font=tkfont.nametofont("TkHeadingFont", root=self))
        book_lbl.grid(row=0, column=0, sticky="w")
        self._book_label = book_lbl
        self._header_text = "(nothing selected)"
        head.bind("<Configure>", lambda e: self._fit_header(e.width))
        self.head_tooltip = None
        book_lbl.bind("<Enter>", self._show_path_tip)
        book_lbl.bind("<Leave>", self._hide_tip)
        self.found_var = tk.StringVar(value="")

        self.rail = StepRail(head, self.show_step)
        self.rail.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        self.timeline = Timeline(self, self.app)
        self.timeline.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.gap_mode_var = self.timeline.mode

        self.workspace = Workspace(self)
        self.workspace.grid(row=2, column=0, sticky="nsew", pady=(4, 0))
        self._main_frames = _stack(self.workspace.main)
        self._insp_frames = _stack(self.workspace.inspector)
        self._wrap_labels = []
        self.workspace.inspector.bind("<Configure>", self._on_host_resize)
        place = ttk.Panedwindow(self._insp_frames["place"], orient="vertical")
        place.grid(row=0, column=0, sticky="nsew")
        self._panels = {
            "measure": self._panel_measure(self._main_frames["scan"]),
            "identify": self._panel_identify(self._insp_frames["match"]),
            "align": self._panel_align(place),
            "resolve": self._panel_resolve(place),
            "emit": self._panel_emit(self._main_frames["finish"]),
            "finalize": self._panel_finalize(self._insp_frames["finish"]),
        }
        for key in ("measure", "emit", "finalize"):
            self._panels[key].grid(row=0, column=0, sticky="nsew")
        self._panels["identify"].grid(row=0, column=0, sticky="new")
        self._build_book_info(self._insp_frames["match"])
        self._build_editions(self._main_frames["match"])
        place.add(self._panels["align"], weight=1)
        place.add(self._panels["resolve"], weight=2)
        self._build_chapters_tab(self._main_frames["place"])
        self._build_quality_tab(self._main_frames["cleanup"])
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
        self.quality = QualityTab(parent, app=self.app, workflow=self)
        self.quality.grid(row=0, column=0, sticky="nsew")

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
            step in ("match", "place", "finish"), table=step == "place")
        if step == "finish":
            self.timeline.grid_remove()
        else:
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

    def _sync_rail(self, states: dict | None = None) -> None:
        states = states or self._compute_states()
        preset = self.app.session.cleanup.get("preset") or "off"
        on = int(self.app.session.cleanup.get("stages_on") or 0)
        cleanup = f"{preset} · {on} stages" if on else "off"
        self.rail.update(states, self._step, cleanup)

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
        self.found_var.set(found_summary(harvest))
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
        self.audio = os.path.abspath(audio)
        self.work_dir = work_dir_for(self.audio)
        self.app.session.book = self.audio
        self.app.session.playhead = 0.0
        self.app.session.selection = None
        self.app.session.preview_marks = []
        self.app.session.emit("book")
        if hasattr(self, "timeline"):
            self.timeline.view = None
        self.book_var.set(self.audio)
        folder = os.path.dirname(self.audio)
        title = os.path.basename(folder) or os.path.basename(self.audio)
        author = os.path.basename(os.path.dirname(folder))
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
            self.identify_mode.set("asin")
            self.identify_value.set(label.split(" ", 1)[1].strip())
            self._identify_autofilled = True
        elif label:
            if os.path.basename(label) == "chapters.embedded.json":
                self.identify_mode.set("emb")
            elif os.path.basename(label) == "chapters.nfo.json":
                self.identify_mode.set("nfo")
            elif os.path.isfile(label):
                self.identify_mode.set("json")
            else:
                return
            self.identify_value.set(label)
            self._identify_autofilled = True
        elif (asin := top_asin(self.audio or "", self.work_dir)):
            self.identify_mode.set("asin")
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
        self.found_var.set("reading tags and chapters from the file…")
        self._request_facts(harvest=True)

    def _update_header(self):
        """One line: Title — Author · duration · ASIN · elided file name."""
        s = self.harvest.get("suggested") or {}
        title = s.get("title") or "(nothing selected)"
        author = s.get("author") or ""
        dur = ((self.analysis.get("envelope") or {}).get("duration"))
        ident = self.analysis.get("identification") or {}
        asin = ident.get("label") or ident.get("candidate") or ""
        if not asin:
            asin = top_asin(self.audio or "", self.work_dir) or ""
        name = os.path.basename(self.audio) if self.audio else ""
        bits = [f"{title} — {author}" if author else title]
        if dur:
            bits.append(hms(dur))
        if asin and asin.lower() != "none":
            bits.append(asin)
        self._header_bits = (bits, name)
        self._fit_header(self._book_label.master.winfo_width())

    def _fit_header(self, width: int):
        """The header stays one line: the file name is elided from the
        middle first, then the title (the full path is in the tooltip)."""
        bits, name = getattr(self, "_header_bits", (["(nothing selected)"],
                                                    ""))
        font = tkfont.nametofont("TkHeadingFont", root=self)
        room = max(width - 8, 200) if width > 1 else 10 ** 6

        def text(bits, name):
            return "  ·  ".join([*bits, name] if name else bits)

        if font.measure(text(bits, name)) > room and name:
            rest = font.measure(text(bits, "")) + font.measure("  ·  ")
            name = _elide(name, font, max(room - rest, font.measure("ab…yz")))
        if font.measure(text(bits, name)) > room:
            rest = font.measure(text(["", *bits[1:]], name))
            bits = [_elide(bits[0], font, max(room - rest, 80)), *bits[1:]]
        self.book_var.set(text(bits, name))

    def _show_path_tip(self, _event=None):
        self._hide_tip()
        if not self.audio:
            return
        x = self.winfo_rootx() + 8
        y = self.winfo_rooty() + self.winfo_height() + 2
        self.head_tooltip = tk.Toplevel(self)
        self.head_tooltip.wm_overrideredirect(True)
        self.head_tooltip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.head_tooltip, text=self.audio, background=palette.TIP,
                 foreground=palette.TEXT, font="TkDefaultFont",
                 relief="solid", borderwidth=1, padx=4, pady=1).pack()

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
        env = (self.analysis or {}).get("envelope") or {}
        session.duration = float(env.get("duration") or 0.0)
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
        return [self.measure_btn, self.identify_btn, self.identify_entry,
                self.identify_browse, *self.identify_radios, self.align_btn,
                self.min_conf_spin,
                self.hint_btn, self.listen_btn, self.sweep_start_entry,
                self.sweep_end_entry, self.range_btn, self.probe_btn,
                self.full_btn, self.reread_btn,
                self.finalize_btn, self.apply_btn,
                self.asin_search_btn]

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
            off([self.apply_btn], "run a successful Dry run first")
        mode = self.identify_mode.get()
        if mode == "emb":
            off([self.identify_entry],
                "Embedded uses the chapters already in the file")
        if mode == "nfo":
            off([self.identify_entry],
                "NFO uses the chapter tracks in album.nfo")
        if mode != "json":
            off([self.identify_browse], "Browse picks a chapters JSON file")
        if mode != "asin":
            off([self.asin_search_btn], "Search is for the ASIN source")
        for w in self._job_controls():
            why = reasons.get(w)
            w.state(["disabled" if why else "!disabled"])
            tip = self._tips.get(w)
            if tip is not None:
                tip.reason = why

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
        self.identify_result.set(proj.identify_text)
        self.identify_btn.configure(text=proj.identify_button)
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
            ttk.Label(self.unresolved_frame,
                      text=f"{len(weak)} chapters unresolved:").pack(
                side="left")
            for r in weak:
                n = r["index"] + 1
                lbl = tk.Label(self.unresolved_frame, text=str(n),
                               fg=palette.LINK, cursor="hand2",
                               font=fonts.derived(self, underline=True))
                lbl.pack(side="left", padx=2)
                lbl.bind("<Button-1>",
                         lambda e, r=r: self._select_unresolved(r))
        else:
            ttk.Label(self.unresolved_frame,
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
            btn.configure(style="Accent.TButton" if name == accent else "TButton")

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
            self._maybe_identify_from_nfo()
            if self._rescan_quality:
                self._rescan_quality = False
                self._scan_quality()

    # ------------------------------------------------------------- actions --
    def cmd_measure(self):
        self._run("measure", self._argv("measure"), stage="measure")

    def cmd_rescan(self):
        """Measure again, then refresh the loudness tiles from that file."""
        if not self.audio:
            return
        self._rescan_quality = True
        self._run("measure", self._argv("measure", "--redo", "envelope"),
                  stage="measure")

    def _large_audio(self) -> bool:
        try:
            return bool(self.audio) and os.path.getsize(self.audio) >= 4096
        except OSError:
            return False

    def _measure_current(self) -> bool:
        env = (self.analysis or {}).get("envelope") or {}
        if not env.get("duration"):
            return False
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
        if hasattr(self, "apply_btn"):  # built: enablement is decided centrally
            self._update_controls()
        if mode == "json" and not self.identify_value.get():
            self.identify_value.set(os.path.join(
                os.path.dirname(self.audio or ".") or ".", "chapters.json"))
        if hasattr(self, "asin_frame"):
            if mode == "asin":
                self.asin_frame.grid()
            else:
                self.asin_frame.grid_remove()

    def _identify_browse(self):
        path = filedialog.askopenfilename(
            parent=self, title="Chapters JSON",
            filetypes=[("JSON", "*.json"), ("All files", "*")])
        if path:
            self.identify_value.set(path)

    def _same_source_as_last(self) -> bool:
        return same_source(
            self.identify_mode.get(),
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

    def _fill_asin_list(self):
        if not hasattr(self, "asin_tree"):
            return
        counts = _chapter_counts(self.audio or "", self.work_dir)
        self._filling_editions = True
        self.asin_tree.delete(*self.asin_tree.get_children())
        for row in asin_candidates(self.audio or "", self.work_dir):
            self.asin_tree.insert("", "end", values=(
                row["asin"], row["title"], row["author"], row["narrator"],
                row["runtime"], counts.get(row["asin"], ""),
                self._fit_label(row["asin"])))
        self._filling_editions = False
        self._show_edition_marks()

    def _fit_label(self, asin: str) -> str:
        ident = self.analysis.get("identification") or {}
        label = ident.get("label") or ""
        if not asin or not label.endswith(asin):
            return ""
        verdict = identify_verdict(ident) or ""
        score = (ident.get("best_trial") or {}).get("score")
        if isinstance(score, (int, float)):
            return f"{verdict} {score:.1f}".strip()
        return verdict

    def _show_edition_marks(self) -> None:
        chapters = (self.analysis.get("identification") or {}).get("chapters") or []
        marks = [float(c["start"]) for c in chapters
                 if isinstance(c, dict) and isinstance(c.get("start"), (int, float))]
        self.app.session.preview_marks = marks
        if hasattr(self, "timeline"):
            self.timeline.redraw()
        if hasattr(self, "fit_var"):
            chosen = ""
            label = (self.analysis.get("identification") or {}).get("label") or ""
            if label.startswith("Audnexus "):
                chosen = self._fit_label(label.split(" ", 1)[1].strip())
            self.fit_var.set(chosen)

    def _on_asin_select(self, _event=None):
        if getattr(self, "_filling_editions", False):
            return
        sel = self.asin_tree.selection()
        if not sel:
            return
        asin = self.asin_tree.item(sel[0], "values")[0]
        if not asin:
            return
        self.identify_value.set(asin)
        self._identify_autofilled = True
        label = (self.analysis.get("identification") or {}).get("label") or ""
        if label.endswith(asin):
            self._show_edition_marks()
            return
        self.cmd_identify_asin(asin)

    def cmd_use_edition(self):
        """Fill empty book-info fields, align, and open Place."""
        sel = self.asin_tree.selection()
        if not sel:
            return
        asin = str(self.asin_tree.item(sel[0], "values")[0])
        defaults = asin_emit_defaults(
            self.audio or "", self.work_dir, f"Audnexus {asin}")
        filled = []
        for key, value in defaults.items():
            var = self.meta_vars.get(key)
            if var is None or not value or var.get().strip():
                continue
            var.set(value)
            filled.append(key)
        if filled:
            self.found_var.set("filled from the edition: " + ", ".join(filled))
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
        self._run("find-asin", find_asin_argv(
            self.audio, out,
            title=self.asin_title.get(),
            author=self.asin_author.get(),
            keywords=self.asin_keywords.get(),
            region=self.asin_region.get()),
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

    def cmd_identify_primary(self):
        verdict = identify_verdict(self.analysis.get("identification") or {})
        if verdict == "rejected" and self._same_source_as_last():
            # the same source would be rejected again: ask for another one
            self.identify_entry.focus_set()
            self.identify_entry.select_range(0, "end")
            self.app.log("enter a different ASIN or chapters JSON file, "
                         "then Identify")
            return
        mode = self.identify_mode.get()
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
            return
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
        ttk.Label(bar, text=self._problem_sentence(row)).grid(
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
            ttk.Label(bar, text=f"{clock(mid)} · {dur:.2f} s").grid(
                row=0, column=col, sticky="w", padx=(8, 2))
            col += 1
            ttk.Button(
                bar, text="Use this",
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
        self.app.play(self.audio, start=start, length=0)  # never self-stops

    def _key_playpause(self, _event=None):
        if self.app.player.alive:
            self.toggle_pause()
        else:
            self.preview_row()
        self._sync_play_btn()
        return "break"

    def _key_seek(self, delta):
        if self.app.player.alive:
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
            self.chapters_card.set("Chapters: not written")
        else:
            pins = sum(1 for row in proj.rows if row.pin)
            withheld = len(proj.rows) - proj.confident_count
            self.chapters_card.set(
                f"Chapters: {proj.confident_count} written, "
                f"{withheld} withheld, {pins} pins")
        bits = [f"{key} {self.meta_vars[key].get().strip()}"
                for key in ("title", "author", "album", "year", "genre")
                if self.meta_vars[key].get().strip()]
        tags = ", ".join(bits) if bits else "filled in on Match"
        self.tags_card.set(f"Tags: {tags} · also written to album.nfo")
        preset = "custom"
        stages = self.app.session.cleanup.get("stages_on") or 0
        quality = getattr(self, "quality", None)
        if quality is not None and quality._preset_name:
            preset = quality._preset_name
        state = "in this write" if self.include_cleanup.get() else "off"
        about = ""
        factor = getattr(quality, "_render_factor", None)
        duration = float(self.app.session.duration or 0)
        if factor and duration > 0:
            from chaptertools.gui.quality_tab.tab import _eta
            about = f" · about {_eta(factor * duration)}"
        self.cleanup_card.set(
            f"Audio cleanup: {state} · {preset} · {stages} stages · "
            f"MP3 {self.bitrate_var.get()}{about}")

    def _clear_dry_log(self):
        self.dry_log.configure(state="normal")
        self.dry_log.delete("1.0", "end")
        self.dry_log.configure(state="disabled")

    def _dry_line(self, line: str):
        self.dry_log.configure(state="normal")
        self.dry_log.insert("end", line.rstrip() + "\n")
        self.dry_log.configure(state="disabled")
        self.dry_log.see("end")

    def _planned_actions(self) -> list[str]:
        lines = []
        if self.include_cleanup.get():
            lines.append("render cleaned audio")
        if self._chapters_placed():
            if self.embed_var.get():
                lines.append("embed chapters and tags into the file")
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

    def _emit_into(self, audio: str, then=None, source: str | None = None):
        assert self.work_dir is not None
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
