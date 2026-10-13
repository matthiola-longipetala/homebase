#!/usr/bin/env bash
# install.sh — installs the speaker-identification dependencies on the Pi (aarch64, Debian 13).
#
# STRATEGY: a DEDICATED venv (.spk-venv) instead of the system Python 3.13, so the MCP
# server's environment is left untouched (same pattern as the YAMNet .yamnet-venv).
# torch/torchaudio cp313 aarch64 wheels exist, but the real aarch64 risk is
# librosa -> numba -> llvmlite (LLVM build). The piwheels index provides pre-built
# aarch64 wheels, so it is added as an extra index.
#
# Run on the Pi:   bash ~/picarx-mcp/speaker_id/install.sh
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HERE/.spk-venv"

# 1) System packages (arecord is already there; webrtcvad needs python3-dev to build)
echo "== apt dependencies (needs sudo) =="
sudo apt-get update -qq || true
sudo apt-get install -y python3-venv python3-dev build-essential libsndfile1 ffmpeg || true

# 2) venv — prefer 3.12 if present (numba/llvmlite are most mature there on aarch64),
#    otherwise the system python3 (3.13). resemblyzer also works on 3.13 if numba installs.
PYBIN="$(command -v python3.12 || command -v python3)"
echo "== creating venv: $PYBIN -> $VENV =="
"$PYBIN" -m venv "$VENV"
source "$VENV/bin/activate"
pip install --upgrade pip wheel
# setuptools<81 is REQUIRED: webrtcvad imports pkg_resources, which setuptools 81+
# no longer ships (newer versions fail with ModuleNotFoundError)
pip install "setuptools<81"

# 3) piwheels index (pre-built aarch64 wheels: numba, llvmlite, scipy, numpy)
export PIP_EXTRA_INDEX_URL="https://www.piwheels.org/simple"

echo "== torch (CPU) + audio dependencies =="
# CPU-only torch for the smallest footprint. If the CPU index has no matching
# cp3xx aarch64 wheel, fall back to a plain 'pip install torch'.
pip install numpy scipy || true
pip install torch --index-url https://download.pytorch.org/whl/cpu || pip install torch
pip install librosa webrtcvad resemblyzer

echo "== self-check import test =="
python - <<'PY'
import numpy, scipy, torch, librosa, webrtcvad
from resemblyzer import VoiceEncoder, preprocess_wav
print("OK: torch", torch.__version__, "| librosa", librosa.__version__)
enc = VoiceEncoder("cpu")
print("OK: VoiceEncoder loaded (CPU). Dependencies are fine.")
PY

echo
echo "== DONE =="
echo "Enroll:   $VENV/bin/python $HERE/enroll.py <Name>"
echo "Identify: $VENV/bin/python $HERE/identify.py /tmp/utt.wav"
echo "Daemon:   $VENV/bin/python $HERE/speaker_daemon.py   (used by the listen loop)"
