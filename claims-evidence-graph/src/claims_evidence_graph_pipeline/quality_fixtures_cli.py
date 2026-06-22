"""CLI for generating deterministic quality fixtures and running the POC."""

from __future__ import annotations

import argparse
from pathlib import Path

from claims_evidence_graph_pipeline.contracts import RunConfig
from claims_evidence_graph_pipeline.pipeline import run_pipeline
from claims_evidence_graph_pipeline.quality_fixtures import (
    build_quality_fixture_workspace,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate claims quality fixture data for local validation.",
    )
    parser.add_argument(
        "--workspace-root",
        default="tmp/claims-poc-quality-fixture-workspace",
    )
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument(
        "--execution-backend",
        choices=["local", "ray_task", "ray_actor"],
        default="local",
    )
    parser.add_argument("--runner", choices=["", "ray"], default="")
    parser.add_argument("--skip-parquet", action="store_true")
    parser.add_argument("--skip-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = build_quality_fixture_workspace(
        Path(args.workspace_root),
        data_root=Path(args.data_root) if args.data_root else None,
        output_dir=Path(args.output_dir) if args.output_dir else None,
    )

    print(f"Workspace root: {paths.workspace_root}")
    print(f"Data root: {paths.data_root}")
    print(f"Photo labels: {paths.photo_labels_path}")
    print(f"Output dir: {paths.output_dir}")

    if args.skip_run:
        return

    run_pipeline(
        RunConfig(
            data_root=paths.data_root,
            workspace_root=paths.workspace_root,
            output_dir=paths.output_dir,
            batch_size=args.batch_size,
            execution_backend=args.execution_backend,
            runner=args.runner,
            write_parquet=not args.skip_parquet,
            photo_labels_path=paths.photo_labels_path,
        )
    )


if __name__ == "__main__":
    main()
