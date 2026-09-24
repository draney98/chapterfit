#!/usr/bin/env bash
#
# chapterbook.sh - take one audiobook from raw file to Audiobookshelf.
#
# Ordered by cost. Every stage is skipped when the artifact already answers
# the question, and each escalation is probed cheaply before it is committed
# to. The expensive things - the envelope, and Whisper - run once or not at
# all.
#
#   ./chapterbook.sh "Greg Bear/Eon/Eon.mp3" --asin B007MF0J3Y
#   ./chapterbook.sh book.mp3 --chapters-json local.json -y
#   ./chapterbook.sh book.mp3 --near 2=4:48 --group audiobookshelf
#
# Re-running is cheap and safe: it resumes from whatever is already known.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHAPTERFIT="${CHAPTERFIT:-$SCRIPT_DIR/chapterfit.py}"
FIND_ASIN="${FIND_ASIN:-$SCRIPT_DIR/find_asin.py}"
WORK_ROOT="${CHAPTERFIT_WORK:-$HOME/.local/share/chapterfit}"
REQUIRED_CF="1.11.0"

AUDIO=""; ASIN=""; CHAPTERS_JSON=""; GROUP=""; REGION="us"; NEAR=""
ENV_FILE=""; MIN_CONF="0.5"; WORK=""; ASSUME_YES=0
WHISPER_MODEL="${WHISPER_MODEL:-base}"
CONFIRM_TARGET="${CONFIRM_TARGET:-12}"
SWEEP_PROBE_MIN="${SWEEP_PROBE_MIN:-10}"

if [[ -t 1 ]]; then
  B=$'\033[1m'; DIM=$'\033[2m'; R=$'\033[31m'; G=$'\033[32m'
  Y=$'\033[33m'; C=$'\033[36m'; N=$'\033[0m'
else B=""; DIM=""; R=""; G=""; Y=""; C=""; N=""; fi

say()   { printf '%s\n' "$*"; }
head1() { printf '\n%s%s%s\n' "$B$C" "$*" "$N"; }
ok()    { printf '%s  ok%s  %s\n' "$G" "$N" "$*"; }
warn()  { printf '%s  !%s   %s\n' "$Y" "$N" "$*"; }
err()   { printf '%s  x%s   %s\n' "$R" "$N" "$*" >&2; }
die()   { err "$*"; exit 1; }
note()  { printf '%s      %s%s\n' "$DIM" "$*" "$N"; }

usage() {
  cat <<EOF
usage: $(basename "$0") AUDIO_FILE [options]

  --asin ASIN            Audible ASIN for the chapter list
  --chapters-json FILE   local chapter list instead of Audnexus
  --near CH=TIME         roughly where a chapter starts (aims the search)
  --region CODE          Audible region (default us)
  --group GROUP          chgrp the finished file, e.g. audiobookshelf
  --env FILE             .env with ABS_URL / ABS_TOKEN
  --model NAME           whisper model (default $WHISPER_MODEL)
  --confirm N            chapters to confirm from the narration (default $CONFIRM_TARGET)
  --min-confidence N     confidence needed to emit (default $MIN_CONF)
  --work-dir DIR         analysis artifact location
                         (default \$CHAPTERFIT_WORK/<author>/<book>, CHAPTERFIT_WORK
                         defaulting to ~/.local/share/chapterfit)
  -y, --yes              take the recommended option at every prompt
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage ;;
    --asin) ASIN="$2"; shift 2 ;;
    --chapters-json) CHAPTERS_JSON="$2"; shift 2 ;;
    --near) NEAR="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --group) GROUP="$2"; shift 2 ;;
    --env) ENV_FILE="$2"; shift 2 ;;
    --model) WHISPER_MODEL="$2"; shift 2 ;;
    --confirm) CONFIRM_TARGET="$2"; shift 2 ;;
    --min-confidence) MIN_CONF="$2"; shift 2 ;;
    --work-dir) WORK="$2"; shift 2 ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    -*) die "unknown option $1" ;;
    *) [[ -z "$AUDIO" ]] && AUDIO="$1" || die "only one audio file"; shift ;;
  esac
done
[[ -n "$AUDIO" ]] || usage
[[ -f "$AUDIO" ]] || die "no such file: $AUDIO"
command -v python3 >/dev/null || die "python3 not found"
command -v ffprobe >/dev/null || die "ffprobe not found (install ffmpeg)"
[[ -f "$CHAPTERFIT" ]] || die "chapterfit.py not found at $CHAPTERFIT"

CF_VERSION="$(python3 "$CHAPTERFIT" version 2>/dev/null | awk '{print $2}')"
[[ -n "$CF_VERSION" ]] || die "chapterfit.py is too old to report a version (needs $REQUIRED_CF)"
if [[ "$(printf '%s\n%s\n' "$REQUIRED_CF" "$CF_VERSION" | sort -V | head -1)" != "$REQUIRED_CF" ]]; then
  die "chapterfit.py is $CF_VERSION, needs $REQUIRED_CF or newer"
fi

AUDIO="$(cd "$(dirname "$AUDIO")" && pwd)/$(basename "$AUDIO")"
FOLDER="$(dirname "$AUDIO")"
[[ -n "$WORK" ]] || WORK="$WORK_ROOT/$(basename "$(dirname "$FOLDER")")/$(basename "$FOLDER")"
# Undo pasted shell escapes ("Harry\ Harrison/...") before mkdir; otherwise
# literal backslash-space names appear beside real author folders.
RAW_WORK="$WORK"
WORK="${WORK//\\ / }"
WORK="$(realpath -m "$WORK")"
if [[ "$RAW_WORK" == *\\* ]]; then
  warn "work-dir had shell escapes; using $WORK"
fi
mkdir -p "$WORK" || die "cannot create $WORK"
CF=(python3 "$CHAPTERFIT" --work-dir "$WORK")

# Whisper lives in its own venv because Debian refuses system-wide pip installs.
WPY="${WHISPER_PYTHON:-$HOME/.venvs/whisper/bin/python}"
HAVE_WHISPER=0
[[ -x "$WPY" ]] && "$WPY" -c "import faster_whisper" >/dev/null 2>&1 && HAVE_WHISPER=1
WCF=("$WPY" "$CHAPTERFIT" --work-dir "$WORK")

art() {
  local f
  for f in "$WORK"/*.analysis.json; do
    [[ -e "$f" ]] || continue
    python3 -c '
import json,sys
try:
    d=json.load(open(sys.argv[1]))
    v=eval(sys.argv[2],{"d":d,"len":len,"sum":sum,"round":round,"max":max,"min":min})
    print("" if v is None else v)
except Exception: print("")' "$f" "$1"
    return
  done
  printf ''
}
ask() {
  local p="$1" d="${2:-}" r
  if (( ASSUME_YES )) && [[ -n "$d" ]]; then printf '%s [%s]\n' "$p" "$d" >&2; printf '%s' "$d"; return; fi
  if [[ -t 0 ]]; then read -r -p "$p${d:+ [$d]}: " r || r=""; else read -r r || r=""; fi
  printf '%s' "${r:-$d}"
}
confirm() { local a; a="$(ask "$1 (y/n)" "${2:-y}")"; [[ "${a,,}" == y* ]]; }

WEAK=0; PLACED=0; TOTAL=0
KNOWN_SOURCE=""; KNOWN_PINS=0; KNOWN_HEARD=0; KNOWN_SWEPT=0; KNOWN_ALIGN=0
recap() {
  KNOWN_SOURCE="$(art 'd["identification"]["label"]')"
  KNOWN_PINS="$(art 'len(d.get("pins") or {})')"
  KNOWN_HEARD="$(art 'sum(1 for p in d.get("heard",{}).get("positions",[]) if p.get("heading"))')"
  KNOWN_SWEEPHITS="$(art 'sum(1 for p in d.get("heard",{}).get("positions",[]) if p.get("source")=="sweep")')"
  # sweep chunks are cached as "start|length|sweep:model"; total the lengths
  KNOWN_SWEPT="$(art 'round(sum(float(k.split("|")[1]) for k in d.get("heard",{}).get("clips",{}) if "sweep" in k)/60,1)')"
  KNOWN_ALIGN="$(art 'len(d.get("alignment",{}).get("results",[]))')"
  : "${KNOWN_PINS:=0}" "${KNOWN_HEARD:=0}" "${KNOWN_SWEPT:=0}" "${KNOWN_ALIGN:=0}"
}
assess() {
  TOTAL="$(art 'len(d["alignment"]["results"])')"
  PLACED="$(art 'sum(1 for r in d["alignment"]["results"] if r["time"] is not None and r["confidence"]>='"$MIN_CONF"')')"
  WEAK=$(( ${TOTAL:-0} - ${PLACED:-0} ))
}
align_now() {
  local a=("${CF[@]}" align "$AUDIO" --min-confidence "$MIN_CONF")
  [[ -n "$NEAR" ]] && a+=(--near "$NEAR")
  "${a[@]}" >/dev/null 2>&1 || return 1
  assess
  say "  ${PLACED}/${TOTAL} chapters confident   rate $(art 'round(d["alignment"]["rate"] or 1,6)')"
}

# ===================================================== 1. measure (cached) ===
head1 "1  measure"
"${CF[@]}" measure "$AUDIO" || die "measure failed"
DUR="$(art 'round(d["envelope"]["duration"],1)')"
[[ -n "$DUR" ]] || die "no artifact written"
if [[ "$(art 'd["source"]["timeline_trustworthy"]')" != "True" ]]; then
  warn "file header is wrong by $(art 'round(d["source"]["header_error_sec"],1)')s"
  note "every timestamp checked by ear will be off, and the error grows"
  note "ffmpeg -i \"$AUDIO\" -c:a copy -write_xing 1 FIXED.mp3"
  confirm "continue anyway?" n || exit 1
fi
ok "$(art 'len(d["gaps"]["gaps"])') gaps in $(art 'len(d["gaps"]["populations"])') population(s)"
[[ "$(art 'd["fingerprint"]["degapped"]')" == "True" ]] && \
  note "no long gaps: joins were likely trimmed, so silence may not mark chapters"

recap
if [[ -n "$KNOWN_SOURCE" || "$KNOWN_PINS" != "0" || "$KNOWN_HEARD" != "0" ]]; then
  say ""
  say "  already known from previous runs:"
  [[ -n "$KNOWN_SOURCE" ]]      && note "edition: $KNOWN_SOURCE"
  [[ "$KNOWN_PINS" != "0" ]]    && note "$KNOWN_PINS pin(s) recorded"
  [[ "$KNOWN_HEARD" != "0" ]]   && note "$KNOWN_HEARD chapter announcement(s) transcribed"
  awk "BEGIN{exit !($KNOWN_SWEPT > 0)}" && note "${KNOWN_SWEPT} min already swept"
fi

# ====================================================== 2. the chapter list ==
head1 "2  identify the edition"
WANT=""
[[ -n "$ASIN" ]] && WANT="Audnexus $ASIN"
[[ -n "$CHAPTERS_JSON" ]] && WANT="$CHAPTERS_JSON"
if [[ -n "$WANT" && "$KNOWN_SOURCE" == "$WANT" ]]; then
  ok "already identified as $KNOWN_SOURCE - not re-running"
  SCORE="$(art 'round(d["identification"]["best_trial"]["score"],2)')"
  note "score ${SCORE:-?}"
else
while :; do
  if [[ -n "$CHAPTERS_JSON" ]]; then
    "${CF[@]}" identify "$AUDIO" --chapters-json "$CHAPTERS_JSON"
  else
    if [[ -z "$ASIN" ]]; then
      if [[ -f "$FIND_ASIN" ]] && confirm "search Audible for the edition?" y; then
        python3 "$FIND_ASIN" "$AUDIO" --verify --region "$REGION" \
          --noise "$(art 'd["calibration"]["threshold_db"]')" --tolerance 10 || true
      fi
      ASIN="$(ask "Audible ASIN (blank for a local chapters json)")"
      if [[ -z "$ASIN" ]]; then
        CHAPTERS_JSON="$(ask "path to chapters json")"
        [[ -f "$CHAPTERS_JSON" ]] || { err "no such file"; CHAPTERS_JSON=""; }
        continue
      fi
    fi
    "${CF[@]}" identify "$AUDIO" --asin "$ASIN" --region "$REGION"
  fi
  SCORE="$(art 'round(d["identification"]["best_trial"]["score"],2)')"
  [[ -n "$SCORE" ]] || SCORE=0
  if awk "BEGIN{exit !($SCORE >= 5)}"; then ok "edition fits the audio"; break
  elif awk "BEGIN{exit !($SCORE >= 2)}"; then
    warn "weak fit (score $SCORE) - the transcript routes below can still fix it"; break
  else
    err "rejected (score $SCORE) - wrong edition"
    confirm "try a different one?" y && { ASIN=""; CHAPTERS_JSON=""; } || exit 1
  fi
done
fi
recap

# ============================================ 3. align - free, so try first ==
head1 "3  align"
align_now || die "align failed"
if (( WEAK == 0 )); then
  ok "everything placed with no transcription needed"
else
  warn "${WEAK} chapter(s) unconvincing"
fi

# ==================================== 4. escalate in order of what it costs ==
if (( WEAK > 0 )); then
  head1 "4  resolve the rest"

  # -- 4a. a hint is free, but pins are strictly better ---------------------
  if [[ -z "$NEAR" && "$KNOWN_PINS" != "0" ]]; then
    note "$KNOWN_PINS pin(s) already recorded - skipping the hint, which is weaker"
  elif [[ -z "$NEAR" ]]; then
    say "  Cheapest fix: if you know roughly where any chapter starts - even to"
    say "  the nearest minute - that aims the search and costs nothing."
    NEAR="$(ask "approximate position as CH=TIME (blank to skip)")"
    [[ -n "$NEAR" ]] && { align_now || true; }
  fi

  # -- 4b. listen: minutes of audio, only beside gaps -----------------------
  if (( WEAK > 0 )) && (( HAVE_WHISPER )); then
    say ""
    if [[ "$KNOWN_HEARD" != "0" ]]; then
      say "  listen has already run and found $KNOWN_HEARD announcement(s); re-running"
      say "  only transcribes what it has not already heard."
    else
      say "  Next: let the narrator say it. 'listen' transcribes ~13s after"
      say "  candidate gaps only - about $(python3 -c "print(f'{(6+$CONFIRM_TARGET)*13/60:.1f}')") min of audio for $CONFIRM_TARGET confirmations."
    fi
    LISTEN_DEFAULT=y
    LISTENED="$(art 'len([k for k in d.get("heard",{}).get("clips",{}) if "sweep" not in k])')"
    if [[ "${LISTENED:-0}" != "0" && "$KNOWN_HEARD" == "0" ]]; then
      say "  (a previous run transcribed ${LISTENED} position(s) and heard nothing,"
      say "   so this is unlikely to help - sweep is the next step)"
      LISTEN_DEFAULT=n
    fi
    if confirm "run listen?" "$LISTEN_DEFAULT"; then
      "${WCF[@]}" listen "$AUDIO" --model "$WHISPER_MODEL" \
                  --confirm "$CONFIRM_TARGET" --pin -y || warn "listen failed"
      align_now || true
      recap
    fi
  elif (( WEAK > 0 )); then
    note "no whisper venv at $WPY - transcript routes unavailable"
    note "python3 -m venv ~/.venvs/whisper && ~/.venvs/whisper/bin/pip install faster-whisper"
  fi

  # -- 4c. sweep, probed before it is committed to --------------------------
  if (( WEAK > 0 )) && (( HAVE_WHISPER )); then
    HEARD="$(art 'sum(1 for p in d.get("heard",{}).get("positions",[]) if p.get("heading"))')"
    say ""
    if [[ "${HEARD:-0}" == "0" ]]; then
      say "  listen heard nothing, so the breaks carry no usable silence."
      say "  'sweep' reads continuous audio and needs no gap, but the whole book"
      say "  is $(python3 -c "print(f'{$DUR/60:.0f}')") min of transcription. Probe ${SWEEP_PROBE_MIN} min first to find out"
      say "  whether chapters are announced at all."
      if awk "BEGIN{exit !($KNOWN_SWEPT > 0)}"; then
        note "${KNOWN_SWEPT} min already swept and cached - that part is free to redo"
      fi
      if confirm "probe?" y; then
        PS="$(python3 -c "print(int($DUR*0.25))")"
        PE="$(python3 -c "print(int($DUR*0.25)+$SWEEP_PROBE_MIN*60)")"
        "${WCF[@]}" sweep "$AUDIO" --model "$WHISPER_MODEL" \
                    --start "$PS" --end "$PE" --pin -y || warn "probe failed"
        align_now || true
        recap
        if (( WEAK > 0 )); then
          say ""
          say "  If the probe found announcements, sweeping the rest resolves"
          say "  everything. If it found none, it will not."
          confirm "sweep the whole book (~$(python3 -c "print(f'{$DUR/600:.0f}')") min of compute)?" n && {
            "${WCF[@]}" sweep "$AUDIO" --model "$WHISPER_MODEL" --pin -y || warn "sweep failed"
            align_now || true; recap; }
        fi
      fi
    else
      say "  listen found announcements but some chapters remain. A targeted"
      say "  sweep around those is cheaper than sweeping the book."
      "${CF[@]}" align "$AUDIO" --min-confidence "$MIN_CONF" 2>/dev/null \
        | sed -n '/measure these/,$p' | head -12
      RANGE="$(ask "sweep range as START-END (blank to skip)")"
      if [[ -n "$RANGE" ]]; then
        "${WCF[@]}" sweep "$AUDIO" --model "$WHISPER_MODEL" \
                    --start "${RANGE%%-*}" --end "${RANGE##*-}" --pin -y || warn "sweep failed"
        align_now || true
      fi
    fi
  fi

  # -- 4d. by ear, last because it costs your time --------------------------
  while (( WEAK > 0 )); do
    say ""
    warn "${WEAK} chapter(s) still unresolved - checking by ear"
    "${CF[@]}" align "$AUDIO" --min-confidence "$MIN_CONF" 2>/dev/null \
      | sed -n '/measure these/,$p' | head -16
    CH="$(ask "chapter number to pin (blank to accept what we have)")"
    [[ -n "$CH" ]] || break
    EXPECT="$(art 'next((r["time"] for r in d["alignment"]["results"] if r["index"]=='"$CH"'-1), "")')"
    [[ -n "$EXPECT" ]] || { err "chapter $CH has no placement"; continue; }
    START="$(python3 -c "t=max(0,float('$EXPECT')-3);print(f'{int(t//3600)}:{int(t%3600//60):02d}:{int(t%60):02d}')")"
    if command -v mpv >/dev/null && confirm "play from $START?" y; then
      mpv --start="$START" --no-video "$AUDIO" || true
    else note "mpv --start=$START \"$AUDIO\""; fi
    TIME="$(ask "true start of chapter $CH (blank to skip)")"
    [[ -n "$TIME" ]] || continue
    "${CF[@]}" pin "$AUDIO" --at "$CH=$TIME" >/dev/null || { err "pin failed"; continue; }
    align_now || true
    recap
  done
fi

(( WEAK == 0 )) && ok "all ${TOTAL} chapters placed" || \
  warn "${WEAK} chapter(s) will be withheld from the output"

# ============================================================== 5. write it ==
head1 "5  write the chapters"
ROOT="${AUDIO%.*}"; EXT="${AUDIO##*.}"
CHAPTERED="$ROOT.chapters.$EXT"
if [[ -f "$CHAPTERED" ]]; then
  # Judge freshness on content, not timestamps: the analysis file is rewritten
  # by every align, so mtime always looks newer than the audio.
  HAVE_N="$(ffprobe -v error -show_chapters -of csv=p=0 "$CHAPTERED" | wc -l)"
  HAVE_LAST="$(ffprobe -v error -show_chapters -of csv=p=0 "$CHAPTERED" \
               | tail -1 | cut -d, -f4 | awk '{printf "%.1f", $1}')"
  WANT_N="$(art 'd["emitted"]["count"]')"
  WANT_LAST="$(art 'round(d["emitted"]["last"],1)' | awk '{printf "%.1f", $1}')"
  if [[ -n "$WANT_N" && "$HAVE_N" == "$WANT_N" && "$HAVE_LAST" == "$WANT_LAST" ]]; then
    ok "$(basename "$CHAPTERED") already holds exactly these ${HAVE_N} chapters"
    confirm "re-embed anyway?" n || SKIP_EMIT=1
  else
    note "$(basename "$CHAPTERED") has ${HAVE_N} chapters; the analysis now says ${WANT_N:-?}"
  fi
fi
if [[ "${SKIP_EMIT:-0}" != "1" ]]; then
say "  ${PLACED} chapters will be embedded; the rest are withheld."
confirm "embed them?" y || { say "  stopping here."; exit 0; }
"${CF[@]}" emit "$AUDIO" --embed \
        --chapters-txt "$WORK/chapters.txt" --abs-json "$WORK/chapters.json" \
        || die "emit failed"
[[ -f "$CHAPTERED" ]] || die "expected $CHAPTERED"
ok "$(ffprobe -v error -show_chapters -of csv=p=0 "$CHAPTERED" | wc -l) chapters embedded"
note "paste-ready list: $WORK/chapters.txt"
fi

# =========================================================== 6. hand it over =
head1 "6  Audiobookshelf"
FIN=("${CF[@]}" finalize "$AUDIO")
[[ -n "$GROUP" ]] && FIN+=(--group "$GROUP")
[[ -n "$ENV_FILE" ]] && FIN+=(--env "$ENV_FILE")
"${FIN[@]}" || die "finalize dry run failed"
say ""
confirm "apply it?" n || { note "${FIN[*]} --apply"; exit 0; }
"${FIN[@]}" --apply || die "finalize failed"

head1 "done"
say "  $AUDIO"
note "analysis and transcripts kept in $WORK"
note "displaced files under /tmp/chapterfit - move them if you want them kept"
