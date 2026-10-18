"""Stage panel views. Mixed into WorkflowPanel so tests keep the same attributes."""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from chaptertools.gui import palette
from chaptertools.gui.theme import ctk_frame, require_customtkinter

ctk = require_customtkinter()

_IDENTIFY_LABELS = {
    "Search": "search",
    "ASIN": "asin",
    "JSON": "json",
    "Embedded": "emb",
    "NFO": "nfo",
}
_FORM_LABEL_W = 90


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
        from chaptertools.gui.ui_kit import primary
        b = primary(parent, text, command=cmd, width=_btn_width(text))
        b.grid(row=row, column=col, sticky="w", padx=2)
        return b

    def _panel_measure(self, host):
        p = ctk_frame(host, fg_color="transparent", corner_radius=0)
        p.columnconfigure(0, weight=1)
        row = ctk_frame(p, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew")
        self.measure_btn = self._primary(row, 0, 0, "Run measure",
                                         self.cmd_measure)
        ctk.CTkLabel(row, text="Analyzes the audio: loudness envelope, timeline "
                               "check, noise calibration, gaps",
                     anchor="w", justify="left", wraplength=420).grid(
                         row=0, column=1, sticky="w", padx=8)
        from chaptertools.gui.ui_kit import text_button
        rescan = text_button(
            row, "Re-scan", command=self.cmd_rescan, width=_btn_width("Re-scan"))
        rescan.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self._tip(rescan, "measure this file again, including loudness and noise")
        from chaptertools.gui.ui_kit import BODY, PAD, body, font, panel, section
        card = panel(p)
        card.grid(row=1, column=0, sticky="ew", pady=(PAD, 0))
        card.columnconfigure(0, weight=1)
        section(card, "RESULT").grid(
            row=0, column=0, sticky="w", padx=PAD, pady=(PAD, 0))
        self.measure_result = tk.StringVar(value="")
        result = body(card, "")
        result.configure(textvariable=self.measure_result, wraplength=720)
        result.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(PAD // 2, 0))
        from chaptertools.gui.ui_kit import small
        self.duration_note = small(card, "")
        self.duration_note.grid(
            row=2, column=0, sticky="w", padx=PAD, pady=(PAD // 2, 0))
        self.duration_note.grid_remove()
        nxt = ctk.CTkLabel(
            card, text="next: Match", text_color=palette.OK,
            font=font(BODY, "bold"), anchor="w")
        nxt.grid(row=3, column=0, sticky="w", padx=PAD, pady=(PAD // 2, PAD))
        self._tip(self.measure_btn, "decode the whole file once: envelope, "
                  "timeline check, calibration, and the gap list")
        from chaptertools.gui.ui_kit import secondary
        self.copy_fix_btn = secondary(
            p, "Copy fix command", command=self._copy_header_fix,
            width=_btn_width("Copy fix command"))
        self.copy_fix_btn.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self.copy_fix_btn.grid_remove()
        self._tip(self.copy_fix_btn, "copy the ffmpeg command that rewrites "
                  "the header; run it, then Measure the fixed file")
        return p

    def _form_row(self, parent, row, label, widget, *, tip: str = ""):
        from chaptertools.gui.ui_kit import PAD, BODY, font, C
        lbl = ctk.CTkLabel(
            parent, text=label, width=_FORM_LABEL_W, anchor="w",
            font=font(BODY), text_color=C["text"])
        lbl.grid(row=row, column=0, sticky="w",
                 padx=(PAD, PAD), pady=(PAD // 2, 0))
        widget.grid(row=row, column=1, sticky="ew",
                    padx=(0, PAD), pady=(PAD // 2, 0))
        if tip:
            self._tip(widget, tip)
        return lbl

    def _panel_identify(self, host):
        from chaptertools.gui.ui_kit import (
            PAD, BODY, C, SMALL, font, section, secondary, small)
        from chaptertools.sources.abs_match import (
            PROVIDER_FIND_ASIN, provider_choices, provider_labels)

        p = ctk_frame(host, fg_color="transparent", corner_radius=0)
        p.columnconfigure(0, weight=1)
        section(p, "Find the edition").grid(row=0, column=0, sticky="w")

        self.identify_mode = tk.StringVar(value="search")
        self.identify_value = tk.StringVar()
        self.identify_result = tk.StringVar(value="")  # kept for projection API

        radios = ctk_frame(p, fg_color="transparent")
        radios.grid(row=1, column=0, sticky="ew", pady=(PAD, 0))
        self._identify_seg = ctk.CTkSegmentedButton(
            radios, values=list(_IDENTIFY_LABELS),
            command=self._on_identify_seg, height=28, border_width=None,
            dynamic_resizing=True,
            font=font(SMALL), fg_color=C["sunken"],
            selected_color=C["accent_soft"], selected_hover_color=C["accent_soft"],
            unselected_color=C["sunken"], unselected_hover_color=C["border"],
            text_color=C["text"])
        self._identify_seg.set("Search")
        self._identify_seg.pack(side="left", fill="x", expand=True)
        from chaptertools.gui.theme import paint_segment_text
        paint_segment_text(self._identify_seg)
        self.identify_radios = [self._identify_seg]
        self._tip(self._identify_seg,
                  "Search catalogs, enter an ASIN, or use JSON / Embedded / NFO")

        body = ctk_frame(p, fg_color="transparent")
        body.grid(row=2, column=0, sticky="ew", pady=(PAD, 0))
        body.columnconfigure(0, weight=1)

        # --- Search mode ---
        self.asin_frame = ctk_frame(body, fg_color="transparent")
        self.asin_frame.columnconfigure(1, weight=1)
        self.asin_title = tk.StringVar()
        self.asin_author = tk.StringVar()
        self.asin_keywords = tk.StringVar()
        self.asin_region = tk.StringVar(value="us")
        self.asin_provider = tk.StringVar(value=PROVIDER_FIND_ASIN)
        self._asin_provider_labels = provider_labels()
        self._asin_provider_values = provider_choices()
        self._form_row(
            self.asin_frame, 0, "Title",
            ctk.CTkEntry(self.asin_frame, textvariable=self.asin_title,
                         font=font(BODY)),
            tip="title sent to the catalog")
        self._form_row(
            self.asin_frame, 1, "Author",
            ctk.CTkEntry(self.asin_frame, textvariable=self.asin_author,
                         font=font(BODY)),
            tip="author sent to the catalog")
        self._form_row(
            self.asin_frame, 2, "Keywords",
            ctk.CTkEntry(self.asin_frame, textvariable=self.asin_keywords,
                         font=font(BODY)),
            tip="free-text search instead of title and author")
        label_list = [self._asin_provider_labels[v]
                      for v in self._asin_provider_values]
        self.asin_provider_box = ttk.Combobox(
            self.asin_frame, width=22, state="readonly", values=label_list)
        self.asin_provider_box.set(
            self._asin_provider_labels[PROVIDER_FIND_ASIN])
        self.asin_provider_box.bind(
            "<<ComboboxSelected>>", self._on_asin_provider_pick)
        self._form_row(
            self.asin_frame, 3, "Catalog", self.asin_provider_box,
            tip="Audible, one ABS catalog, or all catalogs. ABS needs "
                "ABS_URL / ABS_TOKEN in ~/.config/chapterfit/.env")
        self.asin_region_box = ttk.Combobox(
            self.asin_frame, textvariable=self.asin_region, width=6,
            values=("us", "uk", "ca", "au", "de", "fr", "it", "es", "in", "jp"))
        self._form_row(
            self.asin_frame, 4, "Region", self.asin_region_box,
            tip="Audible catalog region")
        self.asin_search_btn = secondary(
            self.asin_frame, "Search", command=self.cmd_search_asin)
        self.asin_search_btn.grid(row=5, column=0, columnspan=2, sticky="w",
                                  pady=(PAD, 0))
        self._tip(self.asin_search_btn,
                  "search with the title, author, keywords, catalog, and region")

        # --- ASIN / JSON entry row ---
        self._identify_entry_frame = ctk_frame(body, fg_color="transparent")
        self._identify_entry_frame.columnconfigure(0, weight=1)
        self.identify_entry = ctk.CTkEntry(
            self._identify_entry_frame, textvariable=self.identify_value,
            font=font(BODY))
        self.identify_entry.grid(row=0, column=0, sticky="ew")
        self.identify_browse = secondary(
            self._identify_entry_frame, "Browse…",
            command=self._identify_browse, width=90)
        self.identify_browse.grid(row=0, column=1, sticky="w", padx=(PAD, 0))
        self._tip(self.identify_browse, "pick a chapters JSON file")
        self.identify_btn = secondary(
            self._identify_entry_frame, "Check",
            command=self.cmd_identify_primary, width=90)
        self.identify_btn.grid(row=0, column=2, sticky="w", padx=(PAD, 0))
        self._tip(self.identify_btn,
                  "check the chosen chapter list against the silences")

        # --- Embedded / NFO muted line ---
        self._identify_found_frame = ctk_frame(body, fg_color="transparent")
        self._identify_found_frame.columnconfigure(0, weight=1)
        self.identify_found_var = tk.StringVar(value="")
        found_lbl = small(self._identify_found_frame, text="")
        found_lbl.configure(textvariable=self.identify_found_var)
        found_lbl.grid(row=0, column=0, sticky="ew")
        self.identify_found_btn = secondary(
            self._identify_found_frame, "Check",
            command=self.cmd_identify_primary, width=90)
        self.identify_found_btn.grid(row=1, column=0, sticky="w", pady=(PAD, 0))

        self._identify_mode_changed()
        return p

    def _on_asin_provider_pick(self, _event=None):
        label = self.asin_provider_box.get()
        for value, text in self._asin_provider_labels.items():
            if text == label:
                self.asin_provider.set(value)
                return
        self.asin_provider.set("find-asin")

    def _on_identify_seg(self, label: str) -> None:
        self.identify_mode.set(_IDENTIFY_LABELS.get(label, "search"))
        self._identify_mode_changed()

    def _panel_align(self, host):
        from chaptertools.gui.ui_kit import BODY, font
        p = ctk_frame(host, fg_color="transparent", corner_radius=0)
        p.columnconfigure(1, weight=1)
        ctk.CTkLabel(p, text="Minimum confidence", width=90, anchor="w",
                     font=font(BODY)).grid(row=0, column=0, sticky="w")
        self.min_conf = tk.DoubleVar(value=0.5)
        self.min_conf.trace_add("write", lambda *_: self._refresh_soon())
        self.min_conf_spin = ttk.Spinbox(p, from_=0.0, to=1.0, increment=0.05,
                                         width=5, textvariable=self.min_conf)
        self.min_conf_spin.grid(row=0, column=1, sticky="w")
        self.align_btn = self._primary(p, 0, 2, "Align", self.cmd_align)
        ctk.CTkLabel(p, text="Hints", width=90, anchor="nw",
                     font=font(BODY)).grid(row=1, column=0, sticky="nw", pady=(6, 0))
        self.near_var = tk.StringVar()
        self.hints_box = tk.Text(p, height=3, width=18, wrap="word")
        self.hints_box.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(6, 0))
        self._tip(self.hints_box, "one CH=TIME per line, for example 12=3:45:00")
        self.align_result = self._result_line(p, row=2, colspan=3)
        self._tip(self.align_btn, "place every published chapter on a "
                  "real gap, keeping local reading rate consistent")
        self._tip(self.min_conf_spin, "chapters below this confidence are "
                  "withheld from Finish (shaded amber in the table)")
        return p

    def _panel_resolve(self, host):
        p = ctk_frame(host, fg_color="transparent", corner_radius=0)
        p.rowconfigure(0, weight=1)
        p.columnconfigure(0, weight=1)
        self._resolve_scroll = ctk.CTkScrollableFrame(
            p, fg_color="transparent", corner_radius=0, height=120)
        self._resolve_scroll.grid(row=0, column=0, sticky="nsew")
        summary = ctk_frame(self._resolve_scroll, fg_color="transparent")
        summary.pack(fill="x", padx=4, pady=2)
        self.unresolved_frame = ctk_frame(summary, fg_color="transparent")
        self.unresolved_frame.pack(side="top", anchor="w", fill="x")
        self.next_var = tk.StringVar(value="")
        from chaptertools.gui.ui_kit import BODY, font, text_button
        row = ctk_frame(summary, fg_color="transparent")
        row.pack(side="top", anchor="w")
        self.next_label = ctk.CTkLabel(
            row, text="next:", text_color=palette.OK,
            font=font(BODY, "bold"), anchor="w")
        self.next_label.pack(side="left")
        self._next_jump = text_button(
            row, "", command=self._jump_next, width=140)
        self._next_jump.pack(side="left", padx=(4, 0))
        inner = getattr(self._resolve_scroll, "_scrollable_frame", None)
        if inner is not None:
            inner.bind(
                "<Configure>",
                lambda _e: self._fit_match_scroll(frame=self._resolve_scroll),
                add="+")
        left = ctk_frame(self._resolve_scroll, corner_radius=8, border_width=1,
                         border_color=palette.BORDER, fg_color=palette.RAIL_IDLE)
        ctk.CTkLabel(left, text="Automatic", height=20,
                     font=font(BODY, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(6, 2))
        self._resolve_left = left
        left.columnconfigure(0, minsize=110)
        left.columnconfigure(2, minsize=130)
        left.columnconfigure(3, weight=1)

        def option(row, label, button, cmd, cost_var, status_var, tip,
                   control=None, pack_side="left"):
            del pack_side
            ctk.CTkLabel(left, text=label.rstrip(":"), height=20, anchor="e").grid(
                row=row, column=0, sticky="e", padx=(8, 6), pady=2)
            if control is not None:
                control.grid(row=row, column=1, sticky="w", padx=2, pady=2)
            from chaptertools.gui.ui_kit import secondary
            b = secondary(left, button, command=cmd, width=_btn_width(button))
            b.grid(row=row, column=2, sticky="w", padx=2, pady=2)
            shown = tk.StringVar()

            def _sync(*_a):
                parts = [cost_var.get(), status_var.get()]
                shown.set(" · ".join(part for part in parts if part))

            cost_var.trace_add("write", _sync)
            status_var.trace_add("write", _sync)
            _sync()
            ctk.CTkLabel(left, textvariable=shown, text="", anchor="e",
                         text_color=palette.MUTED).grid(
                row=row, column=3, sticky="e", padx=(6, 8), pady=2)
            self._tip(b, tip)
            return b

        self.hint_var = tk.StringVar()
        hint_ctrl = ctk_frame(left, fg_color="transparent")
        hint_ctrl.grid(row=1, column=1, sticky="w", padx=2, pady=2)
        ctk.CTkLabel(hint_ctrl, text="Uses the Hints box", height=18,
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
            rng, textvariable=self.sweep_start, width=90, height=28,
            placeholder_text="0:00:00")
        self.sweep_start_entry.pack(side="left")
        ctk.CTkLabel(rng, text="–", height=18).pack(side="left", padx=2)
        self.sweep_end_entry = ctk.CTkEntry(
            rng, textvariable=self.sweep_end, width=90, height=28,
            placeholder_text="0:00:00")
        self.sweep_end_entry.pack(side="left")
        for e in (self.sweep_start_entry, self.sweep_end_entry):
            e.bind("<Key>", self._sweep_edited, add="+")
            self._tip(e, "start and end of the span to sweep (H:MM:SS); "
                         "selecting a chapter fills them until you type")
        self.range_cost = tk.StringVar(value="")
        self.range_status = tk.StringVar(value="not run")
        self.range_btn = option(
            3, "Sweep range:", "Sweep range", self._sweep_range,
            self.range_cost, self.range_status,
            "reads this span continuously and pins announced chapters - "
            "the targeted fix for one stubborn chapter",
            control=sweep_ctrl)
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
        ctk.CTkLabel(heard, text="Heard lines").pack(side="left")
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
        left.pack(fill="x", padx=4, pady=2)

    def _build_book_info(self, parent):
        from chaptertools.gui.ui_kit import (
            PAD, BODY, C, font, panel, section, secondary, small, text_button)

        info = panel(parent)
        info.grid(row=1, column=0, sticky="ew", pady=(PAD * 2, 0))
        info.columnconfigure(1, weight=1)
        section(info, "Book info").grid(
            row=0, column=0, columnspan=2, sticky="w",
            padx=PAD, pady=(PAD, PAD // 2))
        self.meta_vars = {}
        self._meta_entries = {}
        self._meta_conflict = {}
        keys = ("title", "author", "narrator", "album", "year",
                "genre", "comment")
        for n, key in enumerate(keys):
            row = 1 + n * 2
            label = key[:1].upper() + key[1:]
            var = tk.StringVar()
            self.meta_vars[key] = var
            entry = ctk.CTkEntry(info, textvariable=var, font=font(BODY))
            self._form_row(info, row, label, entry)
            self._meta_entries[key] = entry
            conflict = ctk_frame(info, fg_color="transparent")
            conflict.grid(row=row + 1, column=1, sticky="ew", padx=0)
            conflict.grid_remove()
            note = small(conflict, text="")
            note.pack(side="left", fill="x", expand=True)
            use = text_button(conflict, "Use")
            use.pack(side="right")
            self._meta_conflict[key] = {
                "frame": conflict, "note": note, "use": use}

        foot = ctk_frame(info, fg_color="transparent")
        foot.grid(row=1 + len(keys) * 2, column=0, columnspan=2,
                  sticky="ew", padx=PAD, pady=(PAD, PAD))
        foot.columnconfigure(0, weight=1)
        found = small(foot, text="")
        found.configure(textvariable=self.found_var)
        found.grid(row=0, column=0, sticky="ew")
        self._wrap_labels.append(found)
        self.reread_btn = text_button(
            foot, "Re-read from file", command=self._reharvest)
        self.reread_btn.grid(row=1, column=0, sticky="w", pady=(PAD // 2, 0))
        self._tip(self.reread_btn, "re-read tags and chapters from the file")

    def _build_editions(self, parent):
        from chaptertools.gui.ui_kit import (
            PAD, BODY, C, FONT, SMALL, TITLE, font, panel, primary,
            secondary, section, small)

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=0)
        head = ctk_frame(parent, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, PAD))
        head.columnconfigure(1, weight=1)
        section(head, "Editions").grid(row=0, column=0, sticky="w")
        self.editions_count_var = tk.StringVar(value="")
        count_lbl = small(head, text="")
        count_lbl.configure(textvariable=self.editions_count_var)
        count_lbl.grid(row=0, column=1, sticky="w", padx=(PAD, 0))

        cols = ("marker", "asin", "title", "narrator", "length", "ch", "fit")
        table_card = panel(parent)
        table_card.grid(row=1, column=0, sticky="ew")
        table_card.columnconfigure(0, weight=1)
        style = ttk.Style(parent)
        style.configure("Edition.Treeview", font=(FONT, BODY))
        style.map(
            "Edition.Treeview",
            background=[("selected", C["accent_soft"])],
            foreground=[("selected", C["text"])])
        self.asin_tree = ttk.Treeview(
            table_card, columns=cols, show="headings", height=8,
            style="Edition.Treeview")
        headings = (
            ("marker", "", 36, "center"),
            ("asin", "ASIN", 150, "w"),
            ("title", "Title", 180, "w"),
            ("narrator", "Narrator", 170, "w"),
            ("length", "Length", 80, "e"),
            ("ch", "Ch", 48, "e"),
            ("fit", "Fit", 130, "w"),
        )
        for col, heading, width, anchor in headings:
            self.asin_tree.heading(col, text=heading)
            self.asin_tree.column(
                col, width=width, minwidth=width,
                stretch=(col == "title"), anchor=anchor)
        self.asin_tree.tag_configure("divider", foreground=C["muted"])
        self.asin_tree.grid(row=0, column=0, sticky="ew", padx=1, pady=1)
        self.asin_tree.bind("<<TreeviewSelect>>", self._on_asin_select)

        card = panel(parent)
        card.grid(row=2, column=0, sticky="ew", pady=(PAD, 0))
        card.columnconfigure(0, weight=1)
        self.verdict_card = card
        self.verdict_headline_var = tk.StringVar(value="")
        self.verdict_reason_var = tk.StringVar(value="")
        self.verdict_numbers_var = tk.StringVar(value="")
        self.verdict_headline = ctk.CTkLabel(
            card, textvariable=self.verdict_headline_var,
            font=font(TITLE, "bold"), text_color=C["text"], anchor="w")
        self.verdict_headline.grid(
            row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, PAD // 2))
        self.verdict_reason = ctk.CTkLabel(
            card, textvariable=self.verdict_reason_var,
            font=font(BODY), text_color=C["text"], anchor="w",
            justify="left", wraplength=520)
        self.verdict_reason.grid(
            row=1, column=0, sticky="ew", padx=PAD)
        self.verdict_numbers = ctk.CTkLabel(
            card, textvariable=self.verdict_numbers_var,
            font=font(SMALL, mono=True), text_color=C["muted"], anchor="w")
        self.verdict_numbers.grid(
            row=2, column=0, sticky="ew", padx=PAD, pady=(PAD // 2, 0))
        btns = ctk_frame(card, fg_color="transparent")
        btns.grid(row=3, column=0, sticky="ew", padx=PAD, pady=PAD)
        self.use_edition_btn = primary(
            btns, "Use this edition", command=self.cmd_use_edition)
        self.use_edition_btn.pack(side="left")
        self._tip(self.use_edition_btn,
                  "fill empty book info, align this edition, and open Place")
        self.use_anyway_btn = secondary(
            btns, "Use anyway…", command=self.cmd_use_edition_anyway)
        self.use_anyway_btn.pack(side="left", padx=(PAD, 0))
        self.use_anyway_btn.pack_forget()
        self._tip(self.use_anyway_btn,
                  "use this edition even though it does not fit")
        self._edition_in_use = small(btns, "● In use")
        self._edition_in_use.pack_forget()
        # Kept so older refresh paths that set fit_var do not crash.
        self.fit_var = tk.StringVar(value="")

    def _panel_emit(self, host):
        """Finish: one-column write table, plan box, Dry run / Apply."""
        from chaptertools.gui.ui_kit import BODY, SMALL, font

        def _fit_label(lbl):
            def _wrap(_event=None):
                width = lbl.winfo_width()
                if width < 40:
                    return
                lbl.configure(wraplength=width)
                for child in lbl.winfo_children():
                    try:
                        child.configure(wraplength=width)
                    except tk.TclError:
                        pass
            lbl.bind("<Configure>", _wrap)
            lbl.after_idle(_wrap)
        p = ctk_frame(host, fg_color="transparent", corner_radius=0)
        p.columnconfigure(0, weight=1)
        p.rowconfigure(3, weight=0)
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
                     font=font(BODY, "bold")).pack(side="left")
        note = ctk.CTkLabel(
            head,
            text="Writes one new file into the library. "
                 "The first original is always kept.",
            text_color=palette.MUTED, anchor="w", justify="left",
            wraplength=360)
        note.pack(side="left", fill="x", expand=True, padx=(10, 0))

        def _wrap_finish_note(_event=None):
            width = note.winfo_width()
            if width > 40:
                note.configure(wraplength=width)

        note.bind("<Configure>", _wrap_finish_note)

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
                               font=font(SMALL), anchor="w", height=18)
            sticky = "ew" if col == 1 else "w"
            lbl.grid(row=0, column=col, sticky=sticky, padx=8, pady=4)
            if width:
                lbl.configure(width=max(80, width))

        # Chapters row
        self.chapters_check = ctk.CTkCheckBox(
            inner, text="Chapters", variable=self.embed_var)
        self.chapters_check.grid(row=1, column=0, sticky="w", padx=8, pady=4)
        chapters_lbl = ctk.CTkLabel(inner, textvariable=self.chapters_card,
                     text_color=palette.TEXT, anchor="w", justify="left",
                     text="", wraplength=200)
        chapters_lbl.grid(row=1, column=1, sticky="ew", padx=8)
        _fit_label(chapters_lbl)
        ctk.CTkLabel(inner, text="Embed in the audio",
                     text_color=palette.TEXT, anchor="w").grid(
            row=1, column=2, sticky="w", padx=8)
        from chaptertools.gui.ui_kit import text_button
        self._edit_place = text_button(
            inner, "Change…", command=lambda: self.show_step("place"),
            width=_btn_width("Change…"))
        self._edit_place.grid(row=1, column=3, sticky="e", padx=8, pady=4)

        # Tags row
        self.tags_on = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(inner, text="Tags", variable=self.tags_on,
                        state="disabled").grid(
            row=2, column=0, sticky="w", padx=8, pady=4)
        tags_lbl = ctk.CTkLabel(inner, textvariable=self.tags_card,
                     text_color=palette.TEXT, anchor="w", justify="left",
                     text="", wraplength=200)
        tags_lbl.grid(row=2, column=1, sticky="ew", padx=8)
        _fit_label(tags_lbl)
        ctk.CTkLabel(inner, text="Also written to album.nfo",
                     text_color=palette.TEXT, anchor="w").grid(
            row=2, column=2, sticky="w", padx=8)
        self._edit_match = text_button(
            inner, "Change…", command=lambda: self.show_step("match"),
            width=_btn_width("Change…"))
        self._edit_match.grid(row=2, column=3, sticky="e", padx=8, pady=4)

        # Audio cleanup row
        ctk.CTkCheckBox(
            inner, text="Audio cleanup",
            variable=self.include_cleanup,
            command=self._sync_cleanup_choice).grid(
            row=3, column=0, sticky="w", padx=8, pady=4)
        cleanup_lbl = ctk.CTkLabel(inner, textvariable=self.cleanup_card,
                     text_color=palette.TEXT, anchor="w", justify="left",
                     text="", wraplength=200)
        cleanup_lbl.grid(row=3, column=1, sticky="ew", padx=8)
        _fit_label(cleanup_lbl)
        opt = ctk_frame(inner, fg_color="transparent")
        opt.grid(row=3, column=2, sticky="w", padx=8)
        ctk.CTkLabel(opt, text="MP3").pack(side="left")
        bitrates = ("48k", "64k", "96k", "128k", "160k", "192k", "256k", "320k")
        from chaptertools.gui.ui_kit import option_menu
        self.bitrate_combo = option_menu(
            opt, self.bitrate_var, bitrates, width=90)
        self.bitrate_combo.pack(side="left", padx=(6, 0))
        self._edit_cleanup = text_button(
            inner, "Change…", command=lambda: self.show_step("cleanup"),
            width=_btn_width("Change…"))
        self._edit_cleanup.grid(row=3, column=3, sticky="e", padx=8, pady=4)

        plan_head = ctk_frame(p, fg_color="transparent")
        plan_head.grid(row=2, column=0, sticky="ew", pady=(10, 4))
        ctk.CTkLabel(plan_head, text="Plan",
                     font=font(SMALL, "bold")).pack(side="left")
        ctk.CTkLabel(plan_head, text="From the last dry run",
                     text_color=palette.MUTED).pack(side="left", padx=(8, 0))

        plan_wrap = ctk_frame(p, corner_radius=8, border_width=1,
                              border_color=palette.BORDER, fg_color=palette.FIELD)
        plan_wrap.grid(row=3, column=0, sticky="nsew")
        plan_wrap.columnconfigure(0, weight=1)
        plan_wrap.rowconfigure(0, weight=1)
        from chaptertools.gui.ui_kit import body
        self._plan_empty_frame = ctk_frame(plan_wrap, fg_color="transparent")
        self._plan_empty_frame.grid(row=0, column=0, sticky="ew")
        body(
            self._plan_empty_frame,
            "No dry run yet. A dry run shows what Apply will write, "
            "without changing the library.",
        ).pack(anchor="w", padx=8, pady=8)
        fixed = tkfont.nametofont("TkFixedFont", root=self)
        self.dry_log = ctk.CTkTextbox(
            plan_wrap, wrap="none", height=160,
            fg_color=palette.WINDOW, text_color=palette.TEXT,
            font=ctk.CTkFont(family=fixed.actual("family"),
                             size=fixed.actual("size")))
        self.dry_log.configure(state="disabled")
        # dry_log shown after first dry run

        foot = ctk_frame(p, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        foot.columnconfigure(0, weight=1)
        self.allow_delete_var = tk.BooleanVar(value=False)
        from chaptertools.gui.ui_kit import banner, checkbox, small
        warn = banner(foot, "warn")
        warn.grid(row=0, column=0, columnspan=3, sticky="ew")
        allow_delete_cb = checkbox(
            warn, "Allow delete-and-re-add in Audiobookshelf",
            variable=self.allow_delete_var)
        allow_delete_cb.pack(anchor="w", padx=8, pady=(6, 0))
        small(warn, "This clears listening progress.").pack(
            anchor="w", padx=8, pady=(0, 6))
        self._tip(allow_delete_cb, "last resort: delete the ABS database "
                  "entry and let the library scan re-add it; clears "
                  "listening progress")
        ctk.CTkLabel(foot, textvariable=self.apply_hint,
                     text_color=palette.MUTED, anchor="w", text="").grid(
            row=1, column=0, sticky="w", pady=(4, 0))
        from chaptertools.gui.theme import set_enabled
        from chaptertools.gui.ui_kit import primary, secondary
        self.finalize_btn = secondary(
            foot, "Dry run", command=self.cmd_finalize_dry,
            width=_btn_width("Dry run"))
        self.finalize_btn.grid(row=1, column=1, sticky="e", padx=(8, 4))
        self.apply_btn = primary(
            foot, "Apply to library", command=self.cmd_apply_library,
            width=_btn_width("Apply to library"))
        set_enabled(self.apply_btn, False)
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
