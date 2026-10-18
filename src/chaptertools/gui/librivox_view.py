"""Get-from-LibriVox view: search, plan detail, and fetch progress.

Driven only by ``chapterfit librivox … --json`` jobs. Never imports
``librivox_fetch``.
"""
from __future__ import annotations

import contextlib
import json
import os
import tkinter as tk
from tkinter import ttk

from chaptertools.gui import palette
from chaptertools.gui.commands import chapterfit_prefix
from chaptertools.gui.runner import Job
from chaptertools.gui.theme import ctk_frame, require_customtkinter, set_enabled
from chaptertools.gui.ui_kit import (
    BODY, C, SMALL, font, primary, secondary, text_button)
from chaptertools.util import clock

ctk = require_customtkinter()


def _readers_label(readers) -> str:
    if not readers:
        return "—"
    if isinstance(readers, str):
        return readers
    if len(readers) > 2:
        return f"{len(readers)} readers"
    return ", ".join(readers)


class LibrivoxView(ctk.CTkFrame):
    """Replaces the book pane while searching or fetching."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self.app = app
        self._prev_book: str | None = None
        self._plan: dict | None = None
        self._set_var = tk.StringVar()
        self._paused = False
        self._fetch_id: str | None = None
        self._fetch_title = ""
        self._mode = "search"  # search | fetch | stop
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self._host = ctk_frame(self, fg_color="transparent")
        self._host.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)
        self._host.columnconfigure(0, weight=1)
        self._host.rowconfigure(0, weight=1)
        self._build_search()
        self._build_fetch()
        self._show_search()

    # -- open / close -------------------------------------------------------

    def open_search(self):
        self._prev_book = getattr(self.app.workflow, "audio", None)
        self.app._show_librivox(True)
        self._show_search()
        self.title_entry.focus_set()

    def close(self):
        self.app._show_librivox(False)
        if self._prev_book:
            path = self._prev_book
            if os.path.isfile(path) or os.path.isdir(path):
                self.app.workflow.set_book(path)

    # -- search UI ----------------------------------------------------------

    def _build_search(self):
        self.search_frame = ctk_frame(self._host)
        self.search_frame.columnconfigure(0, weight=1)
        self.search_frame.rowconfigure(2, weight=1)

        head = ctk_frame(self.search_frame)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        head.columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="Get a book from LibriVox",
                  font=font(BODY, "bold")).grid(row=0, column=0, sticky="w")
        close = text_button(
            head, "Close", command=self.close,
            width=font(SMALL).measure("Close") + 24)
        close.grid(row=0, column=1, sticky="e")
        ctk.CTkLabel(
            head,
            text="Search the catalog, pick a finished recording, fetch tagged sections.",
            text_color=palette.MUTED).grid(row=1, column=0, sticky="w")

        form = ctk_frame(self.search_frame)
        form.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(form, text="Title starts with").grid(row=0, column=0, sticky="w")
        self.title_var = tk.StringVar()
        self.title_entry = ctk.CTkEntry(form, textvariable=self.title_var, width=280)
        self.title_entry.grid(row=0, column=1, sticky="ew", padx=(6, 12))
        ctk.CTkLabel(form, text="Author last name").grid(row=0, column=2, sticky="w")
        self.author_var = tk.StringVar()
        self.author_entry = ctk.CTkEntry(form, textvariable=self.author_var, width=160)
        self.author_entry.grid(row=0, column=3, sticky="ew", padx=(6, 12))
        primary(form, "Search", command=self._do_search).grid(
            row=0, column=4, sticky="e")
        form.columnconfigure(1, weight=1)
        ctk.CTkLabel(
            form,
            text="Matches from the start of the title — try the first word or two.",
            text_color=palette.MUTED).grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(4, 0))
        for entry in (self.title_entry, self.author_entry):
            entry.bind("<Return>", lambda _e: self._do_search())

        body = ctk_frame(self.search_frame)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0)
        body.rowconfigure(0, weight=1)

        cols = ("id", "title", "author", "readers", "length", "lang", "sections")
        self.results = ttk.Treeview(
            body, columns=cols, show="headings", selectmode="browse")
        headings = (
            ("id", "ID", 48), ("title", "Title", 180), ("author", "Author", 120),
            ("readers", "Readers", 88), ("length", "Length", 72),
            ("lang", "Lang", 56), ("sections", "Parts", 56),
        )
        for col, text, width in headings:
            self.results.heading(col, text=text)
            self.results.column(
                col, width=width, minwidth=width,
                stretch=col in ("title", "author"))
        self.results.tag_configure("unfinished", foreground=palette.MUTED)
        self.results.grid(row=0, column=0, sticky="nsew")
        self._results_empty = ctk.CTkLabel(
            body, text="Search for a title to see recordings.",
            text_color=palette.MUTED, fg_color="transparent")
        self._results_empty.grid(row=0, column=0)
        from .ui_kit import vscroll
        vscroll(body, self.results, row=0, column=0, sticky="nse")
        self.results.bind("<<TreeviewSelect>>", self._on_result_select)
        self.results.bind("<Double-1>", self._on_result_activate)
        self.results.bind("<Return>", self._on_result_activate)

        self.detail = ctk_frame(body, width=300)
        self.detail.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.detail.grid_propagate(False)
        self.detail.columnconfigure(0, weight=1)
        self._detail_title = tk.StringVar(value="Select a downloadable book")
        self._detail_meta = tk.StringVar(value="")
        self._detail_order = tk.StringVar(value="")
        self._detail_path = tk.StringVar(value="")
        self._detail_note = tk.StringVar(
            value="Files are named 00 - Title.mp3. Audio is not re-encoded.")
        ctk.CTkLabel(self.detail, textvariable=self._detail_title,
                  font=font(SMALL, "bold"), wraplength=440).grid(
            row=0, column=0, sticky="w")
        ctk.CTkLabel(self.detail, textvariable=self._detail_meta,
                  text_color=palette.MUTED, wraplength=440, text="").grid(
            row=1, column=0, sticky="w", pady=(2, 6))
        set_row = ctk_frame(self.detail)
        set_row.grid(row=2, column=0, sticky="ew")
        ctk.CTkLabel(set_row, text="Audio").pack(side="left")
        self.set_combo = ttk.Combobox(
            set_row, textvariable=self._set_var, state="readonly", width=22)
        self.set_combo.pack(side="left", padx=(6, 0))
        self.set_combo.bind("<<ComboboxSelected>>", lambda _e: self._replan())
        self._order_lbl = ctk.CTkLabel(
            self.detail, textvariable=self._detail_order, wraplength=440, text="")
        self._order_lbl.grid(row=3, column=0, sticky="w", pady=(6, 0))
        ctk.CTkLabel(self.detail, textvariable=self._detail_path,
                  text_color=palette.MUTED, wraplength=440, text="").grid(
            row=4, column=0, sticky="w", pady=(4, 0))
        self.section_list = tk.Listbox(self.detail, height=12)
        self.section_list.grid(row=5, column=0, sticky="nsew", pady=(6, 0))
        self.detail.rowconfigure(5, weight=1)
        self._files_empty = ctk.CTkLabel(
            self.detail, text="Pick a recording to see its files.",
            text_color=palette.MUTED, justify="left", anchor="w")
        self._files_empty.grid(row=5, column=0, sticky="nw", pady=(8, 0))
        note = ctk.CTkLabel(self.detail, textvariable=self._detail_note,
                  text_color=palette.MUTED, justify="left", anchor="w", text="")
        note.grid(row=6, column=0, sticky="ew", pady=(4, 0))
        from .ui_kit import wrap_to_parent
        wrap_to_parent(note)
        self.fetch_btn = secondary(
            self.detail, "Fetch into library", command=self._start_fetch)
        set_enabled(self.fetch_btn, False)
        self.fetch_btn.grid(row=7, column=0, sticky="ew", pady=(8, 0))

        self._status_var = tk.StringVar(value="")
        ctk.CTkLabel(self.search_frame, textvariable=self._status_var,
                  text_color=palette.MUTED, text="").grid(
            row=3, column=0, sticky="w", pady=(6, 0))
        self._empty_var = tk.StringVar(value="")
        ctk.CTkLabel(self.search_frame, textvariable=self._empty_var,
                  text_color=palette.MUTED, text="").grid(
            row=4, column=0, sticky="w")

        self._hits: dict[str, dict] = {}
        self._unfinished_ids: set[str] = set()
        self._sync_empty()

    def _sync_empty(self) -> None:
        if self.results.get_children():
            self._results_empty.grid_remove()
        else:
            self._results_empty.grid(row=0, column=0)
        if self.section_list.size() > 0:
            self._files_empty.grid_remove()
        else:
            self._files_empty.grid(row=5, column=0, sticky="nw", pady=(8, 0))

    def _build_fetch(self):
        self.fetch_frame = ctk_frame(self._host)
        self.fetch_frame.columnconfigure(0, weight=1)
        self.fetch_frame.rowconfigure(4, weight=1)

        head = ctk_frame(self.fetch_frame)
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self._fetch_head = tk.StringVar(value="Fetching…")
        self._fetch_sub = tk.StringVar(value="")
        ctk.CTkLabel(head, textvariable=self._fetch_head,
                  font=font(BODY, "bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(head, textvariable=self._fetch_sub,
                  text_color=palette.MUTED, text="").grid(row=1, column=0, sticky="w")
        ctk.CTkButton(head, text="Back to search",
                   command=self._show_search).grid(row=0, column=1, rowspan=2)

        stages = ctk_frame(self.fetch_frame)
        stages.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        for i, name in enumerate(("Check", "Download", "Tag", "Add to library")):
            ctk.CTkLabel(stages, text=name).grid(row=0, column=i)

        prog = ctk_frame(self.fetch_frame)
        prog.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        prog.columnconfigure(0, weight=1)
        self._progress = ctk.CTkProgressBar(prog, mode="determinate")
        self._progress.set(0)
        self._progress.grid(row=0, column=0, sticky="ew")
        self._progress_lbl = tk.StringVar(value="")
        ctk.CTkLabel(prog, textvariable=self._progress_lbl, text="").grid(
            row=0, column=1, padx=(8, 0))
        self.pause_btn = ctk.CTkButton(prog, text="Pause", command=self._toggle_pause)
        self.pause_btn.grid(row=0, column=2, padx=(8, 0))

        ctk.CTkLabel(
            self.fetch_frame,
            text="Safe to pause or quit: finished files stay, "
                 "the current one resumes from where it stopped.",
            text_color=palette.MUTED).grid(row=3, column=0, sticky="w", pady=4)

        cols = ("num", "file", "reader", "time", "size", "state")
        self.file_tree = ttk.Treeview(
            self.fetch_frame, columns=cols, show="headings")
        for col, text, width in (
            ("num", "#", 40), ("file", "File", 260), ("reader", "Reader", 120),
            ("time", "Time", 70), ("size", "Size", 70), ("state", "State", 120),
        ):
            self.file_tree.heading(col, text=text)
            self.file_tree.column(col, width=width, stretch=col == "file")
        self.file_tree.grid(row=4, column=0, sticky="nsew")

        self._stop_frame = ctk_frame(self.fetch_frame)
        self._stop_frame.grid(row=2, column=0, rowspan=2, sticky="nsew")
        self._stop_frame.grid_remove()
        self._stop_title = tk.StringVar()
        self._stop_body = tk.StringVar()
        ctk.CTkLabel(self._stop_frame, textvariable=self._stop_title,
                  font=font(BODY, "bold")).pack(anchor="w", pady=(8, 4))
        ctk.CTkLabel(self._stop_frame, textvariable=self._stop_body,
                  wraplength=520, text="").pack(anchor="w", pady=(0, 8))
        self._stop_btns = ctk_frame(self._stop_frame)
        self._stop_btns.pack(anchor="w")

    def _show_search(self):
        self._mode = "search"
        self.fetch_frame.grid_remove()
        self.search_frame.grid(row=0, column=0, sticky="nsew")

    def _show_fetch(self):
        self._mode = "fetch"
        self.search_frame.grid_remove()
        self.fetch_frame.grid(row=0, column=0, sticky="nsew")
        self._stop_frame.grid_remove()
        self._progress.grid()
        self.pause_btn.grid()

    # -- jobs ---------------------------------------------------------------

    def _cf(self) -> list[str]:
        return chapterfit_prefix()

    def _run_json(self, name: str, argv: list[str], on_obj, on_done=None):
        if self.app.runner.busy:
            return

        def on_line(line: str):
            line = (line or "").strip()
            if not line:
                return
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                self.app.log(line)
                return
            on_obj(obj)

        def done(job, code):
            if on_done:
                on_done(job, code)

        self.app.log(f"$ {' '.join(argv)}")
        self.app.session.set_job(True, name=name, detail=name)
        job = Job(name, argv, on_done=done, on_line=on_line)
        self.app.runner.run(job)

    def _do_search(self):
        title = self.title_var.get().strip()
        if not title:
            self._empty_var.set("Title is required.")
            return
        author = self.author_var.get().strip()
        argv = [*self._cf(), "librivox", "search", "--title", title, "--json"]
        if author:
            argv.extend(["--author", author])
        self._empty_var.set("")
        self._status_var.set("Searching…")
        self.results.delete(*self.results.get_children())
        self._hits.clear()
        self._unfinished_ids.clear()
        self._sync_empty()

        def on_obj(obj):
            self._apply_search(obj, title)

        self._run_json("librivox-search", argv, on_obj)

    def _apply_search(self, obj: dict, query: str):
        if obj.get("event") == "error":
            msg = obj.get("error") or "LibriVox search failed."
            self._empty_var.set(msg)
            self._status_var.set("")
            self.app.log(f"! {msg}")
            self._sync_empty()
            return
        hits = obj.get("hits") or []
        unfinished = obj.get("unfinished") or []
        for hit in hits:
            iid = str(hit["id"])
            self._hits[iid] = hit
            mins = int(round((hit.get("duration") or 0) / 60))
            self.results.insert("", "end", iid=iid, values=(
                hit["id"], hit.get("title") or "", hit.get("author") or "",
                _readers_label(hit.get("readers")), f"{mins} min",
                hit.get("language") or "", hit.get("sections") or 0,
            ))
        if unfinished:
            sep = self.results.insert(
                "", "end", iid="__sep__",
                values=("", "Not downloadable yet — recording unfinished",
                        "", "", "", "", ""),
                tags=("unfinished",))
            self.results.item(sep, tags=("unfinished",))
        for row in unfinished:
            iid = str(row["id"])
            self._unfinished_ids.add(iid)
            self.results.insert(
                "", "end", iid=iid, tags=("unfinished",),
                values=(row["id"], row.get("title") or "",
                        row.get("author") or "", "", "",
                        row.get("language") or "", ""))
        self._status_var.set(
            f"{len(hits)} downloadable · {len(unfinished)} unfinished · "
            "Enter or double-click a row to look at its sections")
        if not hits and not unfinished:
            self._empty_var.set(
                f'Nothing on LibriVox starts with "{query}". '
                "Titles match from the first word.")
            self._status_var.set("")
        self._sync_empty()

    def _arm_fetch(self, kind: str, enabled: bool, **kw) -> None:
        """Search stays the primary until a recording is chosen."""
        self.fetch_btn._kind = kind  # noqa: SLF001
        if kw:
            self.fetch_btn.configure(**kw)
        set_enabled(self.fetch_btn, enabled)

    def _on_result_select(self, _e=None):
        sel = self.results.selection()
        if not sel:
            return
        iid = sel[0]
        if iid == "__sep__" or iid in self._unfinished_ids:
            self.results.selection_remove(iid)
            self._arm_fetch("secondary", False)
            return
        self._load_plan(iid)

    def _on_result_activate(self, _e=None):
        self._on_result_select()

    def _load_plan(self, lv_id: str, set_name: str | None = None):
        self._arm_fetch("secondary", False, text="Fetch into library",
                        command=self._start_fetch)
        self._detail_title.set("Loading plan…")
        self._detail_meta.set("")
        self._detail_order.set("")
        self.section_list.delete(0, "end")
        self._sync_empty()
        argv = [*self._cf(), "librivox", "fetch", str(lv_id),
                "--library", self.app.library_root, "--plan-only", "--json"]
        if set_name:
            argv.extend(["--set", set_name])

        def on_obj(obj):
            self._apply_plan(obj)

        self._run_json("librivox-plan", argv, on_obj)

    def _replan(self):
        sel = self.results.selection()
        if not sel:
            return
        self._load_plan(sel[0], self._set_var.get() or None)

    def _apply_plan(self, obj: dict):
        self._plan = obj
        if obj.get("event") == "refused":
            self._detail_title.set("Cannot fetch")
            self._detail_meta.set(obj.get("detail") or obj.get("reason") or "")
            reason = obj.get("reason")
            if reason == "count_mismatch":
                self._detail_order.set(
                    f"Playlist {obj.get('playlist_count')} · "
                    f"catalog {obj.get('section_count')} — counts differ")
                self._order_lbl.configure(text_color=palette.ERROR)
            else:
                self._detail_order.set("")
            self._arm_fetch("secondary", False)
            return
        self._detail_title.set(obj.get("title") or "")
        readers = []
        for item in obj.get("files") or []:
            for name in item.get("readers") or []:
                if name not in readers:
                    readers.append(name)
        dur = sum(float(f.get("duration") or 0) for f in obj.get("files") or [])
        self._detail_meta.set(
            f"{obj.get('author') or ''} · {_readers_label(readers)} · "
            f"{clock(dur)} · {obj.get('section_count')} sections · "
            f"{obj.get('language') or ''}")
        sets_available = obj.get("sets_available") or [obj.get("set")]
        self.set_combo.configure(values=sets_available)
        self._set_var.set(obj.get("set") or "")
        pc, sc = obj.get("playlist_count"), obj.get("section_count")
        self._detail_order.set(f"Playlist {pc} · catalog {sc}")
        self._order_lbl.configure(
            text_color=palette.ERROR if pc != sc else palette.MUTED)
        dest = obj.get("dest") or ""
        self._detail_path.set(f"Saves to {dest}")
        self.section_list.delete(0, "end")
        for item in obj.get("files") or []:
            self.section_list.insert(
                "end",
                f"{int(item['section_number']):02d}  {item['title']}  "
                f"({_readers_label(item.get('readers'))})")
        self._sync_empty()
        # Already in library?
        meta_path = os.path.join(dest, "librivox.json")
        if os.path.isfile(meta_path):
            try:
                with open(meta_path) as handle:
                    existing = json.load(handle)
            except (OSError, ValueError):
                existing = {}
            if int(existing.get("id") or 0) == int(obj.get("id") or -1):
                self._arm_fetch(
                    "primary", True, text="Open the book",
                    command=lambda d=dest: self._open_book(d))
                return
        self._arm_fetch(
            "primary", True, text="Fetch into library",
            command=self._start_fetch)

    def _open_book(self, dest: str):
        self.app._show_librivox(False)
        self.app.workflow.set_book(dest)
        self.app._fill_library()

    def _start_fetch(self):
        if not self._plan or self._plan.get("event") != "plan":
            return
        if self.app.runner.busy:
            return
        plan = self._plan
        self._fetch_id = str(plan["id"])
        self._fetch_title = plan.get("title") or ""
        self._show_fetch()
        self._fetch_head.set(f"Fetching {self._fetch_title}")
        self._fetch_sub.set(
            f"{plan.get('author')} · id {plan['id']} · "
            f"{plan.get('set')} · {plan.get('section_count')} files")
        self.file_tree.delete(*self.file_tree.get_children())
        for item in plan.get("files") or []:
            iid = str(item["section_number"])
            self.file_tree.insert("", "end", iid=iid, values=(
                f"{int(item['section_number']):02d}",
                os.path.basename(item["dest"]),
                item.get("reader") or _readers_label(item.get("readers")),
                clock(item.get("duration") or 0),
                "",
                "waiting",
            ))
        # Mark library row as downloading
        dest = plan["dest"]
        os.makedirs(dest, exist_ok=True)
        progress = {"fetch": {"done": 0, "total": plan["section_count"]},
                    "id": plan["id"]}
        meta_path = os.path.join(dest, "librivox.json")
        if not os.path.isfile(meta_path):
            with open(meta_path, "w") as handle:
                json.dump(progress, handle)
        self.app._fill_library()

        argv = [*self._cf(), "librivox", "fetch", str(plan["id"]),
                "--library", self.app.library_root, "--json"]
        if self._set_var.get():
            argv.extend(["--set", self._set_var.get()])
        self._paused = False
        self.pause_btn.configure(text="Pause")

        def on_obj(obj):
            self._on_fetch_event(obj)

        def on_done(job, code):
            if job.cancelled or self._paused:
                self._show_stop(
                    "Paused",
                    "Finished files stay. The current file resumes from its .partial.",
                    (("Resume", self._start_fetch),
                     ("Remove partial download", self._remove_partials),
                     ("Back to search", self._show_search)))
                return
            if code == 2:
                return  # refused already shown
            if code != 0:
                self._show_stop(
                    "Network gave up",
                    "Stopped mid-fetch. Finished files stay; the current one resumes.",
                    (("Resume", self._start_fetch),
                     ("Open log", self.app.toggle_log),
                     ("Back to search", self._show_search)),
                    color=palette.WARN)
                return
            self.app._fill_library()
            self._show_stop(
                "Added",
                f"{plan.get('section_count')} chapters in {plan.get('dest')}",
                (("Open the book", lambda: self._open_book(plan["dest"])),
                 ("Get another", self._show_search)),
                color=palette.OK)

        self.app.session.set_job(
            True, name="librivox-fetch",
            detail=f"Fetching 0 of {plan['section_count']}")

        def on_line(line: str):
            line = (line or "").strip()
            if line.startswith("{"):
                try:
                    self._on_fetch_event(json.loads(line))
                except json.JSONDecodeError:
                    self.app.log(line)
            elif line:
                self.app.log(line)

        job = Job("librivox-fetch", argv, on_done=on_done, on_line=on_line)
        self.app.runner.run(job)

    def _on_fetch_event(self, obj: dict):
        event = obj.get("event")
        if event == "refused":
            detail = obj.get("detail") or ""
            if obj.get("reason") == "count_mismatch":
                detail = (
                    f"Playlist {obj.get('playlist_count')} · "
                    f"catalog {obj.get('section_count')}. Nothing was downloaded.")
            self._show_stop(
                "Counts differ" if obj.get("reason") == "count_mismatch"
                else "Refused",
                detail,
                (("Copy details", lambda: self.app.clipboard_clear() or
                  self.app.clipboard_append(json.dumps(obj, indent=2))),
                 ("Back to search", self._show_search)),
                color=palette.ERROR)
            return
        if event == "file":
            idx = obj.get("index")
            total = obj.get("total") or 1
            state = obj.get("state")
            iid = None
            # Match by index order in tree
            children = self.file_tree.get_children()
            if idx and 1 <= idx <= len(children):
                iid = children[idx - 1]
            if iid and self.file_tree.exists(iid):
                vals = list(self.file_tree.item(iid, "values"))
                if state == "downloading":
                    b, t = obj.get("bytes") or 0, obj.get("total_bytes") or 0
                    pct = int(100 * b / t) if t else 0
                    vals[5] = f"{pct}%"
                    if obj.get("resumed_from"):
                        vals[5] += " · resumed"
                elif state == "done":
                    vals[5] = ("✓ done · resumed" if obj.get("resumed_from")
                               else "✓ done")
                    if obj.get("bytes"):
                        vals[4] = f"{obj['bytes'] // 1024} KB"
                elif state == "tagged":
                    vals[5] = "✓ done"
                self.file_tree.item(iid, values=vals)
                self.file_tree.see(iid)
            done = sum(
                1 for c in self.file_tree.get_children()
                if "✓" in str(self.file_tree.item(c, "values")[5]))
            pct = int(100 * done / total) if total else 0
            self._progress.set(pct / 100.0)
            self._progress_lbl.set(f"{done} of {total} files · {pct}%")
            self.app.session.set_job_detail(f"Fetching {done} of {total} · {pct}%")
            self.app.player_bar.show_status(f"Fetching {done} of {total} · {pct}%")
        elif event == "done":
            self._progress.set(1.0)

    def _toggle_pause(self):
        if self._paused:
            self._paused = False
            self.pause_btn.configure(text="Pause")
            self._start_fetch()
            return
        self._paused = True
        self.pause_btn.configure(text="Resume")
        self.app._cancel()

    def _remove_partials(self):
        if not self._plan:
            return
        dest = self._plan.get("dest") or ""
        for root, _dirs, files in os.walk(dest):
            for name in files:
                if name.endswith(".partial"):
                    with contextlib.suppress(OSError):
                        os.remove(os.path.join(root, name))
        self._show_search()

    def _show_stop(self, title: str, body: str, buttons, color: str | None = None):
        self._mode = "stop"
        self._progress.grid_remove()
        self.pause_btn.grid_remove()
        self._stop_frame.grid()
        self._stop_title.set(title)
        self._stop_body.set(body)
        for child in self._stop_btns.winfo_children():
            child.destroy()
        for label, cmd in buttons:
            ctk.CTkButton(self._stop_btns, text=label, command=cmd).pack(
                side="left", padx=(0, 8))
