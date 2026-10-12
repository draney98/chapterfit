"""Stage panel views. Mixed into WorkflowPanel so tests keep the same attributes."""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from chaptertools.gui import palette
from chaptertools.gui.theme import ctk_frame, equalize_segments, require_customtkinter

ctk = require_customtkinter()

_IDENTIFY_LABELS = {
    "ASIN": "asin",
    "JSON file": "json",
    "Embedded": "emb",
    "NFO": "nfo",
}


def _btn_width(text: str, *, minimum: int = 90) -> int:
    """Approximate pixel width so CTkButton does not default to 140."""
    return max(minimum, 8 * len(text) + 24)


class StagePanels:
    """Measure → Finalize panel builders."""

    def _result_line(self, parent, row, colspan=1):
        var = tk.StringVar(value="")
        lbl = ctk.CTkLabel(parent, textvariable=var, text_color=palette.MUTED,
                           justify="left", anchor="w", text="", height=18)
        lbl.grid(row=row, column=0, columnspan=colspan, sticky="w",
                 pady=(4, 0))
        self._wrap_labels.append(lbl)
        return var

    def _primary(self, parent, row, col, text, cmd):
        b = ctk.CTkButton(parent, text=text, command=cmd, height=28,
                          width=_btn_width(text))
        b.grid(row=row, column=col, sticky="w", padx=2)
        return b

    def _panel_measure(self, host):
        p = ctk_frame(host, fg_color="transparent", corner_radius=0)
        p.columnconfigure(0, weight=1)
        row = ctk_frame(p, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew")
        self.measure_btn = self._primary(row, 0, 0, "Run measure",
                                         self.cmd_measure)
        ctk.CTkLabel(row, text="analyzes the audio: loudness envelope, timeline "
                               "check, noise calibration, gaps",
                     anchor="w").grid(row=0, column=1, sticky="w", padx=8)
        menu = tk.Menu(row, tearoff=0)
        menu.add_command(label="Re-scan", command=self.cmd_rescan)
        more = ttk.Menubutton(row, text="\u22ef", menu=menu, direction="below")
        more.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self._tip(more, "measure this file again, including loudness and noise")
        self.measure_result = self._result_line(p, row=1, colspan=1)
        self._tip(self.measure_btn, "decode the whole file once: envelope, "
                  "timeline check, calibration, and the gap list")
        self.copy_fix_btn = ctk.CTkButton(
            p, text="Copy fix command", command=self._copy_header_fix, height=28)
        self.copy_fix_btn.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.copy_fix_btn.grid_remove()
        self._tip(self.copy_fix_btn, "copy the ffmpeg command that rewrites "
                  "the header; run it, then Measure the fixed file")
        return p

    def _panel_identify(self, host):
        p = ctk_frame(host, fg_color="transparent", corner_radius=0)
        p.columnconfigure(1, weight=1)
        ctk.CTkLabel(p, text="Source:").grid(row=0, column=0, sticky="w")
        self.identify_mode = tk.StringVar(value="asin")
        self.identify_value = tk.StringVar()
        radios = ctk_frame(p, fg_color="transparent")
        radios.grid(row=0, column=1, columnspan=3, sticky="w")
        self._identify_seg = ctk.CTkSegmentedButton(
            radios, values=list(_IDENTIFY_LABELS),
            command=self._on_identify_seg, height=28, border_width=None,
            dynamic_resizing=False, width=360)
        self._identify_seg.set("ASIN")
        self._identify_seg.pack(side="left")
        equalize_segments(self._identify_seg)
        self.identify_radios = [self._identify_seg]
        self._tip(self._identify_seg,
                  "ASIN, JSON file, Embedded chapters, or album.nfo")
        self.identify_entry = ctk.CTkEntry(
            p, textvariable=self.identify_value, width=220)
        self.identify_entry.grid(row=1, column=1, sticky="ew", padx=(4, 4),
                                 pady=(4, 0))
        self.identify_browse = ctk.CTkButton(
            p, text="Browse…", command=self._identify_browse, width=90, height=28)
        self.identify_browse.grid(row=1, column=2, sticky="w", pady=(4, 0))
        self._tip(self.identify_browse, "pick a chapters JSON file")
        self.identify_btn = self._primary(p, 1, 3, "Identify",
                                          self.cmd_identify_primary)
        self.identify_btn.grid_configure(pady=(4, 0))
        ctk.CTkLabel(p, text="chooses and verifies the chapter list against "
                             "the audio", anchor="w").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.identify_result = self._result_line(p, row=3, colspan=4)
        self._tip(self.identify_btn, "check the chosen chapter list "
                  "against the real silences before aligning")
        self.asin_frame = ctk_frame(p, fg_color="transparent")
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
            ctk.CTkLabel(self.asin_frame, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 6))
            entry = ctk.CTkEntry(self.asin_frame, textvariable=var)
            entry.grid(row=row, column=1, columnspan=2, sticky="ew",
                       pady=(2, 0))
            self._tip(entry, tip)
        ctk.CTkLabel(self.asin_frame, text="Region").grid(
            row=3, column=0, sticky="w", padx=(0, 6), pady=(2, 0))
        self.asin_region_box = ttk.Combobox(
            self.asin_frame, textvariable=self.asin_region, width=6,
            values=("us", "uk", "ca", "au", "de", "fr", "it", "es", "in", "jp"))
        self.asin_region_box.grid(row=3, column=1, sticky="w", pady=(2, 0))
        self._tip(self.asin_region_box, "Audible catalog region")
        self.asin_search_btn = ctk.CTkButton(
            self.asin_frame, text="Search", command=self.cmd_search_asin,
            height=28)
        self.asin_search_btn.grid(row=4, column=0, columnspan=2, sticky="w",
                                  pady=(4, 0))
        self._tip(self.asin_search_btn,
                  "search Audible with the title, author, keywords, and "
                  "region above")
        self._identify_mode_changed()
        return p

    def _on_identify_seg(self, label: str) -> None:
        self.identify_mode.set(_IDENTIFY_LABELS.get(label, "asin"))
        self._identify_mode_changed()

    def _panel_align(self, host):
        p = ctk_frame(host, fg_color="transparent", corner_radius=0)
        p.columnconfigure(3, weight=1)
        ctk.CTkLabel(p, text="Minimum confidence:").grid(row=0, column=0,
                                                         sticky="w")
        self.min_conf = tk.DoubleVar(value=0.5)
        self.min_conf.trace_add("write", lambda *_: self._refresh_soon())
        self.min_conf_spin = ttk.Spinbox(p, from_=0.0, to=1.0, increment=0.05,
                                         width=5, textvariable=self.min_conf)
        self.min_conf_spin.grid(row=0, column=1, sticky="w")
        ctk.CTkLabel(p, text="Hints:").grid(row=0, column=2, sticky="ne",
                                            padx=(10, 2))
        self.near_var = tk.StringVar()
        self.hints_box = tk.Text(p, height=3, width=18, wrap="none")
        self.hints_box.grid(row=0, column=3, sticky="ew")
        self._tip(self.hints_box, "one CH=TIME per line, for example 12=3:45:00")
        self.align_btn = self._primary(p, 0, 4, "Align", self.cmd_align)
        ctk.CTkLabel(p, text="places every published chapter on a real gap",
                     anchor="w").grid(row=1, column=0, columnspan=5, sticky="w")
        self.align_result = self._result_line(p, row=2, colspan=5)
        self._tip(self.align_btn, "place every published chapter on a "
                  "real gap, keeping local reading rate consistent")
        self._tip(self.min_conf_spin, "chapters below this confidence are "
                  "withheld from Finish (shaded amber in the table)")
        return p

    def _panel_resolve(self, host):
        p = ctk_frame(host, fg_color="transparent", corner_radius=0)
        summary = ctk_frame(p, fg_color="transparent")
        summary.grid(row=0, column=0, columnspan=2, sticky="ew", padx=4, pady=2)
        self.unresolved_frame = ctk_frame(summary, fg_color="transparent")
        self.unresolved_frame.pack(side="top", anchor="w", fill="x")
        self.next_var = tk.StringVar(value="")
        self.next_label = ctk.CTkLabel(
            summary, textvariable=self.next_var, text_color=palette.OK,
            font=ctk.CTkFont(weight="bold"), anchor="w")
        self.next_label.pack(side="top", anchor="w")

        left = ctk_frame(p, corner_radius=8, border_width=1,
                         border_color=palette.BORDER, fg_color=palette.RAIL_IDLE)
        ctk.CTkLabel(left, text="Automatic", height=20,
                     font=ctk.CTkFont(weight="bold"), anchor="w").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 2))
        self._resolve_left = left
        left.columnconfigure(0, minsize=110)
        left.columnconfigure(1, weight=1)
        left.columnconfigure(2, weight=1)

        def option(row, label, button, cmd, cost_var, status_var, tip,
                   control=None, pack_side="left"):
            ctk.CTkLabel(left, text=label, height=20, anchor="e").grid(
                row=row, column=0, sticky="e", padx=(8, 6), pady=2)
            cell = control or ctk_frame(left, fg_color="transparent")
            if control is None:
                cell.grid(row=row, column=1, sticky="w", padx=2, pady=2)
            b = ctk.CTkButton(cell, text=button, command=cmd, height=28,
                              width=_btn_width(button))
            b.pack(side=pack_side, anchor="w")
            meta = ctk_frame(left, fg_color="transparent")
            meta.grid(row=row, column=2, sticky="w", padx=(6, 8), pady=2)
            ctk.CTkLabel(meta, textvariable=cost_var, text="", height=18,
                         text_color=palette.MUTED).pack(side="left")
            ctk.CTkLabel(meta, textvariable=status_var, text="", height=18,
                         text_color=palette.MUTED).pack(side="left", padx=(6, 0))
            self._tip(b, tip)
            return b

        self.hint_var = tk.StringVar()
        hint_ctrl = ctk_frame(left, fg_color="transparent")
        hint_ctrl.grid(row=1, column=1, sticky="w", padx=2, pady=2)
        ctk.CTkLabel(hint_ctrl, text="uses the Hints box", height=18,
                     text_color=palette.MUTED).pack(side="left", padx=(0, 8))
        self.hint_status = tk.StringVar(value="")
        self.hint_btn = option(
            1, "Position hint:", "Align with hint", self._resolve_hint,
            tk.StringVar(value="free"), self.hint_status,
            "re-runs Align aimed at roughly this position (CH=TIME); "
            "free - it is not a pin",
            control=hint_ctrl)
        self.listen_status = tk.StringVar(value="not run")
        self.listen_btn = option(
            2, "Listen:", "Listen", self.cmd_listen,
            tk.StringVar(value="~4 min"), self.listen_status,
            "transcribes ~13 s after each candidate gap (about 4 minutes "
            "for a typical book) and pins the chapter announcements it "
            "hears")
        sweep_ctrl = ctk_frame(left, fg_color="transparent")
        sweep_ctrl.grid(row=3, column=1, sticky="w", padx=2, pady=2)
        rng = ctk_frame(sweep_ctrl, fg_color="transparent")
        rng.pack(side="top", anchor="w")
        self.sweep_start = tk.StringVar()
        self.sweep_end = tk.StringVar()
        self.sweep_start_entry = ctk.CTkEntry(
            rng, textvariable=self.sweep_start, width=90, height=28)
        self.sweep_start_entry.pack(side="left")
        ctk.CTkLabel(rng, text="–", height=18).pack(side="left", padx=2)
        self.sweep_end_entry = ctk.CTkEntry(
            rng, textvariable=self.sweep_end, width=90, height=28)
        self.sweep_end_entry.pack(side="left")
        for e in (self.sweep_start_entry, self.sweep_end_entry):
            e.bind("<Key>", self._sweep_edited, add="+")
            self._tip(e, "start and end of the span to sweep (H:MM:SS); "
                         "selecting a chapter fills them until you type")
        self.range_cost = tk.StringVar(value="")
        self.range_status = tk.StringVar(value="")
        self.range_btn = option(
            3, "Sweep range:", "Sweep range", self._sweep_range,
            self.range_cost, self.range_status,
            "reads this span continuously and pins announced chapters - "
            "the targeted fix for one stubborn chapter",
            control=sweep_ctrl, pack_side="top")
        self.probe_status = tk.StringVar(value="not run")
        self.probe_btn = option(
            4, "Sweep probe:", "Probe 10 min", self._sweep_probe,
            tk.StringVar(value="10 min"), self.probe_status,
            "sweeps 10 minutes from the quarter point to learn whether "
            "chapters are announced at all")
        self.full_cost = tk.StringVar(value="")
        self.swept_status = tk.StringVar(value="not run")
        self.full_btn = option(
            5, "Full sweep:", "Full sweep", self._sweep_full,
            self.full_cost, self.swept_status,
            "sweeps the whole book; already-swept spans are cached and "
            "cost nothing to redo")

        self.pin_time = tk.StringVar(value="0:00:00.000")
        self.pin_ch = tk.StringVar()
        self._pin_autofilled = False
        heard = ctk_frame(left, fg_color="transparent")
        heard.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(6, 8), padx=8)
        ctk.CTkLabel(heard, text="Heard lines:").pack(side="left")
        self.grep_var = tk.StringVar(value="chapter")
        heard_entry = ctk.CTkEntry(heard, textvariable=self.grep_var, width=140)
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
        left.grid(row=1, column=0, sticky="nsew", padx=4, pady=2)

    def _build_book_info(self, parent):
        info = ctk_frame(parent, corner_radius=8, border_width=1,
                         border_color=palette.BORDER, fg_color=palette.RAIL_IDLE)
        info.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ctk.CTkLabel(info, text="Book info (written at Finish)", height=20,
                     font=ctk.CTkFont(weight="bold"), anchor="w").grid(
            row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(6, 4))
        for c in range(4):
            info.columnconfigure(c, weight=1)
        self.meta_vars = {}
        self._meta_entries = {}
        for n, key in enumerate(("title", "author", "album", "year",
                                 "genre", "comment")):
            row, col = divmod(n, 2)
            ctk.CTkLabel(info, text=f"{key}:").grid(
                row=row + 1, column=col * 2, sticky="e", padx=(8, 2))
            var = tk.StringVar()
            self.meta_vars[key] = var
            entry = ctk.CTkEntry(info, textvariable=var, width=160)
            entry.grid(row=row + 1, column=col * 2 + 1, sticky="ew",
                       padx=(0, 8), pady=1)
            self._meta_entries[key] = entry
        self.reread_btn = ctk.CTkButton(
            info, text="Re-read", command=self._reharvest, height=28, width=90)
        self.reread_btn.grid(row=4, column=0, columnspan=2, sticky="w",
                             padx=8, pady=(4, 0))
        self._tip(self.reread_btn, "re-read tags and chapters from the file")
        found = ctk.CTkLabel(info, textvariable=self.found_var,
                             text_color=palette.MUTED, justify="left",
                             anchor="w", text="")
        found.grid(row=5, column=0, columnspan=4, sticky="w", padx=8, pady=(4, 8))
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
        bar = ctk_frame(parent, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.fit_var = tk.StringVar(value="")
        ctk.CTkLabel(bar, textvariable=self.fit_var, text="").pack(side="left")
        self.use_edition_btn = ctk.CTkButton(
            bar, text="Use this edition", command=self.cmd_use_edition,
            height=28, width=_btn_width("Use this edition"))
        self.use_edition_btn.pack(side="right")
        self._tip(self.use_edition_btn,
                  "fill empty book info, align this edition, and open Place")

    def _panel_emit(self, host):
        """Finish: one-column write table, plan box, Dry run / Apply."""
        p = ctk_frame(host, fg_color="transparent", corner_radius=0)
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

        head = ctk_frame(p, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ctk.CTkLabel(head, text="Finish",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkLabel(
            head,
            text="Writes one new file into the library. "
                 "The first original is always kept.",
            text_color=palette.MUTED).pack(side="left", padx=(10, 0))

        table = ctk_frame(p, fg_color="transparent")
        table.grid(row=1, column=0, sticky="ew")
        table.columnconfigure(0, weight=1)
        inner = ctk_frame(table, corner_radius=8, border_width=1,
                          border_color=palette.BORDER, fg_color=palette.RAIL_IDLE)
        inner.grid(row=0, column=0, sticky="ew")
        inner.columnconfigure(1, weight=1)
        headers = (("Write", 170), ("What goes in", 0), ("Option", 250), ("", 130))
        for col, (text, width) in enumerate(headers):
            lbl = ctk.CTkLabel(inner, text=text, text_color=palette.MUTED,
                               font=ctk.CTkFont(size=11), anchor="w", height=18)
            sticky = "ew" if col == 1 else "w"
            lbl.grid(row=0, column=col, sticky=sticky, padx=8, pady=4)
            if width:
                lbl.configure(width=max(80, width))

        # Chapters row
        self.chapters_check = ctk.CTkCheckBox(
            inner, text="Chapters", variable=self.embed_var)
        self.chapters_check.grid(row=1, column=0, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(inner, textvariable=self.chapters_card,
                     text_color=palette.TEXT, anchor="w", text="").grid(
            row=1, column=1, sticky="ew", padx=8)
        ctk.CTkLabel(inner, text="Embed in the audio",
                     text_color=palette.TEXT, anchor="w").grid(
            row=1, column=2, sticky="w", padx=8)
        self._edit_place = ctk.CTkButton(
            inner, text="Edit in Place", width=120, height=28,
            command=lambda: self.show_step("place"))
        self._edit_place.grid(row=1, column=3, sticky="e", padx=8, pady=4)

        # Tags row
        self.tags_on = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(inner, text="Tags", variable=self.tags_on,
                        state="disabled").grid(
            row=2, column=0, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(inner, textvariable=self.tags_card,
                     text_color=palette.TEXT, anchor="w", text="").grid(
            row=2, column=1, sticky="ew", padx=8)
        ctk.CTkLabel(inner, text="also written to album.nfo",
                     text_color=palette.TEXT, anchor="w").grid(
            row=2, column=2, sticky="w", padx=8)
        self._edit_match = ctk.CTkButton(
            inner, text="Edit in Match", width=120, height=28,
            command=lambda: self.show_step("match"))
        self._edit_match.grid(row=2, column=3, sticky="e", padx=8, pady=4)

        # Audio cleanup row
        ctk.CTkCheckBox(
            inner, text="Audio cleanup",
            variable=self.include_cleanup,
            command=self._sync_cleanup_choice).grid(
            row=3, column=0, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(inner, textvariable=self.cleanup_card,
                     text_color=palette.TEXT, anchor="w", text="").grid(
            row=3, column=1, sticky="ew", padx=8)
        opt = ctk_frame(inner, fg_color="transparent")
        opt.grid(row=3, column=2, sticky="w", padx=8)
        ctk.CTkLabel(opt, text="MP3").pack(side="left")
        bitrates = ("48k", "64k", "96k", "128k", "160k", "192k", "256k", "320k")
        self.bitrate_combo = ttk.Combobox(
            opt, textvariable=self.bitrate_var, values=bitrates,
            width=6, state="readonly")
        self.bitrate_combo.pack(side="left", padx=(6, 0))
        self._edit_cleanup = ctk.CTkButton(
            inner, text="Edit cleanup", width=120, height=28,
            command=lambda: self.show_step("cleanup"))
        self._edit_cleanup.grid(row=3, column=3, sticky="e", padx=8, pady=4)

        plan_head = ctk_frame(p, fg_color="transparent")
        plan_head.grid(row=2, column=0, sticky="ew", pady=(10, 4))
        ctk.CTkLabel(plan_head, text="Plan",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        ctk.CTkLabel(plan_head, text="from the last dry run",
                     text_color=palette.MUTED).pack(side="left", padx=(8, 0))

        plan_wrap = ctk_frame(p, corner_radius=8, border_width=1,
                              border_color=palette.BORDER, fg_color=palette.FIELD)
        plan_wrap.grid(row=3, column=0, sticky="nsew")
        plan_wrap.columnconfigure(0, weight=1)
        plan_wrap.rowconfigure(0, weight=1)
        self._plan_empty_frame = ctk_frame(plan_wrap, fg_color="transparent")
        self._plan_empty_frame.grid(row=0, column=0, sticky="")
        ctk.CTkLabel(self._plan_empty_frame, text="No dry run yet",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(pady=(24, 4))
        ctk.CTkLabel(
            self._plan_empty_frame,
            text="Dry run shows what Apply will write, without changing the library.",
            text_color=palette.MUTED).pack(pady=(0, 8))
        self._plan_empty_btn = ctk.CTkButton(
            self._plan_empty_frame, text="Dry run", height=28,
            command=self.cmd_finalize_dry)
        self._plan_empty_btn.pack()
        fixed = tkfont.nametofont("TkFixedFont", root=self)
        self.dry_log = ctk.CTkTextbox(
            plan_wrap, wrap="none",
            font=ctk.CTkFont(family=fixed.actual("family"),
                             size=fixed.actual("size")))
        self.dry_log.configure(state="disabled")
        # dry_log shown after first dry run

        foot = ctk_frame(p, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        foot.columnconfigure(0, weight=1)
        self.allow_delete_var = tk.BooleanVar(value=False)
        allow_delete_cb = ctk.CTkCheckBox(
            foot,
            text="Allow delete-and-re-add in Audiobookshelf "
                 "(clears listening progress)",
            variable=self.allow_delete_var)
        allow_delete_cb.grid(row=0, column=0, columnspan=3, sticky="w")
        self._tip(allow_delete_cb, "last resort: delete the ABS database "
                  "entry and let the library scan re-add it; clears "
                  "listening progress")
        ctk.CTkLabel(foot, textvariable=self.apply_hint,
                     text_color=palette.MUTED, anchor="w", text="").grid(
            row=1, column=0, sticky="w", pady=(4, 0))
        self.finalize_btn = ctk.CTkButton(
            foot, text="Dry run", command=self.cmd_finalize_dry, height=28)
        self.finalize_btn.grid(row=1, column=1, sticky="e", padx=(8, 4))
        self.apply_btn = ctk.CTkButton(
            foot, text="Apply to library", state="disabled", height=28,
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
        p = ctk_frame(host, fg_color="transparent", corner_radius=0)
        return p

    def _show_plan_log(self):
        """Reveal the dry-run text after the first run."""
        if not getattr(self, "_plan_empty", True):
            return
        self._plan_empty = False
        self._plan_empty_frame.grid_remove()
        self.dry_log.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
