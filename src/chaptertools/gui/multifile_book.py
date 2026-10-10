"""Multi-file LibriVox book pane: status strip, file strip, chapter table.

No Scan / Match / Place / Finish. Audio cleanup is optional (Part 4).
"""
from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import ttk
from webbrowser import open as open_url

from chaptertools.gui import palette
from chaptertools.util import clock


AUDIO_EXTS = {".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".wav"}


def load_librivox(folder: str) -> dict | None:
    path = os.path.join(folder, "librivox.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def list_audio(folder: str) -> list[str]:
    out = []
    try:
        for name in sorted(os.listdir(folder)):
            if os.path.splitext(name)[1].lower() in AUDIO_EXTS:
                out.append(os.path.join(folder, name))
    except OSError:
        return []
    return out


def is_multifile_book(path: str) -> bool:
    """True for a folder (or file inside a folder) with librivox.json."""
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    return load_librivox(folder) is not None


class MultifileBook(ttk.Frame):
    def __init__(self, master, app, on_cleanup):
        super().__init__(master)
        self.app = app
        self.on_cleanup = on_cleanup
        self.folder = ""
        self.meta: dict = {}
        self.files: list[str] = []
        self._index = 0
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        head = ttk.Frame(self)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        head.columnconfigure(0, weight=1)
        self.title_var = tk.StringVar()
        self.author_var = tk.StringVar()
        self.meta_var = tk.StringVar()
        ttk.Label(head, textvariable=self.title_var,
                  font=("", 13, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(head, textvariable=self.author_var).grid(
            row=1, column=0, sticky="w")
        ttk.Label(head, textvariable=self.meta_var,
                  foreground=palette.MUTED).grid(row=2, column=0, sticky="w")
        nav = ttk.Frame(head)
        nav.grid(row=0, column=1, rowspan=3, sticky="e")
        ttk.Button(nav, text="◀ Prev", width=8,
                   command=self.play_prev).pack(side="left", padx=2)
        ttk.Button(nav, text="Next ▶", width=8,
                   command=self.play_next).pack(side="left", padx=2)

        strip = ttk.Frame(self)
        strip.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        self.chapters_status = tk.StringVar(
            value="✓ Chapters — files, one per chapter — nothing to scan, match or place")
        self.cleanup_status = tk.StringVar(value="○ Audio cleanup — off · optional")
        ch = ttk.Label(strip, textvariable=self.chapters_status, cursor="hand2")
        ch.pack(side="left", padx=(0, 16))
        cu = ttk.Label(strip, textvariable=self.cleanup_status, cursor="hand2")
        cu.pack(side="left")
        cu.bind("<Button-1>", lambda _e: self.on_cleanup())

        self.file_strip = tk.Canvas(self, height=36, bg="#E9E7E2",
                                    highlightthickness=0)
        self.file_strip.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self.file_strip.bind("<Button-1>", self._strip_click)
        self.file_strip.bind("<Configure>", lambda _e: self._draw_strip())

        body = ttk.Frame(self)
        body.grid(row=3, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0)
        body.rowconfigure(0, weight=1)

        cols = ("num", "title", "reader", "length", "file")
        self.table = ttk.Treeview(body, columns=cols, show="headings")
        for col, text, width in (
            ("num", "#", 40), ("title", "Title", 220), ("reader", "Reader", 140),
            ("length", "Length", 70), ("file", "File", 180),
        ):
            self.table.heading(col, text=text)
            self.table.column(col, width=width, stretch=col == "title")
        self.table.grid(row=0, column=0, sticky="nsew")
        self.table.bind("<Double-1>", self._play_row)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.table.yview)
        sb.grid(row=0, column=0, sticky="nse")
        self.table.configure(yscrollcommand=sb.set)

        side = ttk.Frame(body, width=380)
        side.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        side.grid_propagate(False)
        self._source_text = tk.StringVar()
        self._tags_text = tk.StringVar()
        ttk.Label(side, text="Source", font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(side, textvariable=self._source_text, wraplength=360,
                  justify="left").pack(anchor="w", pady=(0, 8))
        btns = ttk.Frame(side)
        btns.pack(anchor="w", pady=(0, 12))
        ttk.Button(btns, text="Open folder",
                   command=self._open_folder).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="LibriVox page",
                   command=self._open_lv).pack(side="left")
        ttk.Label(side, text="Tags", font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(side, textvariable=self._tags_text, wraplength=360,
                  justify="left").pack(anchor="w")

        ttk.Label(
            self,
            text="A single glued-together file still goes through Scan, Match, and Place.",
            foreground=palette.MUTED).grid(row=4, column=0, sticky="w", pady=(6, 0))

    def load(self, path: str) -> None:
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        self.folder = folder
        self.meta = load_librivox(folder) or {}
        self.files = list_audio(folder)
        title = self.meta.get("title") or os.path.basename(folder)
        author = self.meta.get("author") or os.path.basename(os.path.dirname(folder))
        self.title_var.set(title)
        self.author_var.set(author)
        total = sum(float(s.get("duration") or 0)
                    for s in self.meta.get("sections") or [])
        if not total:
            total = 0.0
        self.meta_var.set(
            f"{clock(total)} · {len(self.files)} files · "
            f"LibriVox {self.meta.get('id') or '—'} · "
            f"{self.meta.get('set') or ''}")
        n = len(self.files)
        self.chapters_status.set(
            f"✓ Chapters — {n} files, one per chapter — "
            "nothing to scan, match or place")
        cleanup = self.meta.get("cleanup") or {}
        if cleanup.get("written"):
            self.cleanup_status.set(
                f"✓ Audio cleanup — written {cleanup['written']}")
        else:
            self.cleanup_status.set("○ Audio cleanup — off · optional")

        self.table.delete(*self.table.get_children())
        sections = self.meta.get("sections") or []
        for i, path in enumerate(self.files):
            sec = sections[i] if i < len(sections) else {}
            num = sec.get("section_number", i)
            self.table.insert("", "end", iid=str(i), values=(
                f"{int(num):02d}",
                sec.get("title") or os.path.splitext(os.path.basename(path))[0],
                sec.get("reader") or "",
                clock(sec.get("duration") or 0),
                os.path.basename(path),
            ))
        readers = []
        for sec in sections:
            for name in sec.get("readers") or (
                    [sec["reader"]] if sec.get("reader") else []):
                if name and name not in readers:
                    readers.append(name)
        self._source_text.set(
            f"id {self.meta.get('id')} · set {self.meta.get('set')}\n"
            f"language {self.meta.get('language') or '—'}\n"
            f"readers: {', '.join(readers) or '—'}\n"
            f"fetched {self.meta.get('fetched') or '—'}")
        self._tags_text.set(
            f"album {title}\nartist {author}\n"
            f"title = chapter title · track 1–{n}\n"
            f"album.nfo and librivox.json")
        self._index = 0
        self._draw_strip()

    def _draw_strip(self):
        c = self.file_strip
        c.delete("all")
        if not self.files:
            return
        w = max(c.winfo_width(), 100)
        sections = self.meta.get("sections") or []
        durs = [float((sections[i] if i < len(sections) else {}).get("duration")
                      or 1)
                for i in range(len(self.files))]
        total = sum(durs) or 1
        x = 0.0
        for i, dur in enumerate(durs):
            bw = max(4, w * dur / total)
            color = "#4A6984" if i == self._index else "#C8C5BE"
            c.create_rectangle(x, 4, x + bw - 1, 32, fill=color, outline="")
            c.create_text(x + 4, 18, anchor="w", text=str(i),
                          fill="#fff" if i == self._index else "#333",
                          font=("", 8))
            x += bw

    def _strip_click(self, event):
        if not self.files:
            return
        w = max(self.file_strip.winfo_width(), 1)
        sections = self.meta.get("sections") or []
        durs = [float((sections[i] if i < len(sections) else {}).get("duration")
                      or 1)
                for i in range(len(self.files))]
        total = sum(durs) or 1
        x = 0.0
        for i, dur in enumerate(durs):
            bw = max(4, w * dur / total)
            if x <= event.x < x + bw:
                self.play_index(i)
                return
            x += bw

    def _play_row(self, _e=None):
        sel = self.table.selection()
        if not sel:
            return
        self.play_index(int(sel[0]))

    def play_index(self, index: int):
        if index < 0 or index >= len(self.files):
            return
        self._index = index
        self._draw_strip()
        self.table.selection_set(str(index))
        self.table.see(str(index))
        path = self.files[index]
        self.app.session.chapter_index = index
        self.app.play(path, start=0, length=0)
        bar = getattr(self.app, "player_bar", None)
        if bar is not None:
            bar.chapter_var.set(f"ch {index + 1}")

    def play_next(self):
        if self._index + 1 < len(self.files):
            self.play_index(self._index + 1)

    def play_prev(self):
        if self._index > 0:
            self.play_index(self._index - 1)

    def _open_folder(self):
        if self.folder:
            open_url(f"file://{self.folder}")

    def _open_lv(self):
        url = self.meta.get("url_librivox") or ""
        if url:
            open_url(url)
