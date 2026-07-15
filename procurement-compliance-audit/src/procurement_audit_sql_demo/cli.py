"""One-command CLI for the release-facing procurement audit demo."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from .config import DEFAULT_CONFIG_PATH, load_runtime_config
from .pipeline import PipelineResult, run_pipeline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the focused Vane procurement compliance SQL demo."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Runtime YAML path.",
    )
    return parser


def _print_result(result: PipelineResult) -> None:
    summary = result.summary
    print("Vane Procurement Audit SQL Demo")
    print(f"Project: {summary['title']} ({summary['project_id']})")
    print(
        f"Result: {summary['status']} | {result.finding_count} findings | "
        f"flagged expert {summary['flagged_expert_id']}"
    )
    print(
        "Winner recalculation: "
        f"{summary['original_winner_supplier_id']} -> "
        f"{summary['winner_without_flagged_expert']}"
    )
    print(f"Outputs: {result.findings_path} | {result.summary_path}")
    print("Vane capabilities exercised:")
    print("  [stateful UDF] RapidOCR engine reused across evidence images")
    print("  [AI Function] Qwen multimodal fact extraction from PNG evidence")
    print("  [stateless UDF] Strict AI JSON contract validation")
    print("  [SQL] Score bias, winner impact, findings, and project summary")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        config = load_runtime_config(args.config)
        result = run_pipeline(config)
    except Exception as exc:
        print(f"demo failed: {exc}", file=sys.stderr)
        return 1
    _print_result(result)
    return 0
