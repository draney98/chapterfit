"""Main-area controller and stage panels: measure → … → finalize.

WorkflowPanel owns the command plumbing (one method per chapterfit
subcommand, all subprocess-driven via app.runner) and builds the main
area: header, stage bar, one visible stage panel, and the notebook tabs
(Chapters, Transcripts). The Log tab and the status bar belong to the app.
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
from .projection import (
    WorkflowProjection,
    filter_transcript,
    transcript_line_time,
    transcript_text,
)
from .runner import Job
from .stages import (
    ATTENTION,
    BLOCKED,
    STAGE_KEYS,
    STAGE_LABELS,
    DiskFacts,
    StageBar,
    identify_verdict,
    next_stage,
    resolve_evidence,
    stage_states,
)
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


def _live(t: float) -> str:
    """Playback clock with hundredths: 1:02:03.45."""
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


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


def _t(v) -> str:
    """No empty time cells: a zero clock instead of '-' or blank."""
    if v is None or v == "" or v == "-":
        return "0:00:00.000"
    return str(v) if isinstance(v, str) and ":" in v else hms(v)


class WorkflowPanel(TableViews, StagePanels, ttk.Frame):
    """Coordinator: header, stage bar, projection refresh, command jobs."""

    def __init__(self, master, app):
        super().__init__(master, padding=(8, 4))
        self.app = app
        self.notebook = ttk.Notebook(self)
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
        self._pin_autofilled = False
        self._sweep_autofilled = False
        self._busy = False
        self._tips: dict = {}

        self._build()
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
        self.rowconfigure(5, weight=1)

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

        self.timeline = Timeline(self, self.app)
        self.timeline.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.gap_mode_var = self.timeline.mode

        self.stage_bar = StageBar(self, self._pick_stage)
        self.stage_bar.grid(row=2, column=0, sticky="ew", pady=(4, 0))

        # amber explainer strip: shown when the SELECTED stage needs attention
        self.banner_var = tk.StringVar(value="")
        self.banner = ttk.Label(self, textvariable=self.banner_var,
                                background=palette.BANNER,
                                foreground=palette.WARN,
                                padding=(8, 3))

        # panel host sizes to the tallest visible panel (nothing clips)
        self._panel_host = ttk.Frame(self)
        self._panel_host.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        self._panel_host.columnconfigure(0, weight=1)
        self._panel_host.rowconfigure(0, weight=1)
        self._wrap_labels = [self.banner]
        self._panel_host.bind("<Configure>", self._on_host_resize)
        self._panels = {
            "measure": self._panel_measure(self._panel_host),
            "identify": self._panel_identify(self._panel_host),
            "align": self._panel_align(self._panel_host),
            "resolve": self._panel_resolve(self._panel_host),
            "emit": self._panel_emit(self._panel_host),
            "finalize": self._panel_finalize(self._panel_host),
        }

        self._build_chapters_tab()
        self._build_transcripts_tab()
        self._build_quality_tab()
        self.notebook.grid(row=5, column=0, columnspan=2, sticky="nsew")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab)
        self.set_stage("measure")

    TABLE_MIN_ROWS = 4

    def _panel_need(self, key) -> tuple[int, int]:
        """(width, height) a stage panel needs; Resolve counted stacked."""
        if key != "resolve":
            p = self._panels[key]
            return p.winfo_reqwidth(), p.winfo_reqheight()
        left, right = self._resolve_left, self._resolve_right
        summary = self._panels["resolve"].grid_slaves(row=0)[0]
        w = max(left.winfo_reqwidth(), right.winfo_reqwidth(),
                summary.winfo_reqwidth()) + 8
        h = (summary.winfo_reqheight() + left.winfo_reqheight()
             + right.winfo_reqheight() + 16)
        return w, h

    def min_width(self) -> int:
        self.update_idletasks()
        table = sum(int(self.tree.column(c, "minwidth"))
                    for c, *_ in CHAPTER_COLUMNS) + 24
        return max([self._panel_need(k)[0] for k in self._panels]
                   + [self.stage_bar.winfo_reqwidth(), table]) + 16

    def min_height(self) -> int:
        """Header, stage bar, banner, the tallest panel, and a table that
        still shows a few rows."""
        self.update_idletasks()
        rowh = int(ttk.Style(self).lookup("Chapter.Treeview", "rowheight")
                   or 24)
        chrome = sum(w.winfo_reqheight() for w in
                     (self.grid_slaves(row=0)[0], self.timeline,
                      self.stage_bar, self.banner))
        tab_extra = sum(w.winfo_reqheight() for w in
                        self._chapters_tab.grid_slaves()
                        if w is not self.tree)
        tallest = max(self._panel_need(k)[1] for k in self._panels)
        return (chrome + tallest + tab_extra + 40
                + rowh * (self.TABLE_MIN_ROWS + 1) + 24)

    def _on_host_resize(self, event):
        """Long text wraps to the panel width instead of widening it."""
        width = event.width
        for lbl in self._wrap_labels:
            lbl.configure(wraplength=max(width - 24, 200))
        need = (self._resolve_left.winfo_reqwidth()
                + self._resolve_right.winfo_reqwidth() + 16)
        self._layout_resolve(side_by_side=need <= width)

    # ---------------------------------------------------------- stage panels
    def _copy_header_fix(self):
        fix = self._header_fix()
        if fix:
            self.clipboard_clear()
            self.clipboard_append(fix)
            self.app.log("copied: " + fix)

    # ------------------------------------------------------------- tabs --
    def _build_transcripts_tab(self):
        tab = ttk.Frame(self.notebook)
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(1, weight=1)
        ttk.Label(tab, text="Filter (regex):").grid(row=0, column=0,
                                                   sticky="w")
        self.grep_var = tk.StringVar(value="chapter")
        e = ttk.Entry(tab, textvariable=self.grep_var)
        e.grid(row=0, column=1, sticky="ew", padx=4)
        e.bind("<Return>", lambda e: self.cmd_transcripts())
        self.grep_var.trace_add(
            "write", lambda *_: self._show_saved_transcripts())
        self._tip(e, "only lines matching this regular expression, case "
                  "insensitive; empty shows everything")
        show = ttk.Button(tab, text="Show transcripts",
                          command=self.cmd_transcripts)
        show.grid(row=0, column=2, sticky="w")
        self._tip(show, "list what Listen and Sweep heard, with times")
        self.transcripts_text = tk.Text(
            tab, height=10, wrap="none",
            font=tkfont.nametofont("TkFixedFont", root=self))
        self.transcripts_text.grid(row=1, column=0, columnspan=3,
                                   sticky="nsew")
        self.transcripts_text.bind("<Double-1>", self._transcript_seek)
        self.notebook.add(tab, text="Transcripts")

    def _build_quality_tab(self):
        from chaptertools.gui.quality_tab import QualityTab
        self.quality = QualityTab(self.notebook, app=self.app, workflow=self)
        self.notebook.add(self.quality, text="Quality")
        self._quality_open = False

    def _on_notebook_tab(self, _event=None):
        """The quality page takes the space the stage panels normally use."""
        selected = self.notebook.select()
        opening = selected == str(self.quality)
        self._quality_open = opening
        if opening:
            self._panel_host.grid_remove()
            self.banner.grid_remove()
            return
        self.stage_bar.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self._panel_host.grid(row=4, column=0, sticky="ew", pady=(4, 0))
        self.set_stage(self._stage)

    # -------------------------------------------------------------- state --
    def set_stage(self, key: str):
        if key not in STAGE_KEYS:
            return
        self._stage = key
        states = self._compute_states()
        self.stage_bar.update(states, key)
        self._update_banner(states)
        if getattr(self, "_quality_open", False):
            self.banner.grid_remove()
            return
        for k, panel in self._panels.items():
            if k == key:
                panel.grid(row=0, column=0, sticky="nsew")
            else:
                panel.grid_remove()

    def _update_banner(self, states: dict | None = None):
        """Amber strip: why the stage we're ON needs attention."""
        states = states or self._compute_states()
        st = states.get(self._stage) or {}
        REMEDY = {
            "measure": "rewrite the Xing header before trusting timestamps",
            "identify": "pick a different edition and identify again",
            "align": "use Resolve to place them: Listen, Sweep, or pin by ear",
            "resolve": "use Listen, Sweep, or pin by ear to place them",
            "emit": "run Emit to write the supported chapters",
            "finalize": "run the dry run first",
        }
        # Resolve's own summary line already says what is unresolved
        if st.get("state") == ATTENTION and self._stage != "resolve":
            detail = st.get("detail") or "needs attention"
            self.banner_var.set(f"{STAGE_LABELS[self._stage]}: {detail} — "
                                f"{REMEDY.get(self._stage, '')}")
            self.banner.grid(row=3, column=0, sticky="ew")
        else:
            self.banner_var.set("")
            self.banner.grid_remove()

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
        """A dry run succeeded for this book and nothing changed since."""
        return bool(self.audio) and self.audio in self._dry_ok \
            and self._dry_ok[self.audio] == self._artifact_mtime()

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
        for r in proj.rows:
            iid = self.tree.insert("", "end", tags=r.tags, values=(
                r.mark, r.title, hms(r.published), _t(r.placed),
                r.gap,
                "" if r.confidence is None else f"{r.confidence:.2f}",
                r.pin))
            self._row_data[iid] = r.source
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])
        self.tree.yview_moveto(scroll[0] if scroll else 0)

        self.conflict_note.set(
            "pins from an older Align: re-run Align to detect conflicting "
            "pins" if proj.conflicts_unknown else "")
        if proj.conflicts_unknown:
            self.conflict_label.grid(row=2, column=0, columnspan=2,
                                     sticky="w")
        else:
            self.conflict_label.grid_remove()
        self.notebook.tab(self._chapters_tab,
                          text=f"Chapters {proj.confident_count}/{len(proj.rows)}")

        states = proj.states
        self.stage_bar.update(states, self._stage)
        self._update_banner(states)
        self._update_panel_results(states)
        self._update_resolve()
        self._update_header()
        self.timeline.redraw()
        self._show_saved_transcripts()
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
                self.near_entry, self.min_conf_spin, self.hint_entry,
                self.hint_btn, self.listen_btn, self.sweep_start_entry,
                self.sweep_end_entry, self.range_btn, self.probe_btn,
                self.full_btn, self.pin_btn, self.unpin_btn, self.reread_btn,
                self.emit_btn, self.finalize_btn, self.apply_btn,
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
            off(self._job_controls() + [self.playfrom_btn],
                "select a book in the library first")
        stage_btns = {"align": [self.align_btn],
                      "resolve": [self.hint_btn, self.listen_btn,
                                  self.range_btn, self.probe_btn,
                                  self.full_btn, self.pin_btn, self.unpin_btn],
                      "emit": [self.emit_btn],
                      "finalize": [self.finalize_btn, self.apply_btn]}
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
        if not self._selected_row() and not self.pin_ch.get().strip():
            off([self.pin_btn, self.unpin_btn],
                "select a chapter in the table")

        for w in {*self._job_controls(), self.playfrom_btn}:
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
                  "Sweep range": self.range_btn, "Full sweep": self.full_btn,
                  "By ear": self.pin_btn}
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
        self.set_busy(True)
        self.app.runner.run(Job(
            name, argv, on_line=on_line, env=package_env(),
            on_done=lambda j, code: self._on_done(
                code, then=then, stage=stage, name=j.name,
                cancelled=getattr(j, "cancelled", False))))

    def _on_done(self, code, then=None, stage: str | None = None,
                 name: str = "the last step", cancelled: bool = False):
        self.set_busy(False)
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
                "chapterfit", f"{name} {why} - see the Log tab for details",
                parent=self)
            return
        if then:
            then()
            return
        if stage == "finalize-dry":
            self._dry_ok[self.audio] = self._artifact_mtime()
            self._update_controls()
            return
        if stage:
            target = next_stage(self._compute_states())
            if target != self._stage:
                self.set_stage(target)
            self._auto_stage = True  # again once fresh disk facts land
        if stage == "measure" and code == 0:
            self._maybe_identify_from_nfo()

    # ------------------------------------------------------------- actions --
    def cmd_measure(self):
        self._run("measure", self._argv("measure"), stage="measure")

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
        self.asin_tree.delete(*self.asin_tree.get_children())
        for row in asin_candidates(self.audio or "", self.work_dir):
            self.asin_tree.insert("", "end", values=(
                row["asin"], row["title"], row["author"], row["narrator"],
                row["runtime"], row["score"]))

    def _on_asin_select(self, _event=None):
        sel = self.asin_tree.selection()
        if not sel:
            return
        asin = self.asin_tree.item(sel[0], "values")[0]
        if not asin:
            return
        self.identify_value.set(asin)
        self._identify_autofilled = True

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

    def cmd_align(self):
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

    def _show_saved_transcripts(self):
        """Fill the Transcripts tab from the artifact, or clear it."""
        if not hasattr(self, "transcripts_text"):
            return
        pattern = self.grep_var.get() if hasattr(self, "grep_var") else ""
        body = filter_transcript(transcript_text(self.analysis), pattern)
        self.transcripts_text.delete("1.0", "end")
        if body:
            self.transcripts_text.insert("1.0", body + "\n")

    def cmd_transcripts(self):
        argv = self._argv("transcripts")
        grep = self.grep_var.get().strip()
        if grep:
            argv += ["--grep", grep]
        self.transcripts_text.delete("1.0", "end")
        self._run("transcripts", argv, on_line=self._transcript_line)

    def _transcript_line(self, line: str):
        self.transcripts_text.insert("end", line + "\n")
        self.transcripts_text.see("end")

    def _transcript_seek(self, event):
        index = self.transcripts_text.index(f"@{event.x},{event.y}")
        line = self.transcripts_text.get(f"{index} linestart",
                                         f"{index} lineend")
        t = transcript_line_time(line)
        if t is None or not self.audio:
            return
        if self.app.player.alive:
            self.app.player.seek_to(t)
        else:
            self.app.play(self.audio, start=t, length=0)

    # pin by ear ----------------------------------------------------------
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

    def play_from_time(self):
        """Start uncapped playback at the time in the field."""
        if not self.audio:
            return
        try:
            seconds = parse_time(self.pin_time.get().strip())
        except ValueError:
            messagebox.showerror("Play from time",
                                 f"cannot read time: {self.pin_time.get()}",
                                 parent=self)
            return
        self.app.play(self.audio, start=max(0.0, seconds), length=0)
        self._sync_play_btn()

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
        self.play_btn.configure(
            text="⏸ Pause" if self.app.player.alive
            and not self.app.player.paused else "▶ Play")

    def _fill_from_selection(self, _event=None):
        """Selecting a row fills chapter/time/sweep fields; manual edits in
        the chapter and time fields are never clobbered."""
        row = self._selected_row()
        if not row:
            return
        self._update_byear(row)
        if not self.pin_ch.get().strip() or self._pin_autofilled:
            self.pin_ch.set(str(row["index"] + 1))
            self.pin_time.set(_t(row.get("time") or row.get("published")))
            self._pin_autofilled = True
        self.play_btn.state(["!disabled"])
        if not self.sweep_start.get().strip() or self._sweep_autofilled:
            self._prefill_sweep(row)
        self._update_controls()

    def _sweep_edited(self, event):
        if event.char and event.char.isprintable() \
                or event.keysym in ("BackSpace", "Delete"):
            self._sweep_autofilled = False

    def _update_byear(self, row):
        name = row.get("title") or f"chapter {row['index'] + 1}"
        # long titles are elided so the column never outgrows the panel
        name = _elide(name, fonts.derived(self, weight="bold"), 360)
        self.byear_title.set(f"{name}  (#{row['index'] + 1})")
        conf = row.get("confidence")
        conf_s = f"{conf:.2f}" if isinstance(conf, (int, float)) \
            else (conf or "–")
        t = row.get("time")
        if t is None or str(t) in ("", "-"):
            self.byear_info.set("not placed yet")
        else:
            self.byear_info.set(f"placed {_t(t)}   confidence {conf_s}")

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

    def set_from_playback(self):
        t = self.app.current_playback()
        if t is None:
            return
        self.pin_time.set(_live(t))
        if not self.pin_ch.get().strip():
            row = self._selected_row()
            if row:
                self.pin_ch.set(str(row["index"] + 1))
            self._pin_autofilled = True

    def update_playback(self, t):
        """Called ~10x/s from the app pump with the live playback time."""
        self.playback_var.set(_t(t) if t is not None else "0:00:00.000")
        active = t is not None
        for b in (*self.seek_btns, self._set_btn, self.stop_btn):
            b.state(["!disabled" if active else "disabled"])
        self._sync_play_btn()

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
    def cmd_emit(self):
        assert self.work_dir is not None
        meta = self._meta_overrides()
        overrides = {flag: meta.get(key, "") for flag, key in META_FLAGS.items()}
        self._run("emit", emit_argv(
            self._cf, self.work_dir, self.audio, self.min_conf.get(),
            self.embed_var.get(), overrides), stage="emit")

    def cmd_finalize_dry(self):
        self._run("finalize", self._argv("finalize"), stage="finalize-dry")

    def cmd_finalize_apply(self):
        nfo_path = os.path.join(os.path.dirname(self.audio or ""), "album.nfo")
        existing = read_nfo_chapters(nfo_path)
        prompt = ("Move the original aside and reload the book in "
                  "Audiobookshelf?")
        if len(existing) >= 2:
            prompt += (f"\n\nalbum.nfo already has {len(existing)} chapter "
                       "tracks. They will be replaced if they differ from "
                       "the chapters being written.")
        if not messagebox.askyesno("Finalize", prompt, parent=self):
            return
        argv = self._argv("finalize", "--apply", "-y", "--force-nfo")
        if self.allow_delete_var.get():
            argv.append("--allow-delete")
        self._run("finalize", argv, stage="finalize")
