# Chapter toolkit reference

## chapterfit stages

Typical order (also driven by `chapterbook.sh`):

1. `measure` — write `.envelope.i8` + envelope section of `.analysis.json`
2. `calibrate` / classify gaps from envelope distribution
3. `identify` — ASIN / published timeline structural check (`--asin` or chapters JSON)
4. `align` — monotonic mapping published chapters → observed gaps
5. `pin` — human anchors (`--at 3=43:47`, `--near`)
6. `emit` — ffmetadata / ABS JSON / embed
7. `listen` / `sweep` / `transcripts` — optional Whisper confirmation (root v1.13+)
8. `finalize` — push to Audiobookshelf API
9. `status` — show artifact state

Artifact class lives in `chapterfit.py` (search `class Artifact` / stage 0). Envelope Hz default: 20 (50 ms frames). Schema constant: `SCHEMA`.

## Silence-snap stack

- `chapterize.py` — core align/snap/embed/split; Audnexus via `api.audnex.us`
- `abchapters.py` — interactive / batch front end
- `chapterdebug.py` — inspect clips, anchor, tape period, profile
- `consensus_chapters.py` — multi-source consensus (+ tests)
- `librivox_chapters.py` — LibriVox sections
- `find_asin.py` — Audible catalog ASIN discovery

Silence cache: `BOOK.mp3.silences.json` (hash-keyed; see `chapterize.detect_silences`).

## Env / config keys

| Key | Purpose |
|-----|---------|
| `ABS_URL` | Audiobookshelf base URL |
| `ABS_TOKEN` | API token (secret) |
| `ABS_LIBRARY_NAME` | Target library |
| `CHAPTERFIT` | Override path to `chapterfit.py` |
| `CHAPTERFIT_WORK` | Work/staging root |
| `CHAPTERFIT_LIBRARY` | GUI library root when no path argument is given |
| `WHISPER_MODEL` | faster-whisper model name |

Use `~/.config/chapterfit/.env`. Never put a `.env` in the media library.

## Known debt (cleanup backlog)

- Monoliths: `chapterfit.py` ~3.3k LOC; `chapterdebug.py` ~2k
- Duplicated helpers: `hms` / `die` / `parse_time` / Audnexus fetch / emit writers