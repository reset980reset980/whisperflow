#!/usr/bin/env bash
# WhisperFlow — Linux (headless web) launcher.
# Creates a venv on first run, installs deps, then starts the JARVIS server.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "[run-linux] creating virtualenv (.venv)…"
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip
  ./.venv/bin/python -m pip install -r requirements-linux.txt
fi

if [ ! -f .env ]; then
  echo "[run-linux] no .env found — copying .env.example."
  echo "            Edit .env and set OPENAI_API_KEY before real use."
  cp .env.example .env
fi

exec ./.venv/bin/python -m whisperflow.server
