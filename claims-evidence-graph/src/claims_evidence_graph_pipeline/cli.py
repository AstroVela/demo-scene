"""Command line entrypoint for the claims evidence graph POC."""

from __future__ import annotations

import argparse

from claims_evidence_graph_pipeline.contracts import (
    DEFAULT_DATA_ROOT,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_WORKSPACE_ROOT,
    ContractError,
    RunConfig,
)
from claims_evidence_graph_pipeline.pipeline import run_pipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build production-oriented claims evidence graph tables.",
    )
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--workspace-root", default=str(DEFAULT_WORKSPACE_ROOT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--mode", choices=["offline", "ai"], default="offline")
    parser.add_argument(
        "--profile",
        choices=["baseline", "semantic", "semantic_strict"],
        default="baseline",
        help=(
            "Run profile. baseline uses deterministic evidence only; semantic "
            "requires VLM evidence but routes failures to review; semantic_strict "
            "fails when image model errors exceed --max-image-model-errors."
        ),
    )
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument(
        "--execution-backend",
        choices=["local", "ray_task", "ray_actor"],
        default="local",
    )
    parser.add_argument("--runner", choices=["local", "ray"], default="local")
    parser.add_argument("--skip-parquet", action="store_true")
    parser.add_argument(
        "--photo-labels-path",
        default=None,
        help="Optional JSONL photo-level human labels for validation/evaluation.",
    )
    parser.add_argument(
        "--fail-on-warnings",
        action="store_true",
        help="Treat validation warnings as fatal for stricter demo gates.",
    )
    parser.add_argument(
        "--image-model-provider",
        default="openai",
        help="Vane AI provider for image understanding in semantic profiles.",
    )
    parser.add_argument(
        "--image-model",
        default="Qwen2.5-VL-3B-Instruct",
        help="Vision-language model name for semantic profiles.",
    )
    parser.add_argument(
        "--image-model-base-url",
        default="http://127.0.0.1:8001/v1",
        help="OpenAI-compatible VLM endpoint for semantic profiles.",
    )
    parser.add_argument(
        "--image-model-api-key",
        default="EMPTY",
        help="API key passed to the image model provider.",
    )
    parser.add_argument(
        "--image-model-max-tokens",
        type=int,
        default=768,
        help="Maximum VLM response tokens per photo.",
    )
    parser.add_argument(
        "--image-model-temperature",
        type=float,
        default=0.0,
        help="VLM sampling temperature.",
    )
    parser.add_argument(
        "--image-model-version",
        default="",
        help="Optional model version label written to audit tables.",
    )
    parser.add_argument(
        "--prompt-version",
        default="photo_damage_v1",
        help="Prompt version label written to model evidence.",
    )
    parser.add_argument(
        "--response-schema-version",
        default="photo_damage_v1",
        help="Structured response schema version label.",
    )
    parser.add_argument(
        "--max-image-model-errors",
        type=int,
        default=0,
        help=(
            "Maximum non-successful image model rows allowed in --mode ai or "
            "--profile semantic_strict."
        ),
    )
    return parser.parse_args()


def main() -> None:
    try:
        run_pipeline(RunConfig.from_args(parse_args()))
    except ContractError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
