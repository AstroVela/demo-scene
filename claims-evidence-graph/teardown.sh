#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$SCRIPT_DIR/workspace/quality-fixtures}"
REMOVE_VENV="false"

while getopts "v" opt; do
    case "$opt" in
        v) REMOVE_VENV="true" ;;
        *)
            echo "Usage: ./teardown.sh [-v]"
            exit 1
            ;;
    esac
done

echo "==> Removing generated workspace: $WORKSPACE_ROOT"
rm -rf "$WORKSPACE_ROOT"

if [ "$REMOVE_VENV" = "true" ]; then
    echo "==> Removing virtual environment: $SCRIPT_DIR/.venv"
    rm -rf "$SCRIPT_DIR/.venv"
fi

echo "Done."
