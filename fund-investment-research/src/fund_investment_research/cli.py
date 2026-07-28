"""Four-command CLI for the Ray-only research Demo."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_runtime_config
from .fixture_loader import SCENARIOS, load_fixture
from .pipeline import run_pipeline
from .verify_outputs import print_verification, verify


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the auditable multimodal fund-investment-research Demo."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)
    fixture = subparsers.add_parser("fixture", help="load PostgreSQL and MinIO fixture")
    fixture.add_argument("--scenario", choices=SCENARIOS, default="default")
    run = subparsers.add_parser("run", help="run the real Ray pipeline")
    run.add_argument("--resume", action="store_true")
    verify_parser = subparsers.add_parser("verify", help="verify published outputs")
    verify_parser.add_argument("--scenario", choices=SCENARIOS, default="default")
    e2e = subparsers.add_parser("e2e", help="fixture + run + verify")
    e2e.add_argument("--scenario", choices=("default", "glossary-before", "glossary-after"), default="default")
    return parser


def _run(config_path: Path, *, resume: bool) -> int:
    config = load_runtime_config(config_path)
    result = run_pipeline(config, resume=resume)
    print(
        f"published {result.signal_count} research signals, {result.fact_count} facts, "
        f"and {result.review_task_count} review tasks"
    )
    print(f"outputs: {result.published.current_dir}")
    print("runner: ray")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        config = load_runtime_config(args.config)
        if args.command == "fixture":
            counts = load_fixture(config, args.scenario)
            print(
                f"loaded {counts['companies']} company, "
                f"{counts['thesis_conditions']} thesis conditions, "
                f"{counts['source_files']} source files, and "
                f"{counts['incoming_signals']} signals "
                f"(scenario={args.scenario})"
            )
            return 0
        if args.command == "run":
            return _run(args.config, resume=args.resume)
        if args.command == "verify":
            result = verify(config, args.scenario)
            print_verification(result)
            return 0 if result.passed else 1
        if args.command == "e2e":
            counts = load_fixture(config, args.scenario)
            print(
                f"loaded {counts['companies']} company, "
                f"{counts['thesis_conditions']} thesis conditions, "
                f"{counts['source_files']} source files, and "
                f"{counts['incoming_signals']} signals"
            )
            if _run(args.config, resume=False) != 0:
                return 1
            result = verify(config, args.scenario)
            print_verification(result)
            return 0 if result.passed else 1
        raise AssertionError(f"unsupported command: {args.command}")
    except Exception as exc:
        print(f"demo failed: {exc}", file=sys.stderr)
        return 1
