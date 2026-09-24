# Chapter alignment

Tools that place published chapter marks onto a recording of an audiobook.

## Recording

**Edition**:
The published version of an audiobook whose chapter list we are matching, usually named by an ASIN.
_Avoid_: book, title, product

**Published timeline**:
The edition's chapter titles and start times, as the publisher issued them.
_Avoid_: TOC, chapter list, source chapters

**Gap**:
A pause in the recording that the envelope classified as a likely chapter boundary.
_Avoid_: silence, break, dip

**Placement**:
Where one published chapter was mapped onto the recording.
_Avoid_: start time, hit, match

**Pin**:
A human-confirmed placement that later alignment must honor.
_Avoid_: anchor, lock, override

**Artifact**:
The accumulated analysis JSON for one book; the single source of truth for later stages and the GUI.
_Avoid_: sidecar, state file, cache

## Stages

**Resolve**:
The stage that confirms or repairs weak placements by listening, sweeping, or pinning by ear.
_Avoid_: pin stage, confirm

**Emit**:
Writing the confident placements into sidecar files or the audio itself.
_Avoid_: export, write chapters

**Finalize**:
Pushing the emitted marks to Audiobookshelf and refreshing the book's NFO.
_Avoid_: publish, upload, apply

## Pipelines

**Envelope**:
Measure loudness once, classify gaps, then map the published timeline onto those gaps.
_Avoid_: chapterfit pipeline

**Silence-snap**:
Snap a published timeline onto silences detected in the audio.
_Avoid_: chapterize pipeline

**Consensus**:
Merge chapter lists from more than one source into one timeline.
_Avoid_: vote, blend
