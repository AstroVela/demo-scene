"""Verify the four fixture dispositions directly from PostgreSQL output."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Mapping, Sequence

from psycopg import sql

from .config import DEFAULT_CONFIG_PATH, load_runtime_config
from .output_writer import validate_rows
from .pg import connect_postgres


EXPECTED_DISPOSITIONS = {
    "CLM-APPROVE": "approve_for_payment",
    "CLM-DENY": "deny_claim",
    "CLM-MISSING": "request_more_materials",
    "CLM-REVIEW": "manual_review",
}


def read_output_rows(config) -> list[dict]:
    query = sql.SQL("select * from {}.{} order by claim_id").format(
        sql.Identifier(config.output_schema),
        sql.Identifier(config.output_table),
    )
    with connect_postgres(config) as connection:
        return list(connection.execute(query).fetchall())


def validate_fixture_rows(
    rows: Sequence[Mapping],
    expected: Mapping[str, str] = EXPECTED_DISPOSITIONS,
) -> list[str]:
    errors: list[str] = []
    actual: dict[str, str] = {}
    for row in rows:
        claim_id = str(row.get("claim_id", ""))
        if claim_id in actual:
            errors.append(f"{claim_id}: duplicate output row")
            continue
        actual[claim_id] = str(row.get("disposition", ""))

    for claim_id, disposition in expected.items():
        if claim_id not in actual:
            errors.append(f"{claim_id}: missing expected output row")
        elif actual[claim_id] != disposition:
            errors.append(
                f"{claim_id}: expected {disposition}, got {actual[claim_id]}"
            )
    for claim_id in sorted(set(actual) - set(expected)):
        errors.append(f"{claim_id}: unexpected output row")
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify claims disposition fixture outputs in PostgreSQL."
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
        rows = read_output_rows(config.postgres)
        validate_rows(rows)
        errors = validate_fixture_rows(rows)
    except Exception as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"verification failed: {error}", file=sys.stderr)
        return 1
    mapping = ", ".join(
        f"{claim_id}={disposition}"
        for claim_id, disposition in EXPECTED_DISPOSITIONS.items()
    )
    print(f"verified {len(rows)} claim dispositions: {mapping}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
