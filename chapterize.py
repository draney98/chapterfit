#!/usr/bin/env python3
"""chapterize.py - compatibility shim: silence-snap: Audnexus chapters snapped onto ffmpeg silences.

The implementation is chaptertools.silence.chapterize; `python3 chapterize.py ...` runs it and
`import chapterize` returns that module itself (so patches and globals are shared).
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if __name__ == "__main__":
    import runpy
    runpy.run_module("chaptertools.silence.chapterize", run_name="__main__", alter_sys=True)
else:
    import chaptertools.silence.chapterize as _impl  # noqa: E402
    sys.modules[__name__] = _impl
