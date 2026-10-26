#!/usr/bin/env bash
# macOS/Linux counterpart of start-local.ps1. Run ./dev.sh setup first.
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python -m uvicorn agent:app --host 127.0.0.1 --port 8000 "$@"
