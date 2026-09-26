# Chapter toolkit

Local audiobook chapter tools (stdlib + ffmpeg). The code lives here; the
audiobook library it operates on (e.g. `/srv/media/books`) stays data-only.

Three pipelines:

- **Envelope:** `chapterfit` / `chapterbook.sh` — measure loudness, classify
  gaps, map a published timeline onto those gaps
- **Silence-snap:** `chapterize` and friends — snap a published timeline
  onto detected silences
- **Consensus:** `consensus-chapters` — merge chapter lists from more than
  one source

## Setup

```bash
python3 -m pip install -e '.[dev]'
pytest -q
```

Optional Whisper ASR:

```bash
python3 -m pip install -e '.[whisper]'
```

## Work directory

Analysis artifacts live under `$CHAPTERFIT_WORK/<author>/<book>`, not
beside the audio. `CHAPTERFIT_WORK` defaults to
`~/.local/share/chapterfit`. Pass `--work-dir` to override one run.

An older `<audio>.analysis.json` left beside the file is copied into the
work directory the first time the book is opened, with a notice. The
media tree stays data-only.

To measure every book that does not already have those artifacts:

```bash
./measure-library.sh /srv/media/books
./measure-library.sh /srv/media/books --dry-run
./find-asin-library.sh /srv/media/books
./find-asin-library.sh /srv/media/books --dry-run
./find-asin-library.sh /srv/media/books -- --verify
```

## GUI

A Tkinter front end for the envelope pipeline lives in
`src/chaptertools/gui/` (stdlib only, no new dependencies):

```bash
python3 chaptergui.py /srv/media/books
# or: CHAPTERFIT_LIBRARY=/srv/media/books python3 chaptergui.py
```

The library root is the positional argument if given, else
`CHAPTERFIT_LIBRARY`, else the current directory.

The `chaptergui` console script is also available after
`python3 -m pip install -e '.[dev]'`.

Left panel browses the author folders; the right panel drives the
chapterfit stages (measure → identify → align → resolve → emit →
finalize) as subprocesses, reads the `.analysis.json` artifact for
state, and previews chapter boundaries with `mpv`. Whisper listen
appears when `WHISPER_PYTHON` is set (or `~/.venvs/whisper` exists).
The same `~/.config/chapterfit/.env` is used for Audiobookshelf
credentials.

Finalize also maintains the Kodi-style `album.nfo` beside the book: on
`--apply` it writes one `<track>` per embedded chapter (title + duration),
keeping the existing metadata and backing up the original once as
`album.nfo.bak`; if there is no NFO yet, a minimal one is created.

An AI-review brief for the GUI (intent documents, known weaknesses,
button audit) lives in `docs/gui-review-brief.md`. Domain terms live in
`CONTEXT.md`.

## ABS credentials

Do **not** store `ABS_TOKEN` in the media library. Put settings in:

```text
~/.config/chapterfit/.env
```

```text
ABS_URL=http://localhost:13378
ABS_TOKEN=...
ABS_LIBRARY_NAME=Books
```

```bash
mkdir -p ~/.config/chapterfit
chmod 700 ~/.config/chapterfit
chmod 600 ~/.config/chapterfit/.env
```

`.env.example` in this repo shows the keys. If a token was ever stored
world/group-readable (for example in the old library-root `chapterfit.env`),
**rotate it in Audiobookshelf** and update the config file above.

## Verify

```bash
python3 chapterfit.py --help
python3 chapterize.py --help
pytest -q
```
