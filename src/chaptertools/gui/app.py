"""Main window: library pane, workflow main area, tabs, status bar."""
from __future__ import annotations

import os
import queue
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from chaptertools.util import clock

from . import fonts, tips
from .library import library_root as resolve_library_root
from .library import scan_library
from .mpv import Player
from .runner import Runner
from .workflow import SHORTCUTS, WorkflowPanel

LIBRARY_WIDTH = 300


class App(tk.Tk):
    def __init__(self, library_root: str | None = None):
        super().__init__()
        self.title("chaptertools")
        self.geometry("1600x960")
        self.library_root = resolve_library_root(library_root)
        self.runner = Runner()
        self.player = Player()
        self._playback_time: float | None = None
        self._library: dict[str, list[str]] = {}
        self.log_errors = 0

        self._setup_fonts()
        self._build_menu()
        self._build_ui()
        self._fill_library()
        self.fit_minsize()
        self.after_idle(lambda: self._pane.sashpos(0, LIBRARY_WIDTH))
        self.after(100, self._pump)
        self.protocol("WM_DELETE_WINDOW", self._quit)

    def fit_minsize(self):
        """Smallest window in which no panel clips: the widest panel's
        need (Resolve counted stacked, as it stacks when narrow) plus
        the library pane."""
        self.update_idletasks()
        need_w = self.workflow.min_width() + LIBRARY_WIDTH + 16
        need_h = self.workflow.min_height() + 60  # menu + status bar
        self.minsize(max(need_w, 1024), max(need_h, 600))

    def _setup_fonts(self, base: int | None = None):
        if base is None:
            base = int(os.environ.get("CHAPTERGUI_FONT", "11"))
        self.font_base = base
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                     "TkHeadingFont", "TkFixedFont"):
            try:
                f = tkfont.nametofont(name, root=self)
            except tk.TclError:
                continue
            f.configure(size=base + (2 if name == "TkFixedFont" else 0))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", rowheight=tkfont.nametofont(
            "TkDefaultFont", root=self).metrics("linespace") + 6)
        style.configure("TButton", padding=(8, 1))

    def set_font_size(self, base: int):
        """View menu: every font, column width and minimum size follows."""
        base = max(8, min(base, 24))
        self._setup_fonts(base)
        fonts.refresh(self)
        self.workflow.size_table()
        self._render_library(self.search_var.get())
        self.fit_minsize()
        self.log(f"text size {base} pt")

    def _build_menu(self):
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(label="Open library…", command=self._open_library)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self._quit)
        menu.add_cascade(label="File", menu=file_menu)

        view = tk.Menu(menu, tearoff=0)
        view.add_command(label="Larger text", accelerator="Ctrl++",
                         command=lambda: self.set_font_size(self.font_base + 1))
        view.add_command(label="Smaller text", accelerator="Ctrl+-",
                         command=lambda: self.set_font_size(self.font_base - 1))
        view.add_command(label="Default text size", accelerator="Ctrl+0",
                         command=lambda: self.set_font_size(11))
        menu.add_cascade(label="View", menu=view)
        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.bind_all(seq, lambda e: self.set_font_size(self.font_base + 1))
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.bind_all(seq, lambda e: self.set_font_size(self.font_base - 1))
        self.bind_all("<Control-0>", lambda e: self.set_font_size(11))

        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(label="Keyboard shortcuts",
                              command=self._show_shortcuts)
        help_menu.add_command(label="About chaptertools",
                              command=self._show_about)
        menu.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menu)

    def _show_shortcuts(self):
        messagebox.showinfo(
            "Keyboard shortcuts",
            "In the chapter table:\n\n" + SHORTCUTS.replace(" · ", "\n")
            + "\n\nAnywhere:\n\nCtrl++ / Ctrl+- larger / smaller text\n"
              "Ctrl+0 default text size", parent=self)

    def _show_about(self):
        from chaptertools import __version__
        messagebox.showinfo(
            "About chaptertools",
            f"chaptertools {__version__}\n\nChapter placement for "
            "audiobooks: Measure, Identify, Align, Resolve, Emit, "
            "Finalize.", parent=self)

    def _build_ui(self):
        pane = ttk.Panedwindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True)

        # ---- library pane -------------------------------------------------
        lib = ttk.LabelFrame(pane, text="Library", padding=4)
        pane.add(lib, weight=0)
        searchbar = ttk.Frame(lib)
        searchbar.pack(side="top", fill="x", pady=(0, 4))
        ttk.Label(searchbar, text="search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._apply_search())
        search_entry = ttk.Entry(searchbar, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=(4, 0))
        search_entry.bind("<Escape>", lambda e: self.search_var.set(""))
        tree_box = ttk.Frame(lib)
        tree_box.pack(side="top", fill="both", expand=True)
        tree_box.columnconfigure(0, weight=1)
        tree_box.rowconfigure(0, weight=1)
        self.lib_tree = ttk.Treeview(tree_box, show="tree")
        self.lib_tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(tree_box, orient="vertical",
                           command=self.lib_tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        xsb = ttk.Scrollbar(tree_box, orient="horizontal",
                            command=self.lib_tree.xview)
        xsb.grid(row=1, column=0, sticky="ew")
        self.lib_tree.configure(yscrollcommand=sb.set,
                                xscrollcommand=xsb.set)
        self.lib_tree.bind("<<TreeviewSelect>>", self._on_select)
        self._pane = pane

        # ---- status bar (packed before the pane so it never gets squeezed)
        status = ttk.Frame(self, padding=(8, 1))
        status.pack(side="bottom", fill="x", before=pane)
        self.busy_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.busy_var).pack(side="right")
        cancel = ttk.Button(status, text="Cancel job", command=self._cancel,
                            padding=(8, 0))
        cancel.pack(side="right", padx=(0, 6))
        tips.tooltip(cancel, "stop the running job and any playback")

        # ---- main area: workflow (owns the notebook) ----------------------
        main = ttk.Frame(pane)
        pane.add(main, weight=1)
        self.workflow = WorkflowPanel(main, app=self)
        self.workflow.pack(fill="both", expand=True)
        self.notebook = self.workflow.notebook
        self._build_log_tab()
        self.status_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.status_var,
                  anchor="w").pack(side="left", fill="x", expand=True)

    def _build_log_tab(self):
        tab = ttk.Frame(self.notebook)
        bar = ttk.Frame(tab)
        bar.pack(side="top", fill="x")
        copy = ttk.Button(bar, text="Copy all", command=self.copy_log)
        copy.pack(side="right", padx=(0, 2))
        tips.tooltip(copy, "copy the whole log to the clipboard")
        save = ttk.Button(bar, text="Save log…", command=self.save_log)
        save.pack(side="right", padx=(0, 8))
        tips.tooltip(save, "write the log to a file")
        self.log_text = tk.Text(tab, height=8, wrap="none",
                                font=tkfont.nametofont("TkFixedFont", root=self))
        self.log_text.pack(side="top", fill="both", expand=True)
        lsb = ttk.Scrollbar(tab, orient="vertical",
                            command=self.log_text.yview)
        lsb.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=lsb.set)
        self._make_log_readonly()
        menu = tk.Menu(self.log_text, tearoff=0)
        menu.add_command(label="Copy", command=self._copy_selection)
        menu.add_command(label="Select all",
                         command=lambda: self.log_text.tag_add(
                             "sel", "1.0", "end"))
        menu.add_command(label="Copy everything", command=self.copy_log)
        menu.add_command(label="Save log…", command=self.save_log)
        self.log_text.bind("<Button-3>",
                           lambda e: menu.tk_popup(e.x_root, e.y_root))
        self._log_tab = tab
        self.notebook.add(tab, text="Log")

    # ------------------------------------------------------------ log pane --
    def _make_log_readonly(self):
        """Selectable and copyable, but keystrokes cannot modify it."""
        self.log_text.bind("<Key>", self._log_key)
        self.log_text.bind("<Button-2>", lambda e: "break")  # no middle paste
        self.log_text.bind("<ButtonRelease-2>", lambda e: "break")

    def _log_key(self, event):
        ctrl = event.state & 0x0004
        if ctrl and event.keysym in ("c", "C", "a", "A"):
            return None
        return "break"

    def _copy_selection(self):
        if self.log_text.tag_ranges("sel"):
            self.log_text.event_generate("<<Copy>>")

    def copy_log(self):
        self.clipboard_clear()
        self.clipboard_append(self.log_text.get("1.0", "end-1c"))

    def save_log(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Save log",
            initialfile="chaptergui.log",
            filetypes=[("Log files", "*.log"), ("All files", "*")])
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.log_text.get("1.0", "end-1c"))

    def log(self, line: str):
        self.log_text.insert("end", line + "\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 5000:
            self.log_text.delete("1.0", "2.0")
        if not self.log_text.tag_ranges("sel"):
            self.log_text.see("end")
        self.status_var.set(line)

    # ------------------------------------------------------------ library --
    def _open_library(self):
        chosen = filedialog.askdirectory(parent=self,
                                         initialdir=self.library_root)
        if chosen:
            self.library_root = chosen
            self._fill_library()

    def _fill_library(self):
        self._library = scan_library(self.library_root)
        self._render_library(self.search_var.get())

    def _render_library(self, query: str | None):
        """(Re)draw the tree; a query of 3+ chars filters by title/author."""
        query = (query or "").strip().lower()
        active = len(query) >= 3
        self.lib_tree.delete(*self.lib_tree.get_children())
        selected = self.lib_tree.selection()

        font = tkfont.nametofont("TkDefaultFont", root=self)
        widest = 0
        for author in sorted(self._library):
            author_hit = active and query in author.lower()
            paths = [p for p in self._library[author]
                     if not active or author_hit or query in _title_of(p)]
            if not paths:
                continue
            author_id = self.lib_tree.insert("", "end",
                                             text=author, open=active)
            widest = max(widest, font.measure(author))
            for path in paths:
                label = _book_label(path, self._library[author])
                self.lib_tree.insert(author_id, "end", iid=str(path),
                                     text=label)
                widest = max(widest, font.measure(label) + 20)
        # wider than the pane -> the horizontal scrollbar reaches the rest
        self.lib_tree.column("#0", minwidth=widest + 40, stretch=True)

        if selected and self.lib_tree.exists(selected[0]):
            self.lib_tree.selection_set(selected[0])

    def _apply_search(self):
        self._render_library(self.search_var.get())

    def _on_select(self, _event=None):
        sel = self.lib_tree.selection()
        if not sel:
            return
        iid = sel[0]
        if self.lib_tree.parent(iid):
            path = self._path_for(iid)
            if path:
                self.workflow.set_book(path)

    def _path_for(self, iid) -> str | None:
        """Leaf ids are the absolute path of the audio file."""
        return iid if os.path.isfile(iid) else None

    # ------------------------------------------------------------ plumbing --
    def _pump(self):
        """Drain runner events and poll the player on the Tk thread."""
        while True:
            try:
                kind, job, payload = self.runner.events.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                if getattr(job, "on_line", None):
                    job.on_line(payload)
                else:
                    self.log(payload)
            elif kind == "done":
                if payload != 0 and not job.cancelled:
                    self.log_errors += 1
                    self.notebook.tab(
                        self._log_tab, text=f"Log • {self.log_errors} "
                        f"error{'s' if self.log_errors != 1 else ''}")
                if job.on_done:
                    job.on_done(job, payload)
            elif kind == "refused":
                self.log(f"! {job.name} not started: {payload}")
        t = self.player.poll()
        self._playback_time = t
        self.workflow.update_playback(t)
        busy = self.runner.busy
        self.workflow.set_busy(busy)
        self.busy_var.set("busy ◌" if busy else "")
        self.after(100, self._pump)

    def _cancel(self):
        self.runner.cancel()
        self.player.stop()

    def current_playback(self) -> float | None:
        return self._playback_time

    def play(self, path: str, start: float | None = None,
             length: int | None = None):
        self.stop_audio()
        try:
            self.player.play(path,
                             start=clock(start) if start is not None else None,
                             length=length)
        except OSError as e:
            messagebox.showerror("mpv", f"could not start mpv: {e}", parent=self)

    def stop_audio(self):
        self.player.stop()
        self._playback_time = None
        self.workflow.update_playback(None)

    def _quit(self):
        self.runner.cancel()
        self.player.stop()
        self.destroy()


def _book_label(path: str, siblings: list[str]) -> str:
    """The book folder's name; the file name too when a folder holds
    several audio files."""
    folder = os.path.dirname(path)
    book = os.path.basename(folder)
    shared = sum(1 for p in siblings if os.path.dirname(p) == folder)
    if shared > 1 or not book:
        name = os.path.splitext(os.path.basename(path))[0]
        return f"{book} — {name}" if book else name
    return book


def _title_of(path: str) -> str:
    """Searchable title text: filename without extension + book folder."""
    base = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.basename(os.path.dirname(path))
    return f"{folder} {base}".lower()


def main(argv: list[str] | None = None):
    if argv is None:
        argv = sys.argv[1:]
    library_root = next((a for a in argv if not a.startswith("-")), None)
    app = App(library_root=library_root)
    app.mainloop()


if __name__ == "__main__":
    main()
