#!/usr/bin/env bash
# Create the local environment if needed, then open the GUI.
#   ./runme.sh
#   ./runme.sh /path/to/library

set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi

if ! .venv/bin/python -c "import numpy, scipy, soundfile, noisereduce, pedalboard, pyloudnorm, sounddevice" >/dev/null 2>&1; then
  .venv/bin/python -m pip install -U pip
  .venv/bin/python -m pip install -e '.[quality]'
fi

if ! .venv/bin/python -c "import customtkinter" >/dev/null 2>&1; then
  .venv/bin/python -m pip install -U pip
  .venv/bin/python -m pip install -e '.[gui]'
fi

# DeepFilterNet's published pin wants numpy<2. resemble-enhance 0.0.1 wants
# torch==2.1.1, which has no Python 3.12 wheel, so it is not installed.
# The CPU index avoids the default CUDA build on this machine. DeepFilterLib
# has no cp312 wheel, so this builds it (rustc required).
if [[ -x "${HOME}/.cargo/bin/rustc" ]]; then
  export PATH="${HOME}/.cargo/bin:${PATH}"
fi
if ! .venv/bin/python -c "from chaptertools.audio_quality.neural import load_deepfilter; load_deepfilter()" >/dev/null 2>&1; then
  .venv/bin/python -m pip install -U pip
  .venv/bin/python -m pip install 'torch==2.14.0+cpu' 'torchaudio==2.11.0+cpu' \
    --index-url https://download.pytorch.org/whl/cpu
  .venv/bin/python -m pip install loguru 'appdirs>=1.4,<2' 'sympy>=1.6' 'requests>=2.27,<3'
  .venv/bin/python -m pip install 'deepfilternet>=0.5' 'deepfilterlib==0.5.6' --no-deps
fi

if ! command -v ffmpeg >/dev/null || ! command -v mpv >/dev/null; then
  sudo apt-get update
  sudo apt-get install -y ffmpeg mpv
fi

if [[ $# -gt 0 ]]; then
  exec .venv/bin/python chaptergui.py "$@"
fi
exec .venv/bin/python chaptergui.py "${CHAPTERFIT_LIBRARY:-/srv/media/books}"
