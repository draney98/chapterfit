#!/usr/bin/env python3
"""librivox_chapters.py - compatibility shim: LibriVox section lists as a chapter source.

The implementation is chaptertools.sources.librivox_chapters; `python3 librivox_chapters.py ...` runs it and
`import librivox_chapters` returns that module itself (so patches and globals are shared).
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if __name__ == "__main__":
    import runpy
    runpy.run_module("chaptertools.sources.librivox_chapters", run_name="__main__", alter_sys=True)
else:
    import chaptertools.sources.librivox_chapters as _impl  # noqa: E402
    sys.modules[__name__] = _impl
