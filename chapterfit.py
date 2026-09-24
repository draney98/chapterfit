#!/usr/bin/env python3
"""chapterfit.py - compatibility shim: envelope pipeline CLI (chapterbook.sh runs this file).

The implementation is chaptertools.cli.chapterfit; `python3 chapterfit.py ...` runs it and
`import chapterfit` returns that module itself (so patches and globals are shared).
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if __name__ == "__main__":
    import runpy
    runpy.run_module("chaptertools.cli.chapterfit", run_name="__main__", alter_sys=True)
else:
    import chaptertools.cli.chapterfit as _impl  # noqa: E402
    sys.modules[__name__] = _impl
