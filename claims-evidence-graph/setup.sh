#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
VENV_DIR="${VENV_DIR:-$SCRIPT_DIR/.venv}"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$SCRIPT_DIR/workspace/quality-fixtures}"
OUTPUT_DIR="${OUTPUT_DIR:-$WORKSPACE_ROOT/outputs}"

echo "============================================"
echo "  Claims Evidence Graph Demo Setup"
echo "============================================"
echo ""

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
    echo "Error: $PYTHON_BIN is required but was not found."
    exit 1
}

if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating virtual environment: $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
else
    echo "==> Reusing virtual environment: $VENV_DIR"
fi

echo "==> Installing demo dependencies..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV_DIR/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"

echo "==> Running synthetic fixture profile..."
"$VENV_DIR/bin/claims-evidence-graph-quality-fixtures" \
    --workspace-root "$WORKSPACE_ROOT" \
    --output-dir "$OUTPUT_DIR" \
    --skip-parquet

echo ""
echo "============================================"
echo "  Demo complete"
echo "============================================"
echo ""
echo "  Output directory:       $OUTPUT_DIR"
echo "  Validation report:      $OUTPUT_DIR/validation_report.json"
echo "  Run metadata:           $OUTPUT_DIR/run_metadata.json"
echo "  Example DuckDB queries: queries.sql"
echo ""
echo "  Cleanup generated data: ./teardown.sh"
echo "  Cleanup data + venv:    ./teardown.sh -v"
echo ""
