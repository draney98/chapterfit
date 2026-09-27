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
        self.asin_search_btn.grid(row=3, column=2, sticky="w", padx=(8, 0),
                                  pady=(2, 0))
        self._tip(self.asin_search_btn,
                  "search Audible with the title, author, keywords, and "
                  "region above")
        cols = ("asin", "title", "author", "narrator", "runtime", "score")
        self.asin_tree = ttk.Treeview(self.asin_frame, columns=cols,
                                      show="headings", height=4)
        headings = (("asin", "ASIN", 110), ("title", "Title", 180),
                    ("author", "Author", 140), ("narrator", "Narrator", 160),
                    ("runtime", "Runtime", 70), ("score", "Score", 50))
        stretch = {"title", "author", "narrator"}
        for col, heading, width in headings:
            self.asin_tree.heading(col, text=heading)
            self.asin_tree.column(col, width=width, stretch=col in stretch)
        self.asin_tree.grid(row=4, column=0, columnspan=3, sticky="ew",
                            pady=(4, 0))
        self.asin_tree.bind("<<TreeviewSelect>>", self._on_asin_select)
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
        ttk.Label(p, text="Near (CH=TIME):").grid(row=0, column=2, sticky="e",
                                                  padx=(10, 2))
        self.near_var = tk.StringVar()
        self.near_entry = ttk.Entry(p, textvariable=self.near_var, width=12)
        self.near_entry.grid(row=0, column=3, sticky="w")
        self.align_btn = self._primary(p, 0, 4, "Align", self.cmd_align)
        ttk.Label(p, text="places every published chapter on a real gap").grid(
            row=1, column=0, columnspan=5, sticky="w")
        self.align_result = self._result_line(p, row=2, colspan=5)
        self._tip(self.align_btn, "place every published chapter on a "
                  "real gap, keeping local reading rate consistent")
        self._tip(self.min_conf_spin, "chapters below this confidence are "
                  "withheld from Emit (shaded amber in the table)")
        self._tip(self.near_entry, "optional CH=TIME hint, e.g. 12=3:45:00, "
                  "that aims the alignment near a known position")
        return p

    def _panel_resolve(self, host):
        p = ttk.Frame(host, padding=(4, 2))
        summary = ttk.Frame(p)
        summary.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.unresolved_frame = ttk.Frame(summary)
        self.unresolved_frame.pack(side="left")
        self.next_var = tk.StringVar(value="")
        self.next_label = ttk.Label(summary, textvariable=self.next_var,
                                    foreground=palette.OK,
                                    font=fonts.derived(self, weight="bold"))
        self.next_label.pack(side="right")

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
        self.hint_entry = ttk.Entry(left, textvariable=self.hint_var, width=12)
        self.hint_entry.grid(row=0, column=1, sticky="ew")
        self._tip(self.hint_entry, "CH=TIME, e.g. 12=3:45:00")
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

        right = ttk.LabelFrame(p, text="Pin by ear", padding=4)
        self._resolve_right = right
        right.columnconfigure(0, weight=1)
        what = ttk.Frame(right)
        what.grid(row=0, column=0, sticky="w")
        self.byear_title = tk.StringVar(value="Select a chapter in the table")
        ttk.Label(what, textvariable=self.byear_title,
                  font=fonts.derived(self, weight="bold")).pack(side="left")
        self.byear_info = tk.StringVar(value="")
        ttk.Label(what, textvariable=self.byear_info).pack(
            side="left", padx=(8, 0))
        transport = ttk.Frame(right)
        transport.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.seek_btns = []
        ttk.Style(self).configure("Transport.TButton", width=0)

        def seek(delta):
            b = ttk.Button(transport, text=f"{delta:+d} s",
                           style="Transport.TButton",
                           command=lambda d=delta: self._key_seek(d),
                           state=["disabled"])
            b.pack(side="left", padx=1)
            self.seek_btns.append(b)
            self._tip(b, f"skip {'back' if delta < 0 else 'forward'} "
                         f"{abs(delta)} s ({'Shift+' if abs(delta) == 10 else ''}"
                         f"{'←' if delta < 0 else '→'} in the table)")

        seek(-10)
        seek(-5)
        self.play_btn = ttk.Button(transport, text="▶ Play",
                                   command=self._key_playpause,
                                   state=["disabled"])
        self.play_btn.pack(side="left", padx=1)
        self._tip(self.play_btn, "play from the selected chapter "
                  "(double-click a row or Space does the same); "
                  "pauses/resumes while playing")
        seek(5)
        seek(10)
        self.stop_btn = ttk.Button(transport, text="■ Stop playback",
                                   command=self.app.stop_audio,
                                   state=["disabled"])
        self.stop_btn.pack(side="left", padx=(6, 0))
        self._tip(self.stop_btn, "stop playback")
        ttk.Label(transport, text="Playback:").pack(side="left",
                                                    padx=(12, 0))
        self.playback_var = tk.StringVar(value="0:00:00.000")
        ttk.Label(transport, textvariable=self.playback_var,
                  font=tkfont.nametofont("TkFixedFont", root=self)).pack(
            side="left", padx=(4, 0))
        time_row = ttk.Frame(right)
        time_row.grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Label(time_row, text="Time (H:MM:SS):").pack(side="left")
        self.pin_time = tk.StringVar(value="0:00:00.000")
        pin_time_entry = ttk.Entry(time_row, textvariable=self.pin_time,
                                   width=12)
        pin_time_entry.pack(side="left", padx=(4, 6))
        pin_time_entry.bind("<Return>",
                            lambda e: self.play_from_time() or "break")
        self._tip(pin_time_entry, "the time Pin will mark and Play from "
                  "time will start at (H:MM:SS); Enter plays from it")
        self._set_btn = ttk.Button(time_row, text="Set from playback",
                                   command=self.set_from_playback,
                                   state=["disabled"])
        self._set_btn.pack(side="left", padx=(0, 4))
        self._tip(self._set_btn, "copy the current (frozen) playback "
                  "clock into the time field")
        self.playfrom_btn = ttk.Button(time_row, text="▶ Play from time",
                                       command=self.play_from_time,
                                       state=["disabled"])
        self.playfrom_btn.pack(side="left")
        self._tip(self.playfrom_btn, "start playback (no time limit) at "
                  "the time in the field")
        self.pin_ch = tk.StringVar()
        self._pin_autofilled = False
        pin_row = ttk.Frame(right)
        pin_row.grid(row=3, column=0, sticky="w", pady=(4, 0))
        self.pin_btn = ttk.Button(pin_row, text="Pin", command=self.cmd_pin)
        self.pin_btn.pack(side="left", padx=(0, 4))
        self._tip(self.pin_btn, "fix the selected chapter at the time in "
                  "the field, then re-align (Enter in the table)")
        self.unpin_btn = ttk.Button(pin_row, text="Remove pin",
                                    command=self.cmd_unpin)
        self.unpin_btn.pack(side="left")
        self._tip(self.unpin_btn, "drop this chapter's pin and re-align "
                  "(Delete in the table)")
        self._resolve_stacked = None
        self._layout_resolve(side_by_side=True)
        return p

    def _layout_resolve(self, side_by_side: bool):
        if self._resolve_stacked is (not side_by_side):
            return
        self._resolve_stacked = not side_by_side
        left, right = self._resolve_left, self._resolve_right
        p = left.master
        if side_by_side:
            p.columnconfigure(0, weight=3)
            p.columnconfigure(1, weight=2)
            left.grid(row=1, column=0, columnspan=1, sticky="nsew")
            right.grid(row=1, column=1, columnspan=1, sticky="nsew",
                       padx=(8, 0), pady=0)
        else:
            p.columnconfigure(0, weight=1)
            p.columnconfigure(1, weight=0)
            left.grid(row=1, column=0, columnspan=2, sticky="nsew")
            right.grid(row=2, column=0, columnspan=2, sticky="nsew",
                       padx=0, pady=(6, 0))

    def _panel_emit(self, host):
        p = ttk.Frame(host)
        info = ttk.LabelFrame(p, text="Book info (written to ID3 on emit)",
                              padding=6)
        info.pack(side="left", fill="both", expand=True)
        for c in range(6):
            info.columnconfigure(c, weight=1)
        self.meta_vars = {}
        for n, key in enumerate(("title", "author", "album", "year",
                                 "genre", "comment")):
            row, col = divmod(n, 3)
            ttk.Label(info, text=f"{key}:").grid(row=row * 2, column=col * 2,
                                                 sticky="e", padx=(8, 2))
            var = tk.StringVar()
            self.meta_vars[key] = var
            ttk.Entry(info, textvariable=var, width=14).grid(
                row=row * 2, column=col * 2 + 1, sticky="ew", padx=(0, 8),
                pady=1)
        self.reread_btn = ttk.Button(info, text="Re-read",
                                     command=self._reharvest)
        self.reread_btn.grid(row=2, column=6, sticky="w")
        self._tip(self.reread_btn, "re-read tags and chapters from the "
                  "folder (after emit changed the file)")
        found = ttk.Label(info, textvariable=self.found_var,
                          foreground=palette.MUTED, justify="left")
        found.grid(row=4, column=0, columnspan=7, sticky="w", pady=(4, 0))
        self._wrap_labels.append(found)
        side = ttk.Frame(p)
        side.pack(side="left", fill="y", padx=(12, 0))
        self.embed_var = tk.BooleanVar(value=True)
        embed_cb = ttk.Checkbutton(side, text="Embed chapters",
                                   variable=self.embed_var)
        embed_cb.grid(row=0, column=0, sticky="w")
        self._tip(embed_cb, "also write a chaptered copy of the audio "
                  "(<name>.chapters.<ext>) for Finalize to put in place; "
                  "off writes only chapters.txt and chapters.json")
        self.emit_btn = ttk.Button(side, text="Emit", command=self.cmd_emit)
        self.emit_btn.grid(row=1, column=0, sticky="ew", pady=4)
        self._tip(self.emit_btn, "write the supported chapters into a "
                  "chaptered copy of the file (and the ID3 tags above)")
        self.emit_result = self._result_line(side, row=2)
        return p

    def _panel_finalize(self, host):
        p = ttk.Frame(host)
        p.columnconfigure(1, weight=1)
        self.finalize_btn = self._primary(p, 0, 0, "Dry run",
                                          self.cmd_finalize_dry)
        self.apply_btn = ttk.Button(p, text="Apply", state=["disabled"],
                                    command=self.cmd_finalize_apply)
        self.apply_btn.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self._tip(self.finalize_btn, "preview everything finalize "
                  "would do - nothing is changed")
        self.allow_delete_var = tk.BooleanVar(value=False)
        allow_delete_cb = ttk.Checkbutton(
            p, text="allow delete-and-re-add (clears listening progress)",
            variable=self.allow_delete_var)
        allow_delete_cb.grid(row=1, column=0, columnspan=2, sticky="w")
        self._tip(allow_delete_cb, "last resort: delete the ABS database "
                  "entry and let the library scan re-add it; clears "
                  "listening progress")
        self.finalize_result = self._result_line(p, row=2, colspan=2)
        self._tip(self.apply_btn, "stage the original away, rename the "
                  "chaptered file into place, write album.nfo, and "
                  "reload Audiobookshelf")
        return p
