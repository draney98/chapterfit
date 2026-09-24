# Chapter toolkit reference

## chapterfit stages

Typical order (also driven by `chapterbook.sh` and the GUI):

1. `measure` — write `.envelope.i8` + envelope section of `.analysis.json`
2. `calibrate` / classify gaps from envelope distribution
3. `identify` — edition / published timeline (`--asin` or chapters JSON)
4. `align` — monotonic mapping published chapters → observed gaps
5. `resolve` — listen / sweep / pin by ear (`--at 3=43:47`, `--near`)
6. `emit` — ffmetadata / ABS JSON / embed
7. `listen` / `sweep` / `transcripts` — optional Whisper confirmation
8. `finalize` — push to Audiobookshelf API
9. `status` — show artifact state

Artifact class lives in `chaptertools.envelope.artifact`. Envelope Hz
default: 20 (50 ms frames). Schema constant: `SCHEMA`. Package version:
`chaptertools.__version__`.

## Silence-snap stack

- `chaptertools.silence.chapterize` — core align/snap/embed/split
- `chaptertools.silence.abchapters` — interactive / batch front end
- `chaptertools.silence.chapterdebug` — inspect clips, anchor, tape period, profile
- `chaptertools.consensus.consensus_chapters` — multi-source consensus
- `chaptertools.sources.librivox_chapters` — LibriVox sections
- `chaptertools.sources.find_asin` — Audible catalog ASIN discovery

Silence cache: `BOOK.mp3.silences.json` (hash-keyed; see `chapterize.detect_silences`).

Audnexus cache: `chapters.{asin}.{region}.json` (legacy
`{asin}.{region}.chapters.json` is still read). One fetch path in
`chaptertools.audnex`.

## Env / config keys

| Key | Purpose |
|-----|---------|
| `ABS_URL` | Audiobookshelf base URL |
| `ABS_TOKEN` | API token (secret) |
| `ABS_LIBRARY_NAME` | Target library |
| `CHAPTERFIT` | Override path to the `chapterfit.py` shim |
| `CHAPTERFIT_WORK` | Work/staging root (`<author>/<book>` under this) |
| `CHAPTERFIT_LIBRARY` | GUI library root when no path argument is given |
| `WHISPER_MODEL` | faster-whisper model name |
| `WHISPER_PYTHON` | Interpreter that has faster-whisper |

Use `~/.config/chapterfit/.env`. Never put a `.env` in the media library.

## GUI structure

- `gui/projection.py` — `WorkflowProjection` from artifact + media facts + threshold
- `gui/commands.py` — argv builders (`chapterfit -m`, whisper `-m`)
- `gui/table.py` — chapters and gaps tabs
- `gui/panels/` — one view per stage
- `gui/workflow.py` — coordinator; paints from the projection
- `gui/facts.py` — off-thread `MediaFactsLoader`

## Known debt

- Envelope/silence alignment math must stay behavior-preserving
- `asr.py` and the moved CLI modules keep a wider lint ignore
