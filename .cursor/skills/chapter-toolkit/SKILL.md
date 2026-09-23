---
name: chapter-toolkit
description: >-
  Domain guide for the audiobook chapter toolkit in /home/oem/dev/chapterfit:
  silence-snap (chapterize/abchapters) vs envelope (chapterfit/chapterbook.sh)
  pipelines, sidecar artifacts, Audnexus/ABS integration, and safe refactor
  rules. Use when editing chapterfit.py, chapterize.py, chapterdebug.py,
  abchapters.py, consensus_chapters.py, librivox_chapters.py, find_asin.py,
  chapterbook.sh, or when cleaning up / packaging this toolkit.
---

# Chapter toolkit

Local audiobook chapter tools. The project lives in `/home/oem/dev/chapterfit`;
the media library (`/srv/media/books`, or `CHAPTERFIT_LIBRARY`) is data-only —
never add code, configs, or caches there. Core stays
**stdlib + ffmpeg/ffprobe**. Optional ASR uses **faster-whisper** in a separate
venv. Do not introduce requests/httpx/numpy unless the user explicitly asks.

## Two pipelines (do not merge yet)

| Pipeline | Entry points | Approach |
|----------|--------------|----------|
| Silence-snap | `chapterize.py`, `abchapters.py`, `chapterdebug.py`, `consensus_chapters.py`, `librivox_chapters.py`, `find_asin.py` | Audnexus/LibriVox timeline → ffmpeg `silencedetect` → offset/scale → snap → embed/ABS JSON/split |
| Envelope | `chapterfit.py` (v1.13+), `chapterbook.sh` | Loudness envelope once → calibrate → classify gaps → identify ASIN → align → pin → emit → optional Whisper → ABS finalize |

Shared nouns: **ASIN**, **gap**, **pin**, **artifact**, **silences sidecar**, **envelope**.

Keep both CLIs until a deliberate UX unification. Alignment math is sacred during
structure refactors—behavior-preserving moves only.

## Script map

```
abchapters.py ──► chapterize.py ◄── find_asin.py
                      ▲
chapterdebug.py ──────┤
consensus_chapters.py ┤ (+ soft import find_asin / librivox)
librivox_chapters.py ─┘

chapterbook.sh ──► chapterfit.py (+ find_asin.py for ASIN discovery)
```

- Prefer root `chapterfit.py` (`__version__ >= 1.13`).
- Do not create a second `chapterfit.py` implementation or forwarding shims inside the media tree.
- Silence-stack scripts import `chapterize`, which re-exports `chaptertools.silence` / `audnex` / `output`.
- Package code lives under `src/chaptertools/`; keep `PYTHONPATH=src` or `pip install -e .` when developing.

## Artifacts (beside the audio unless `--work` overrides)

| File | Meaning |
|------|---------|
| `BOOK.mp3.envelope.i8` | Raw int8 dB envelope (chapterfit measure) |
| `BOOK.mp3.analysis.json` | Accumulated chapterfit stages (schema versioned) |
| `BOOK.mp3.silences.json` | Cached ffmpeg silence list (chapterize) |
| `chapters.ASIN.REGION.json` | Cached Audnexus chapter list |
| `chapters.json` / `--abs-json` | Audiobookshelf chapter editor payload |
| `album.nfo` (`.nfo.bak`) | Kodi-style NFO rewritten by `finalize --apply`: one `<track>` per embedded chapter (`chaptertools/nfo.py`); original backed up once |

Orchestration work root: `~/.local/share/chapterfit` (`CHAPTERFIT_WORK`).
Config preference: `~/.config/chapterfit/.env` — **not** next to the library.

## Secrets

- Never commit or copy `ABS_TOKEN` into the media tree.
- `ABS_URL` / `ABS_TOKEN` / `ABS_LIBRARY_NAME` live in `~/.config/chapterfit/.env` (mode 600); `.env.example` in the repo shows the keys only.
- Do not print token values in logs, diffs, or skill docs.

## Cleanup / refactor rules

When restructuring this project, load these global skills and follow them:

1. **request-refactor-plan** + **domain-modeling** — lock scope; tiny Fowler commits
2. **python-testing-patterns** + **test-driven-development** — characterization tests before moves (today only `test_consensus_chapters.py` exists)
3. **codebase-design** + **python-project-structure** — extract deep modules, thin CLIs
4. **safe-refactor** + **verification-before-completion** — one ownership boundary at a time; prove with commands

Target package shape (when packaging):

```text
chaptertools/
  util.py          # hms, parse_time, die, need, http_json
  audnex.py        # fetch / flatten / cache
  silence.py       # detect_silences + sidecar
  output.py        # ffmetadata, abs_json, embed, split
  envelope/        # measure, calibrate, classify, solve
  abs_client.py
  asr.py           # optional faster-whisper
```

Console scripts may live outside the book tree; media stays data-only.

## Verify after changes

Run what you touched; do not claim success without output:

```bash
python3 chapterfit.py --help
python3 chapterize.py --help
python3 test_consensus_chapters.py
```

For deeper envelope work, prefer a golden fixture book with existing
`.envelope.i8` + `.analysis.json` rather than re-measuring full mp3s.

## More detail

- Artifact and stage details: [reference.md](reference.md)
