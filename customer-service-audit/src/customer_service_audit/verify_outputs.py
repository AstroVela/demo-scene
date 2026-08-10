"""Verify the four fixture call analyses directly from MinIO JSON output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .config import DEFAULT_CONFIG_PATH, load_runtime_config
from .fixture_loader import EXPECTED_ANALYSES
from .minio_store import MinioStore


def read_analysis_jsons(config) -> list[dict[str, Any]]:
    """Read all per-call analysis JSON objects from MinIO."""

    store = MinioStore(config.minio)
    store.probe()
    keys = store.list_object_keys(
        config.minio.bucket,
        config.minio.analysis_prefix,
    )
    results: list[dict[str, Any]] = []
    for key in keys:
        if not key.endswith(".json") or "batch_summary" in key:
            continue
        raw = store.get_bytes(config.minio.bucket, key)
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(payload, Mapping):
            results.append(dict(payload))
    return results


def validate_fixture_analyses(
    analyses: Sequence[Mapping[str, Any]],
    expected: Mapping[str, tuple[str, str]] = EXPECTED_ANALYSES,
) -> list[str]:
    """Compare published analyses with the four intended fixture outcomes."""

    errors: list[str] = []
    actual: dict[str, Mapping[str, Any]] = {}

    for analysis in analyses:
        call_id = str(analysis.get("call_id", ""))
        if not call_id:
            errors.append("analysis JSON missing call_id")
            continue
        if call_id in actual:
            errors.append(f"{call_id}: duplicate analysis output")
            continue
        actual[call_id] = analysis

    for call_id, (exp_category, exp_sentiment) in expected.items():
        if call_id not in actual:
            errors.append(f"{call_id}: missing analysis output")
            continue
        analysis = actual[call_id]
        inner = analysis.get("analysis", {})
        if not isinstance(inner, Mapping):
            errors.append(f"{call_id}: analysis field is not an object")
            continue

        status = inner.get("analysis_status", "")
        if status != "success":
            errors.append(
                f"{call_id}: expected analysis_status=success, got {status}"
            )
            continue

        got_category = inner.get("problem_category", "")
        if got_category != exp_category:
            errors.append(
                f"{call_id}: expected problem_category={exp_category}, got {got_category}"
            )

        got_sentiment = inner.get("customer_sentiment", "")
        if got_sentiment != exp_sentiment:
            errors.append(
                f"{call_id}: expected customer_sentiment={exp_sentiment}, got {got_sentiment}"
            )

    for call_id in sorted(set(actual) - set(expected)):
        errors.append(f"{call_id}: unexpected analysis output")
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify customer service audit fixture outputs in MinIO."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Static runtime YAML path.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_runtime_config(args.config)
        analyses = read_analysis_jsons(config)
        errors = validate_fixture_analyses(analyses)
    except Exception as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"verification failed: {error}", file=sys.stderr)
        return 1
    mapping = ", ".join(
        f"{call_id}=({cat},{sent})"
        for call_id, (cat, sent) in sorted(EXPECTED_ANALYSES.items())
    )
    print(f"verified {len(analyses)} call analyses: {mapping}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
