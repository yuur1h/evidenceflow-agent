#!/usr/bin/env bash
# Developer entry points. Requires uv (https://docs.astral.sh/uv/).
#   ./dev.sh setup   create .venv from uv.lock (Python 3.12)
#   ./dev.sh test    offline regression suite + smoke checks
#   ./dev.sh lock    refresh uv.lock after editing pyproject.toml
set -euo pipefail
cd "$(dirname "$0")"
case "${1:-}" in
  setup) uv sync --frozen ;;
  test)  uv run --frozen pytest tests -q && uv run --frozen python smoke_local.py ;;
  lock)  uv lock ;;
  *)     sed -n '2,6p' "$0"; exit 1 ;;
esac
