# Redesign the chaptertools layout around the workflow

## Context

`chaptertools` is a Tkinter front end for `chapterfit.py`, a CLI that places chapter marks in audiobooks. It runs on a 1920×1080 display. The current layout gives the chapter table about four visible rows, has a log that can't be enlarged, truncates labels, and scatters controls without regard to the order they're used in. Your job is to reorganize it around the workflow without removing any existing capability.

Do not modify `chapterfit.py`. Keep every function the current GUI has, including ones not described here (such as "Identify (embedded)" and the ID3 book-info writing); move them, don't drop them.

## The workflow the layout must follow

| Stage | Purpose | chapterfit command |
|---|---|---|
| 1 Measure | analyze the audio | `measure FILE` |
| 2 Identify | choose and verify the chapter list | `identify FILE --asin X` / `--chapters-json F`, plus the GUI's existing embedded option |
| 3 Align | place chapters | `align FILE --min-confidence N [--near CH=TIME]` |
| 4 Resolve | fix unconfident chapters | `listen`, `sweep`, `pin FILE --at CH=TIME` |
| 5 Emit | write chapters and ID3 info | `emit FILE --embed ...` |
| 6 Finalize | hand to Audiobookshelf | `finalize FILE`, then `finalize FILE --apply` |

Stage 4 is only needed when Align leaves chapters below the confidence threshold.

## Target layout

```
┌─ File  View  Help ──────────────────────────────────────────────────────────┐
├──────────────┬──────────────────────────────────────────────────────────────┤
│ Library      │ Eon — Greg Bear     17:07:43   B007MF0J3Y   chapterfit 1.13.0 │
│ [search   ]  │ ✓Measure › ✓Identify › ✓Align › !Resolve › ○Emit › ○Finalize │
│ ▸ Alastair…  ├──────────────────────────────────────────────────────────────┤
│ ▾ Greg Bear  │ STAGE PANEL: controls for the selected stage only (~170px)   │
│     Eon      ├──────────────────────────────────────────────────────────────┤
│ ▸ …          │ [Chapters 61/69] [Transcripts] [Log • 2 errors]              │
│              │  #  Title            Published   Placed    Gap   Conf  Pin   │
│              │  …  table fills ALL remaining height                         │
├──────────────┴──────────────────────────────────────────────────────────────┤
│ status: last log line ...                                    ◌ busy  Cancel │
└─────────────────────────────────────────────────────────────────────────────┘
```

Use a horizontal `ttk.PanedWindow` between the library (default ~300px) and the main area so the user can drag the divider. In the main area, only the notebook row gets `weight=1`; the header, stage bar and stage panel are fixed height. Resizing the window must grow the table and nothing else.

## The stage bar

Replace the cryptic `[ ] stages` label with a clickable row of six stages. Each shows a state: done (✓), current (●), needs attention (!), pending (○), or blocked (greyed, with a tooltip saying why, such as "select a book first"). Clicking a stage shows its panel. On book selection, compute every stage's state and select the first stage that isn't done. After a stage's action succeeds, advance to the next stage automatically.

Derive state from the analysis artifact, `<work-dir>/<audio basename>.analysis.json`, using the same work dir the GUI already passes to chapterfit. Read the JSON directly rather than parsing CLI output.

- **Measure** is done when `envelope`, `calibration`, `gaps` and `fingerprint` are present. Mark it "!" if `source.timeline_trustworthy` is false.
- **Identify** is done when `identification` is present. Its verdict comes from `identification.best_trial.score`: 5 or above is consistent, 2 to 5 is weak ("!"), below 2 is rejected ("!", and Align is blocked).
- **Align** is done when `alignment` is present. The weak count is the number of `alignment.results` entries whose `time` is null or whose `confidence` is below the min-conf value.
- **Resolve** is not needed when the weak count is zero; show it as done and skip it.
- **Emit** is done when `emitted` is present and the `.chapters.<ext>` file beside the audio has the same chapter count and last start time as `emitted`. Changing pins or alignment makes it pending again.
- **Finalize** has no stored marker. Treat it as done only when the book's folder contains exactly one audio file and that file has embedded chapters.

## Stage panels

Each panel has one primary button (the next thing to do) and states its result in one line of plain text once run.

1. **Measure** — Run button and a summary: gap count, population count, and warnings for a bad header (show the `ffmpeg ... -write_xing 1` fix) or a de-gapped transfer.
2. **Identify** — A three-way choice (ASIN, JSON file, Embedded) that swaps a single input field accordingly, plus a Browse button for JSON. Result: verdict, score, rate spread, and runtime ratio. When rejected, the primary action becomes "Try another edition."
3. **Align** — Min-conf spinbox, `near CH=TIME` field, Align button. Result: placed/total, rate, drift per hour.
4. **Resolve** — Split horizontally. On the left, the escalation options as a vertical list in cost order, each with its button and a cost note: position hint (free), Listen (~4 min of audio), Sweep probe (10 min), full Sweep (roughly book length divided by ten), Pin by ear. On the right, the pin-by-ear controls, described below.
5. **Emit** — The book info fields in a three-column grid (title, author, album / year, genre, comment), the re-read button, the embed checkbox, and Emit. Result: chapters embedded and chapters withheld.
6. **Finalize** — Dry run as the primary action. Apply stays disabled until a dry run has succeeded for this book in this session. An allow-delete checkbox, off by default, labeled with the warning that it clears listening progress.

**Sweep** isn't in the current GUI. It's the right tool for files whose chapter breaks have no silence, so add it to the Resolve list by calling `sweep FILE --start S --end E --pin -y` through the same Whisper interpreter the GUI uses for Listen. Skip this if the owner says no.

## Pin by ear

Make it driven by the chapter table. Selecting a row fills in the chapter number and placed time, which replaces the "Use placed time" button. Double-clicking a row starts preview. Group the transport controls together with even spacing: −10s, −5s, play/pause, +5s, +10s, and Stop preview. Stop currently floats alone at the bottom of the window, and there are unexplained gaps in the current playback row — find out what occupies them (probably an unmapped widget) and either restore it or remove the gap.

Keep "Set from playback," Pin, Remove pin, and "keep playing." Add keyboard shortcuts while the table has focus: Space for play/pause, Left and Right for ±5s, Shift+Left/Right for ±10s, Enter to pin, Delete to remove the pin.

## Chapter table

Title stretches; every other column has a fixed width. Show the confident count in the tab label, for example "Chapters 61/69." Tint rows: grey for withheld (unplaced or below min conf), amber for low confidence, a pin marker for pinned rows, and red for a pin chapterfit reports as conflicting. Move the "shaded / ?N = withheld" note into a one-line legend inside the tab. After any action, keep the selection and scroll position.

## Log and feedback

The log moves to its own tab, with Save log and Copy all inside that tab. Because the log is no longer visible, add a status bar at the bottom showing the most recent log line, a busy indicator, and Cancel for long-running actions. When errors are logged, change the tab label to "Log • N errors" instead of switching tabs automatically.

Run every chapterfit call in a background thread or subprocess, streaming its output to the log so the window never freezes. Disable controls that would conflict while a command runs.

If it's cheap, add a Transcripts tab that shows `transcripts FILE` output with a search box that passes `--grep`.

## Polish

Use `ttk` widgets throughout with one theme (`clam` is fine) and one font size. Use consistent padding of about 8px between groups and 4px within them. Write labels out in full ("Time (H:MM:SS)," "Remove pin," "Playback," "Keep playing") and size widgets so nothing truncates at the default window size. Set a sensible minimum window size, and open maximized or at 1600×960.

## Acceptance checks

- At 1920×1080, the table shows at least 25 rows without scrolling.
- No label or button text is truncated at the default size.
- Enlarging the window grows only the table.
- Every function in the current GUI is still reachable.
- Selecting a book lands on the first incomplete stage, and each completed action advances to the next.
- The latest log line and any error count are visible without leaving the Chapters tab.
- The window stays responsive during Measure, Listen and Sweep.
