"""One-command CLI for the release-facing procurement audit demo."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from .config import DEFAULT_CONFIG_PATH, load_runtime_config
from .pipeline import PipelineResult, run_pipeline


COMMANDS = ("fixture", "run", "e2e")


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
    print("Sources: PostgreSQL business rows + MinIO evidence objects")
    print("Vane capabilities exercised:")
    print("  [stateful UDF] RapidOCR engine reused across evidence images")
    print("  [AI Function] Qwen multimodal fact extraction from PNG evidence")
    print("  [stateless UDF] Strict AI JSON contract validation")
    print("  [SQL] Score bias, winner impact, findings, and project summary")


def _run(arguments: Sequence[str]) -> int:
    args = _parser().parse_args(list(arguments))
    try:
        config = load_runtime_config(args.config)
        result = run_pipeline(config)
    except Exception as exc:
        print(f"demo failed: {exc}", file=sys.stderr)
        return 1
    _print_result(result)
    return 0


def _run_command(command: str, arguments: list[str]) -> int:
    if command == "fixture":
        from . import fixture_loader

        return fixture_loader.main(arguments)
    if command == "run":
        return _run(arguments)
    raise ValueError(f"unsupported command: {command}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command or the fixed PostgreSQL/MinIO end-to-end flow."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in COMMANDS:
        return _run(arguments)

    command, command_arguments = arguments[0], arguments[1:]
    if command != "e2e":
        return _run_command(command, command_arguments)
    if command_arguments:
        raise SystemExit("usage: run_demo.py e2e")
    for step in ("fixture", "run"):
        result = _run_command(step, [])
        if result:
            return result
    return 0
