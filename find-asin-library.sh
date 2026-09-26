#!/usr/bin/env bash
#
# find-asin-library.sh - run `find_asin` on every audiobook in a library
# that does not already have an ASIN result in the work directory.
#
#   ./find-asin-library.sh /srv/media/books
#   CHAPTERFIT_LIBRARY=/srv/media/books ./find-asin-library.sh
#   ./find-asin-library.sh /srv/media/books --dry-run
#   ./find-asin-library.sh /srv/media/books -- --verify
#
# A book is skipped when the work directory already has
#   <audio>.asin.json
# with at least one candidate ASIN, or when the analysis artifact already
# has an identification stage (chapterfit identify already picked an edition).
# Results are written with find_asin --json; nothing is written beside the audio.
# --verify (slow, decisive) is off unless you pass it after --.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIND_ASIN="${FIND_ASIN:-$SCRIPT_DIR/find_asin.py}"
WORK_ROOT="${CHAPTERFIT_WORK:-$HOME/.local/share/chapterfit}"

LIBRARY=""
DRY_RUN=0
FORCE=0
FIND_ARGS=()

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
usage: $(basename "$0") [LIBRARY] [options] [-- find-asin-options]

  LIBRARY                author-folder root (default \$CHAPTERFIT_LIBRARY)
  -n, --dry-run          list what would run; do not search
  -f, --force            search even when work-dir results already exist
  -h, --help             this help

  Extra arguments after -- go to find_asin (e.g. -- --verify --region uk).
  --json and --silence-cache are set to the work directory and should not
  be passed.

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
    --) shift; FIND_ARGS+=("$@"); break ;;
    -*) die "unknown option $1" ;;
    *) [[ -z "$LIBRARY" ]] && LIBRARY="$1" || die "only one library root"; shift ;;
  esac
done

[[ -n "$LIBRARY" ]] || LIBRARY="${CHAPTERFIT_LIBRARY:-}"
[[ -n "$LIBRARY" ]] || die "pass a library root or set CHAPTERFIT_LIBRARY"
[[ -d "$LIBRARY" ]] || die "no such directory: $LIBRARY"
command -v python3 >/dev/null || die "python3 not found"
command -v ffprobe >/dev/null || die "ffprobe not found (install ffmpeg)"
[[ -f "$FIND_ASIN" ]] || die "find_asin.py not found at $FIND_ASIN"

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

# Exit 0 when the work directory already answers "which edition".
have_asin() {
  local audio="$1" work="$2"
  python3 -c '
import json, os, sys
work, audio = sys.argv[1], sys.argv[2]
base = os.path.basename(audio)
asin_path = os.path.join(work, base + ".asin.json")
try:
    data = json.load(open(asin_path))
except (OSError, ValueError):
    data = None
if isinstance(data, dict):
    for c in data.get("candidates") or []:
        if c.get("asin"):
            sys.exit(0)
art_path = os.path.join(work, base + ".analysis.json")
try:
    art = json.load(open(art_path))
except (OSError, ValueError):
    sys.exit(1)
ident = art.get("identification") or {}
if ident.get("label") or ident.get("asin"):
    sys.exit(0)
sys.exit(1)
' "$work" "$audio"
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

say "${B}${C}find-asin-library${N}  $LIBRARY"
printf '%swork root %s%s\n' "$DIM" "$WORK_ROOT" "$N"
(( FORCE )) && warn "forcing search of books that already have an ASIN"
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
    out="$work/$(basename "$audio").asin.json"
    if (( ! FORCE )) && have_asin "$audio" "$work"; then
      SKIPPED=$((SKIPPED + 1))
      skip "skip  $rel"
      continue
    fi
    if (( DRY_RUN )); then
      RAN=$((RAN + 1))
      say "  would find-asin  $rel"
      continue
    fi
    say "${B}find-asin${N}  $rel"
    mkdir -p "$work" || { FAILED=$((FAILED + 1)); err "cannot create $work"; continue; }
    # --json and --silence-cache last so they win over anything in FIND_ARGS.
    if python3 "$FIND_ASIN" "$audio" "${FIND_ARGS[@]}" \
         --silence-cache "$work" --json "$out"; then
      RAN=$((RAN + 1))
      ok "$rel"
    else
      FAILED=$((FAILED + 1))
      err "find_asin failed: $rel"
    fi
  done < <(collect_audio "$author_path")
done

say ""
say "  ${FOUND} audio file(s)  skipped ${SKIPPED}  searched ${RAN}  failed ${FAILED}"
(( FAILED == 0 )) || exit 1
(( FOUND > 0 )) || { warn "no audio files under $LIBRARY"; exit 1; }
exit 0
