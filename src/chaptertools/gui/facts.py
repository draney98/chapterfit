"""Folder and ffprobe facts for the selected book, read off the Tk thread.

The Tk thread only asks (`request`) and later collects (`results`, drained
by the app's pump); ffprobe and folder reads happen on one worker thread.
"""
from __future__ import annotations

import os
import queue
import threading

from chaptertools import media

from .harvest import harvest_book
from .stages import DiskFacts

AUDIO_EXTS = (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".wav")


def disk_facts(audio: str, emitted: dict | None) -> DiskFacts:
    """Folder facts from disk (raw listing, never the GUI scan)."""
    d = DiskFacts()
    folder = os.path.dirname(audio)
    try:
        names = os.listdir(folder)
    except OSError:
        return d
    d.audio_files = [f for f in names
                     if os.path.splitext(f)[1].lower() in AUDIO_EXTS]
    if len(d.audio_files) == 1:
        chs = media.probe_chapters(os.path.join(folder, d.audio_files[0]))
        d.embedded_chapters = len(chs)
        if chs:
            d.embedded_last_start = chs[-1]["start"]
    emitted = emitted or {}
    if emitted.get("count"):
        stem, ext = os.path.splitext(audio)
        ch_file = stem + ".chapters" + ext
        if os.path.isfile(ch_file):
            chs = media.probe_chapters(ch_file)
            d.chapters_file_match = bool(
                len(chs) == emitted.get("count") and chs
                and abs(chs[-1]["start"] - emitted.get("last", -1)) < 0.1)
    return d


class MediaFactsLoader:
    """One worker thread; only the newest request per book is answered.

    Results are (token, audio, DiskFacts | None, harvest | None, error).
    """

    def __init__(self):
        self.results: queue.Queue = queue.Queue()
        self._jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._work, daemon=True,
                         name="media-facts").start()

    def request(self, token: int, audio: str, emitted: dict | None,
                with_harvest: bool) -> None:
        self._jobs.put((token, audio, dict(emitted or {}), with_harvest))

    def _work(self):
        while True:
            job = self._jobs.get()
            # collapse a backlog to its newest request, keeping any harvest
            while True:
                try:
                    newer = self._jobs.get_nowait()
                except queue.Empty:
                    break
                harvest = newer[3] or (job[3] and newer[1] == job[1])
                job = (*newer[:3], harvest)
            token, audio, emitted, with_harvest = job
            try:
                disk = disk_facts(audio, emitted)
                harvest = harvest_book(audio) if with_harvest else None
                self.results.put((token, audio, disk, harvest, None))
            except Exception as e:  # reported on the Tk thread, not lost
                self.results.put((token, audio, None, None, e))
