"""One book's view state. Views subscribe; they do not read each other."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BookInfo:
    title: str = ""
    author: str = ""
    narrator: str = ""
    album: str = ""
    year: str = ""
    genre: str = ""
    comment: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "title": self.title, "author": self.author, "narrator": self.narrator,
            "album": self.album, "year": self.year, "genre": self.genre,
            "comment": self.comment,
        }


@dataclass
class JobState:
    running: bool = False
    name: str = ""


class BookSession:
    """Shared state for the open book."""

    def __init__(self) -> None:
        self.book: str | None = None
        self.scan = None
        self.edition = None
        self.book_info = BookInfo()
        self.chapters: list = []
        self.min_confidence = 0.5
        self.hints: dict[int, float] = {}
        self.transcripts: list = []
        self.cleanup = {"include": False, "preset": "", "stages_on": 0}
        self.playhead = 0.0
        self.duration = 0.0
        self.selection: tuple[float, float] | None = None
        self.last_dry_run = None
        self.job = JobState()
        self.chapter_index: int | None = None
        self.preview_marks: list[float] = []
        self._subs: dict[str, list] = {}

    def subscribe(self, topic: str, fn) -> None:
        self._subs.setdefault(topic, []).append(fn)

    def emit(self, topic: str) -> None:
        for fn in list(self._subs.get(topic, ())):
            fn()
        if topic != "*":
            for fn in list(self._subs.get("*", ())):
                fn()

    def set_playhead(self, seconds: float) -> None:
        self.playhead = max(0.0, float(seconds))
        self.emit("playhead")

    def set_selection(self, span: tuple[float, float] | None) -> None:
        self.selection = span
        self.emit("selection")

    def set_job(self, running: bool, name: str = "") -> None:
        self.job = JobState(running=running, name=name)
        self.emit("job")
