"""Stage panel views. Mixed into WorkflowPanel so tests keep the same attributes."""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from chaptertools.gui import fonts, palette


class StagePanels:
    """Measure → Finalize panel builders."""

    def _result_line(self, parent, row, colspan=1):
        var = tk.StringVar(value="")
        lbl = ttk.Label(parent, textvariable=var, foreground=palette.MUTED,
                        justify="left")
        lbl.grid(row=row, column=0, columnspan=colspan, sticky="w",
                 pady=(4, 0))
        self._wrap_labels.append(lbl)
        return var

    def _primary(self, parent, row, col, text, cmd):
        b = ttk.Button(parent, text=text, command=cmd)
        b.grid(row=row, column=col, sticky="w", padx=2)
        return b

    def _panel_measure(self, host):
        p = ttk.Frame(host)
        p.columnconfigure(0, weight=1)
        row = ttk.Frame(p)
        row.grid(row=0, column=0, sticky="ew")
        self.measure_btn = self._primary(row, 0, 0, "Run measure",
                                         self.cmd_measure)
        ttk.Label(row, text="analyzes the audio: loudness envelope, timeline "
                            "check, noise calibration, gaps").grid(
            row=0, column=1, sticky="w", padx=8)
        menu = tk.Menu(row, tearoff=0)
        menu.add_command(label="Re-scan", command=self.cmd_rescan)
        more = ttk.Menubutton(row, text="\u22ef", menu=menu, direction="below")
        more.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self._tip(more, "measure this file again, including loudness and noise")
        self.measure_result = self._result_line(p, row=1, colspan=1)
        self._tip(self.measure_btn, "decode the whole file once: envelope, "
                  "timeline check, calibration, and the gap list")
        self.copy_fix_btn = ttk.Button(p, text="Copy fix command",
                                       command=self._copy_header_fix)
        self.copy_fix_btn.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.copy_fix_btn.grid_remove()
        self._tip(self.copy_fix_btn, "copy the ffmpeg command that rewrites "
                  "the header; run it, then Measure the fixed file")
        return p

    def _panel_identify(self, host):
        p = ttk.Frame(host)
        p.columnconfigure(1, weight=1)
        ttk.Label(p, text="Source:").grid(row=0, column=0, sticky="w")
        self.identify_mode = tk.StringVar(value="asin")
        self.identify_value = tk.StringVar()
        radios = ttk.Frame(p)
        radios.grid(row=0, column=1, columnspan=3, sticky="w")
        modes = [("ASIN", "asin", "an Audible ASIN; chapters come from "
                  "Audnexus"),
                 ("JSON file", "json", "a chapters JSON file on disk"),
                 ("Embedded", "emb", "the chapters already in the audio "
                  "file"),
                 ("NFO", "nfo", "the chapter tracks already in album.nfo")]
        self.identify_radios = []
        for text, mode, tip in modes:
            rb = ttk.Radiobutton(radios, text=text, value=mode,
                                 variable=self.identify_mode,
                                 command=self._identify_mode_changed)
            rb.pack(side="left", padx=(4, 8))
            self.identify_radios.append(rb)
            self._tip(rb, tip)
        self.identify_entry = ttk.Entry(p, textvariable=self.identify_value,
                                        width=24)
        self.identify_entry.grid(row=1, column=1, sticky="ew", padx=(4, 4),
                                 pady=(4, 0))
        self.identify_browse = ttk.Button(p, text="Browse…",
                                          command=self._identify_browse)
        self.identify_browse.grid(row=1, column=2, sticky="w", pady=(4, 0))
        self._tip(self.identify_browse, "pick a chapters JSON file")
        self.identify_btn = self._primary(p, 1, 3, "Identify",
                                          self.cmd_identify_primary)
        self.identify_btn.grid_configure(pady=(4, 0))
        ttk.Label(p, text="chooses and verifies the chapter list against "
                          "the audio").grid(row=2, column=0, columnspan=4,
                                            sticky="w", pady=(4, 0))
        self.identify_result = self._result_line(p, row=3, colspan=4)
        self._tip(self.identify_btn, "check the chosen chapter list "
                  "against the real silences before aligning")
        self.asin_frame = ttk.Frame(p)
        self.asin_frame.grid(row=4, column=0, columnspan=4, sticky="ew",
                             pady=(4, 0))
        self.asin_frame.columnconfigure(1, weight=1)
        self.asin_title = tk.StringVar()
        self.asin_author = tk.StringVar()
        self.asin_keywords = tk.StringVar()
        self.asin_region = tk.StringVar(value="us")
        query_fields = (
            ("Title", self.asin_title,
             "title sent to Audible; edit it before Search"),
            ("Author", self.asin_author,
             "author sent to Audible; edit it before Search"),
            ("Keywords", self.asin_keywords,
             "free-text search instead of title and author"),
        )
        for row, (label, var, tip) in enumerate(query_fields):
            ttk.Label(self.asin_frame, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 6))
            entry = ttk.Entry(self.asin_frame, textvariable=var)
            entry.grid(row=row, column=1, columnspan=2, sticky="ew",
                       pady=(2, 0))
            self._tip(entry, tip)
        ttk.Label(self.asin_frame, text="Region").grid(
            row=3, column=0, sticky="w", padx=(0, 6), pady=(2, 0))
        self.asin_region_box = ttk.Combobox(
            self.asin_frame, textvariable=self.asin_region, width=6,
            values=("us", "uk", "ca", "au", "de", "fr", "it", "es", "in", "jp"))
        self.asin_region_box.grid(row=3, column=1, sticky="w", pady=(2, 0))
        self._tip(self.asin_region_box, "Audible catalog region")
        self.asin_search_btn = ttk.Button(self.asin_frame, text="Search",
                                          command=self.cmd_search_asin)
        self.asin_search_btn.grid(row=4, column=0, columnspan=2, sticky="w",
                                  pady=(4, 0))
        self._tip(self.asin_search_btn,
                  "search Audible with the title, author, keywords, and "
                  "region above")
        self._identify_mode_changed()
        return p

    def _panel_align(self, host):
        p = ttk.Frame(host)
        p.columnconfigure(3, weight=1)
        ttk.Label(p, text="Minimum confidence:").grid(row=0, column=0,
                                                      sticky="w")
        self.min_conf = tk.DoubleVar(value=0.5)
        self.min_conf.trace_add("write", lambda *_: self._refresh_soon())
        self.min_conf_spin = ttk.Spinbox(p, from_=0.0, to=1.0, increment=0.05,
                                         width=5, textvariable=self.min_conf)
        self.min_conf_spin.grid(row=0, column=1, sticky="w")
        ttk.Label(p, text="Hints:").grid(row=0, column=2, sticky="ne",
                                        padx=(10, 2))
        self.near_var = tk.StringVar()
        self.hints_box = tk.Text(p, height=3, width=18, wrap="none")
        self.hints_box.grid(row=0, column=3, sticky="ew")
        self._tip(self.hints_box, "one CH=TIME per line, for example 12=3:45:00")
        self.align_btn = self._primary(p, 0, 4, "Align", self.cmd_align)
        ttk.Label(p, text="places every published chapter on a real gap").grid(
            row=1, column=0, columnspan=5, sticky="w")
        self.align_result = self._result_line(p, row=2, colspan=5)
        self._tip(self.align_btn, "place every published chapter on a "
                  "real gap, keeping local reading rate consistent")
        self._tip(self.min_conf_spin, "chapters below this confidence are "
                  "withheld from Finish (shaded amber in the table)")
        return p

    def _panel_resolve(self, host):
        p = ttk.Frame(host, padding=(4, 2))
        summary = ttk.Frame(p)
        summary.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.unresolved_frame = ttk.Frame(summary)
        self.unresolved_frame.pack(side="top", anchor="w", fill="x")
        self.next_var = tk.StringVar(value="")
        self.next_label = ttk.Label(summary, textvariable=self.next_var,
                                    foreground=palette.OK,
                                    font=fonts.derived(self, weight="bold"))
        self.next_label.pack(side="top", anchor="w")

        left = ttk.LabelFrame(p, text="Automatic", padding=4)
        self._resolve_left = left
        left.columnconfigure(4, weight=1)

        def option(row, label, button, cmd, cost_var, status_var, tip):
            ttk.Label(left, text=label).grid(row=row, column=0, sticky="e",
                                             padx=(0, 4))
            b = ttk.Button(left, text=button, command=cmd)
            b.grid(row=row, column=2, sticky="ew", padx=4)
            ttk.Label(left, textvariable=cost_var).grid(row=row, column=3,
                                                        sticky="w")
            ttk.Label(left, textvariable=status_var).grid(
                row=row, column=4, sticky="w", padx=(6, 0))
            self._tip(b, tip)
            return b

        self.hint_var = tk.StringVar()
        ttk.Label(left, text="uses the Hints box").grid(row=0, column=1, sticky="w")
        self.hint_status = tk.StringVar(value="")
        self.hint_btn = option(
            0, "Position hint:", "Align with hint", self._resolve_hint,
            tk.StringVar(value="free"), self.hint_status,
            "re-runs Align aimed at roughly this position (CH=TIME); "
            "free - it is not a pin")
        self.listen_status = tk.StringVar(value="not run")
        self.listen_btn = option(
            1, "Listen:", "Listen", self.cmd_listen,
            tk.StringVar(value="~4 min"), self.listen_status,
            "transcribes ~13 s after each candidate gap (about 4 minutes "
            "for a typical book) and pins the chapter announcements it "
            "hears")
        rng = ttk.Frame(left)
        rng.grid(row=2, column=1, sticky="ew")
        self.sweep_start = tk.StringVar()
        self.sweep_end = tk.StringVar()
        self.sweep_start_entry = ttk.Entry(rng, textvariable=self.sweep_start,
                                           width=9)
        self.sweep_start_entry.pack(side="left")
        ttk.Label(rng, text="–").pack(side="left")
        self.sweep_end_entry = ttk.Entry(rng, textvariable=self.sweep_end,
                                         width=9)
        self.sweep_end_entry.pack(side="left")
        for e in (self.sweep_start_entry, self.sweep_end_entry):
            e.bind("<Key>", self._sweep_edited, add="+")
            self._tip(e, "start and end of the span to sweep (H:MM:SS); "
                         "selecting a chapter fills them until you type")
        self.range_cost = tk.StringVar(value="")
        self.range_status = tk.StringVar(value="")
        self.range_btn = option(
            2, "Sweep range:", "Sweep range", self._sweep_range,
            self.range_cost, self.range_status,
            "reads this span continuously and pins announced chapters - "
            "the targeted fix for one stubborn chapter")
        self.probe_status = tk.StringVar(value="not run")
        self.probe_btn = option(
            3, "Sweep probe:", "Probe 10 min", self._sweep_probe,
            tk.StringVar(value="10 min"), self.probe_status,
            "sweeps 10 minutes from the quarter point to learn whether "
            "chapters are announced at all")
        self.full_cost = tk.StringVar(value="")
        self.swept_status = tk.StringVar(value="not run")
        self.full_btn = option(
            4, "Full sweep:", "Full sweep", self._sweep_full,
            self.full_cost, self.swept_status,
            "sweeps the whole book; already-swept spans are cached and "
            "cost nothing to redo")

        self.pin_time = tk.StringVar(value="0:00:00.000")
        self.pin_ch = tk.StringVar()
        self._pin_autofilled = False
        heard = ttk.Frame(left)
        heard.grid(row=5, column=0, columnspan=5, sticky="ew", pady=(6, 0))
        ttk.Label(heard, text="Heard lines:").pack(side="left")
        self.grep_var = tk.StringVar(value="chapter")
        heard_entry = ttk.Entry(heard, textvariable=self.grep_var, width=16)
        heard_entry.pack(side="left", padx=4)
        self.grep_var.trace_add("write", lambda *_: self._refresh_soon())
        self._tip(heard_entry, "only heard lines matching this regular expression")
        self._resolve_stacked = None
        self._layout_resolve(side_by_side=True)
        return p

    def _layout_resolve(self, side_by_side: bool):
        del side_by_side
        left = self._resolve_left
        left.master.columnconfigure(0, weight=1)
        left.grid(row=1, column=0, sticky="nsew")

    def _build_book_info(self, parent):
        info = ttk.LabelFrame(parent, text="Book info (written at Finish)",
                              padding=6)
        info.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for c in range(4):
            info.columnconfigure(c, weight=1)
        self.meta_vars = {}
        self._meta_entries = {}
        for n, key in enumerate(("title", "author", "album", "year",
                                 "genre", "comment")):
            row, col = divmod(n, 2)
            ttk.Label(info, text=f"{key}:").grid(row=row, column=col * 2,
                                                 sticky="e", padx=(4, 2))
            var = tk.StringVar()
            self.meta_vars[key] = var
            entry = ttk.Entry(info, textvariable=var, width=16)
            entry.grid(row=row, column=col * 2 + 1, sticky="ew", padx=(0, 6), pady=1)
            self._meta_entries[key] = entry
        self.reread_btn = ttk.Button(info, text="Re-read",
                                     command=self._reharvest)
        self.reread_btn.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self._tip(self.reread_btn, "re-read tags and chapters from the file")
        found = ttk.Label(info, textvariable=self.found_var,
                          foreground=palette.MUTED, justify="left")
        found.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self._wrap_labels.append(found)

    def _build_editions(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        cols = ("asin", "title", "author", "narrator", "runtime",
                "chapters", "fit")
        self.asin_tree = ttk.Treeview(parent, columns=cols, show="headings")
        headings = (("asin", "ASIN", 110), ("title", "Title", 180),
                    ("author", "Author", 140), ("narrator", "Narrator", 140),
                    ("runtime", "Runtime", 70), ("chapters", "Chapters", 70),
                    ("fit", "Fit", 90))
        stretch = {"title", "author", "narrator"}
        for col, heading, width in headings:
            self.asin_tree.heading(col, text=heading)
            self.asin_tree.column(col, width=width, stretch=col in stretch)
        self.asin_tree.grid(row=0, column=0, sticky="nsew")
        self.asin_tree.bind("<<TreeviewSelect>>", self._on_asin_select)
        bar = ttk.Frame(parent)
        bar.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.fit_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.fit_var).pack(side="left")
        self.use_edition_btn = ttk.Button(
            bar, text="Use this edition", command=self.cmd_use_edition)
        self.use_edition_btn.pack(side="right")
        self._tip(self.use_edition_btn,
                  "fill empty book info, align this edition, and open Place")

    def _panel_emit(self, host):
        """Finish: one-column write table, plan box, Dry run / Apply."""
        p = ttk.Frame(host)
        p.columnconfigure(0, weight=1)
        p.rowconfigure(3, weight=1)
        self.include_cleanup = tk.BooleanVar(value=False)
        self.embed_var = tk.BooleanVar(value=True)
        self.bitrate_var = tk.StringVar(value="64k")
        self.chapters_card = tk.StringVar(value="0 of 0 placed · 0 withheld · 0 pins")
        self.tags_card = tk.StringVar(value="title · author · album · year")
        self.cleanup_card = tk.StringVar(
            value="Not included — the audio is copied untouched.")
        self.cleanup_option = tk.StringVar(value="")
        self.emit_result = tk.StringVar(value="")
        self.apply_hint = tk.StringVar(value="")
        self._plan_empty = True

        head = ttk.Frame(p)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(head, text="Finish", font=("", 12, "bold")).pack(side="left")
        ttk.Label(
            head,
            text="Writes one new file into the library. "
                 "The first original is always kept.",
            foreground=palette.MUTED).pack(side="left", padx=(10, 0))

        table = ttk.Frame(p, padding=0)
        table.grid(row=1, column=0, sticky="ew")
        table.columnconfigure(1, weight=1)
        # Columns: Write 170 | What goes in stretch | Option 250 | blank 130
        border = tk.Frame(table, bg="#C8C5BE", bd=0)
        border.grid(row=0, column=0, sticky="ew")
        border.columnconfigure(1, weight=1)
        inner = tk.Frame(border, bg="#FFFFFF", bd=1, relief="solid")
        inner.pack(fill="both", expand=True)
        inner.columnconfigure(1, weight=1)
        headers = (("Write", 170), ("What goes in", 0), ("Option", 250), ("", 130))
        for col, (text, width) in enumerate(headers):
            lbl = tk.Label(inner, text=text, bg="#FFFFFF", fg="#5a5a5a",
                           font=("", 9), anchor="w", padx=8, pady=4)
            sticky = "ew" if col == 1 else "w"
            lbl.grid(row=0, column=col, sticky=sticky)
            if width:
                lbl.configure(width=max(8, width // 8))

        # Chapters row
        self.chapters_check = ttk.Checkbutton(inner, text="Chapters",
                                              variable=self.embed_var)
        self.chapters_check.grid(row=1, column=0, sticky="w", padx=8, pady=4)
        tk.Label(inner, textvariable=self.chapters_card, bg="#FFFFFF",
                 anchor="w", padx=8).grid(row=1, column=1, sticky="ew")
        tk.Label(inner, text="Embed in the audio", bg="#FFFFFF",
                 anchor="w", padx=8).grid(row=1, column=2, sticky="w")
        self._edit_place = ttk.Button(
            inner, text="Edit in Place",
            command=lambda: self.show_step("place"))
        self._edit_place.grid(row=1, column=3, sticky="e", padx=8, pady=4)

        # Tags row
        self.tags_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(inner, text="Tags", variable=self.tags_on,
                        state="disabled").grid(
            row=2, column=0, sticky="w", padx=8, pady=4)
        tk.Label(inner, textvariable=self.tags_card, bg="#FFFFFF",
                 anchor="w", padx=8).grid(row=2, column=1, sticky="ew")
        tk.Label(inner, text="also written to album.nfo", bg="#FFFFFF",
                 anchor="w", padx=8).grid(row=2, column=2, sticky="w")
        self._edit_match = ttk.Button(
            inner, text="Edit in Match",
            command=lambda: self.show_step("match"))
        self._edit_match.grid(row=2, column=3, sticky="e", padx=8, pady=4)

        # Audio cleanup row
        ttk.Checkbutton(
            inner, text="Audio cleanup",
            variable=self.include_cleanup,
            command=self._sync_cleanup_choice).grid(
            row=3, column=0, sticky="w", padx=8, pady=4)
        tk.Label(inner, textvariable=self.cleanup_card, bg="#FFFFFF",
                 anchor="w", padx=8).grid(row=3, column=1, sticky="ew")
        opt = ttk.Frame(inner)
        opt.grid(row=3, column=2, sticky="w", padx=8)
        ttk.Label(opt, text="MP3").pack(side="left")
        bitrates = ("48k", "64k", "96k", "128k", "160k", "192k", "256k", "320k")
        self.bitrate_combo = ttk.Combobox(
            opt, textvariable=self.bitrate_var, values=bitrates,
            width=6, state="readonly")
        self.bitrate_combo.pack(side="left", padx=(6, 0))
        self._edit_cleanup = ttk.Button(
            inner, text="Edit cleanup",
            command=lambda: self.show_step("cleanup"))
        self._edit_cleanup.grid(row=3, column=3, sticky="e", padx=8, pady=4)

        # Share Edit button width
        edit_w = 14
        for btn in (self._edit_place, self._edit_match, self._edit_cleanup):
            btn.configure(width=edit_w)

        plan_head = ttk.Frame(p)
        plan_head.grid(row=2, column=0, sticky="ew", pady=(10, 4))
        ttk.Label(plan_head, text="Plan", font=("", 11, "bold")).pack(side="left")
        ttk.Label(plan_head, text="from the last dry run",
                  foreground=palette.MUTED).pack(side="left", padx=(8, 0))

        plan_wrap = tk.Frame(p, bg="#FFFFFF", bd=1, relief="solid")
        plan_wrap.grid(row=3, column=0, sticky="nsew")
        plan_wrap.columnconfigure(0, weight=1)
        plan_wrap.rowconfigure(0, weight=1)
        self._plan_empty_frame = ttk.Frame(plan_wrap)
        self._plan_empty_frame.grid(row=0, column=0, sticky="")
        ttk.Label(self._plan_empty_frame, text="No dry run yet",
                  font=("", 11, "bold")).pack(pady=(24, 4))
        ttk.Label(
            self._plan_empty_frame,
            text="Dry run shows what Apply will write, without changing the library.",
            foreground=palette.MUTED).pack(pady=(0, 8))
        self._plan_empty_btn = ttk.Button(
            self._plan_empty_frame, text="Dry run",
            command=self.cmd_finalize_dry)
        self._plan_empty_btn.pack()
        self.dry_log = tk.Text(
            plan_wrap, wrap="none", state="disabled",
            font=tkfont.nametofont("TkFixedFont", root=self),
            bg="#FFFFFF", relief="flat", borderwidth=0)
        # dry_log shown after first dry run

        foot = ttk.Frame(p)
        foot.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        foot.columnconfigure(0, weight=1)
        self.allow_delete_var = tk.BooleanVar(value=False)
        allow_delete_cb = ttk.Checkbutton(
            foot,
            text="Allow delete-and-re-add in Audiobookshelf "
                 "(clears listening progress)",
            variable=self.allow_delete_var)
        allow_delete_cb.grid(row=0, column=0, columnspan=3, sticky="w")
        self._tip(allow_delete_cb, "last resort: delete the ABS database "
                  "entry and let the library scan re-add it; clears "
                  "listening progress")
        ttk.Label(foot, textvariable=self.apply_hint,
                  foreground=palette.MUTED).grid(
            row=1, column=0, sticky="w", pady=(4, 0))
        self.finalize_btn = ttk.Button(
            foot, text="Dry run", command=self.cmd_finalize_dry)
        self.finalize_btn.grid(row=1, column=1, sticky="e", padx=(8, 4))
        self.apply_btn = ttk.Button(
            foot, text="Apply to library", state=["disabled"],
            command=self.cmd_apply_library)
        self.apply_btn.grid(row=1, column=2, sticky="e")
        self._tip(self.finalize_btn, "preview everything finalize "
                  "would do - nothing is changed")
        self._tip(self.apply_btn, "stage the original away, rename the "
                  "chaptered file into place, write album.nfo, and "
                  "reload Audiobookshelf. The first original is kept.")
        self.finalize_result = tk.StringVar(value="")
        return p

    def _panel_finalize(self, host):
        """Finish is one column in the main pane; inspector stays empty."""
        p = ttk.Frame(host)
        return p

    def _show_plan_log(self):
        """Reveal the dry-run text after the first run."""
        if not getattr(self, "_plan_empty", True):
            return
        self._plan_empty = False
        self._plan_empty_frame.grid_remove()
        self.dry_log.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
