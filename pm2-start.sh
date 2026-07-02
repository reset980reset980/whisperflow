#!/usr/bin/env bash
# PM2 launcher for the WhisperFlow Linux server.
# Keeps the JARVIS backend alive so jarvis.xsw.kr (Caddy → 127.0.0.1:8767)
# stays reachable across sessions/reboots.
#   pm2 start pm2-start.sh --name jarvis && pm2 save
set -euo pipefail
cd "$(dirname "$0")"
exec ./.venv/bin/python -m whisperflow.server
