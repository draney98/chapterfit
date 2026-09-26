#!/usr/bin/env bash
#
# measure-library.sh - run `chapterfit measure` on every audiobook in a
# library that does not already have measure results in the work directory.
#
#   ./measure-library.sh /srv/media/books
#   CHAPTERFIT_LIBRARY=/srv/media/books ./measure-library.sh
#   ./measure-library.sh /srv/media/books --dry-run
#   ./measure-library.sh /srv/media/books --force
#
# A book is skipped when the work directory already has both
#   <audio>.envelope.i8
#   <audio>.analysis.json
# and the JSON still matches this audio (schema, size, mtime) and holds
# the measure stages (envelope, calibration, gaps, fingerprint).
# That is the same cache chapterfit measure would reuse.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHAPTERFIT="${CHAPTERFIT:-$SCRIPT_DIR/chapterfit.py}"
WORK_ROOT="${CHAPTERFIT_WORK:-$HOME/.local/share/chapterfit}"

LIBRARY=""
DRY_RUN=0
FORCE=0
MEASURE_ARGS=()

if [[ -t 1 ]]; then
  B=$'\033[1m'; DIM=$'\033[2m'; R=$'\033[31m'; G=$'\033[32m'
  Y=$'\033[33m'; C=$'\033[36m'; N=$'\033[0m'
else B=""; DIM=""; R=""; G=""; Y=""; C=""; N=""; fi

say()   { printf '%s\n' "$*"; }
ok()    { printf '%s  ok%s  %s\n' "$G" "$N" "$*"; }
skip()  { printf '%s  --%s  %s\n' "$DIM" "$N" "$*"; }
warn()  { printf '%s  !%s   %s\n' "$Y" "$N" "$*"; }
err()   { printf '%s  x%s   %s\n' "$R" "$N" "$*" >&2; }
die()   { err "$*"; exit 1; }

usage() {
  cat <<EOF
usage: $(basename "$0") [LIBRARY] [options] [-- measure-options]

  LIBRARY                author-folder root (default \$CHAPTERFIT_LIBRARY)
  -n, --dry-run          list what would run; do not measure
  -f, --force            measure even when work-dir results already exist
  -h, --help             this help

  Extra arguments after -- go to chapterfit measure (e.g. -- --hz 20).

Work directory is \$CHAPTERFIT_WORK/<author>/<book>
(\$CHAPTERFIT_WORK defaults to ~/.local/share/chapterfit).
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage ;;
    -n|--dry-run) DRY_RUN=1; shift ;;
    -f|--force) FORCE=1; shift ;;
    --) shift; MEASURE_ARGS+=("$@"); break ;;
    -*) die "unknown option $1" ;;
    *) [[ -z "$LIBRARY" ]] && LIBRARY="$1" || die "only one library root"; shift ;;
  esac
done

[[ -n "$LIBRARY" ]] || LIBRARY="${CHAPTERFIT_LIBRARY:-}"
[[ -n "$LIBRARY" ]] || die "pass a library root or set CHAPTERFIT_LIBRARY"
[[ -d "$LIBRARY" ]] || die "no such directory: $LIBRARY"
command -v python3 >/dev/null || die "python3 not found"
command -v ffmpeg >/dev/null || die "ffmpeg not found"
[[ -f "$CHAPTERFIT" ]] || die "chapterfit.py not found at $CHAPTERFIT"

LIBRARY="$(cd "$LIBRARY" && pwd)"
WORK_ROOT="$(realpath -m "$WORK_ROOT")"

# Same skip set as chaptertools.gui.library.scan_library.
is_author_dir() {
  local name="$1"
  case "$name" in
    .*|_*) return 1 ;;
    __pycache__|src|tests|.pytest_cache) return 1 ;;
  esac
  return 0
}

is_audio() {
  local base ext
  base="$(basename "$1")"
  [[ "$base" == *.chapters.* ]] && return 1
  ext="${base##*.}"
  case "${ext,,}" in
    mp3|m4a|m4b|flac|ogg|wav) return 0 ;;
  esac
  return 1
}

# Exit 0 when the work directory already has a complete, current measure.
have_measure() {
  local audio="$1" work="$2" base art env
  base="$(basename "$audio")"
  art="$work/${base}.analysis.json"
  env="$work/${base}.envelope.i8"
  [[ -s "$env" && -f "$art" ]] || return 1
  python3 -c '
import json, os, sys
audio, path = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(path))
except (OSError, ValueError):
    sys.exit(1)
st = os.stat(audio)
fp = data.get("file") or {}
if fp.get("size") != st.st_size or fp.get("mtime") != int(st.st_mtime):
    sys.exit(1)
if data.get("schema") != 3:
    sys.exit(1)
for key in ("envelope", "calibration", "gaps", "fingerprint"):
    if not data.get(key):
        sys.exit(1)
' "$audio" "$art"
}

collect_audio() {
  local author_path="$1" dirpath name
  while IFS= read -r -d '' dirpath; do
    name="$(basename "$dirpath")"
    case "$name" in .*|_*) continue ;; esac
    for f in "$dirpath"/*; do
      [[ -f "$f" ]] || continue
      is_audio "$f" && printf '%s\0' "$f"
    done
  done < <(find "$author_path" -type d -print0)
}

FOUND=0
SKIPPED=0
RAN=0
FAILED=0

say "${B}${C}measure-library${N}  $LIBRARY"
printf '%swork root %s%s\n' "$DIM" "$WORK_ROOT" "$N"
(( FORCE )) && warn "forcing remesure of books that already have results"
say ""

shopt -s nullglob
for author_path in "$LIBRARY"/*/; do
  author="$(basename "$author_path")"
  is_author_dir "$author" || continue
  while IFS= read -r -d '' audio; do
    FOUND=$((FOUND + 1))
    folder="$(dirname "$audio")"
    work="$WORK_ROOT/$(basename "$(dirname "$folder")")/$(basename "$folder")"
    rel="${audio#"$LIBRARY"/}"
    if (( ! FORCE )) && have_measure "$audio" "$work"; then
      SKIPPED=$((SKIPPED + 1))
      skip "skip  $rel"
      continue
    fi
    if (( DRY_RUN )); then
      RAN=$((RAN + 1))
      say "  would measure  $rel"
      continue
    fi
    say "${B}measure${N}  $rel"
    if python3 "$CHAPTERFIT" measure "$audio" "${MEASURE_ARGS[@]}"; then
      RAN=$((RAN + 1))
      ok "$rel"
    else
      FAILED=$((FAILED + 1))
      err "measure failed: $rel"
    fi
  done < <(collect_audio "$author_path")
done

say ""
say "  ${FOUND} audio file(s)  skipped ${SKIPPED}  measured ${RAN}  failed ${FAILED}"
(( FAILED == 0 )) || exit 1
(( FOUND > 0 )) || { warn "no audio files under $LIBRARY"; exit 1; }
exit 0
