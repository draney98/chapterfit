# Chapter toolkit

Local audiobook chapter tools (stdlib + ffmpeg). The code lives here; the
audiobook library it operates on (e.g. `/srv/media/books`) stays data-only.
Two pipelines:

- **Silence-snap:** `chapterize.py` and friends
- **Envelope:** `chapterfit.py` / `chapterbook.sh`

## Setup

```bash
python3 -m pip install -e '.[dev]'
pytest -q
```

Optional Whisper ASR:

```bash
python3 -m pip install -e '.[whisper]'
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

Left panel browses the author folders; the right panel drives the chapterfit
stages (measure → identify → align → pin → emit → finalize) as subprocesses,
reads the `.analysis.json` artifact for state, and previews chapter
boundaries with `mpv`. Whisper listen appears when `~/.venvs/whisper` exists
(`WHISPER_PYTHON` overrides). The same `~/.config/chapterfit/.env` is used
for Audiobookshelf credentials.

Finalize also maintains the Kodi-style `album.nfo` beside the book: on
`--apply` it writes one `<track>` per embedded chapter (title + duration),
keeping the existing metadata and backing up the original once as
`album.nfo.bak`; if there is no NFO yet, a minimal one is created.

An AI-review brief for the GUI (intent documents, known weaknesses,
button audit) lives in `docs/gui-review-brief.md`.

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
