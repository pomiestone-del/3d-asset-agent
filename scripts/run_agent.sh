#!/usr/bin/env bash
# Quick-run wrapper for the asset-agent CLI.
# Usage: ./scripts/run_agent.sh process --obj model.obj --textures ./textures --output ./output

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Ensure the package is installed in dev mode
if ! python -c "import asset_agent" 2>/dev/null; then
    echo "[setup] Installing asset-agent in editable mode…"
    pip install -e . --quiet
fi

exec asset-agent "$@"
