"""Validate and atomically publish the SQL mart snapshot to PostgreSQL."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Mapping, Sequence

from psycopg import sql
from psycopg.types.json import Jsonb

from .config import RuntimeConfig
from .pg import OUTPUT_DDL, connect_postgres


OUTPUT_COLUMNS = (
    "claim_id",
    "disposition",
    "disposition_confidence",
    "primary_reason_code",
    "reason_summary",
    "next_action",
    "supporting_facts_json",
    "created_by",
    "decided_at",
)
ALLOWED_DISPOSITIONS = {
    "approve_for_payment",
    "deny_claim",
    "request_more_materials",
    "manual_review",
}


class OutputContractError(ValueError):
    """Raised before publication when a mart row violates the output contract."""


def _text(row: Mapping[str, Any], field: str, claim_id: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise OutputContractError(f"{claim_id}: {field} must be non-empty text")
    return value.strip()


def _confidence(value: Any, claim_id: str) -> Decimal:
    if isinstance(value, bool):
        raise OutputContractError(
            f"{claim_id}: disposition_confidence must be between 0 and 1"
        )
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise OutputContractError(
            f"{claim_id}: disposition_confidence must be numeric"
        ) from exc
    if not result.is_finite() or not Decimal("0") <= result <= Decimal("1"):
        raise OutputContractError(
            f"{claim_id}: disposition_confidence must be between 0 and 1"
        )
    return result


def _facts(value: Any, claim_id: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise OutputContractError(
                f"{claim_id}: supporting_facts_json must be valid JSON"
            ) from exc
    if not isinstance(value, Mapping):
        raise OutputContractError(
            f"{claim_id}: supporting_facts_json must be an object"
        )
    return dict(value)


def _timestamp(value: Any, claim_id: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OutputContractError(f"{claim_id}: decided_at is invalid") from exc
    else:
        raise OutputContractError(f"{claim_id}: decided_at is invalid")
    if result.tzinfo is None or result.utcoffset() is None:
        raise OutputContractError(f"{claim_id}: decided_at must be timezone-aware")
    return result


def validate_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize the mart and enforce the PostgreSQL output contract."""

    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise OutputContractError("output payload must be a list of rows")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    required = set(OUTPUT_COLUMNS)
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise OutputContractError(f"row {index} must be an object")
        actual = set(row)
        if actual != required:
            missing = sorted(required - actual)
            extra = sorted(actual - required)
            raise OutputContractError(
                f"row {index} has wrong columns; missing={missing}, extra={extra}"
            )
        claim_id = _text(row, "claim_id", f"row {index}")
        if claim_id in seen:
            raise OutputContractError(f"duplicate claim_id: {claim_id}")
        seen.add(claim_id)
        disposition = _text(row, "disposition", claim_id)
        if disposition not in ALLOWED_DISPOSITIONS:
            raise OutputContractError(f"{claim_id}: invalid disposition {disposition}")
        normalized.append(
            {
                "claim_id": claim_id,
                "disposition": disposition,
                "disposition_confidence": _confidence(
                    row["disposition_confidence"], claim_id
                ),
                "primary_reason_code": _text(row, "primary_reason_code", claim_id),
                "reason_summary": _text(row, "reason_summary", claim_id),
                "next_action": _text(row, "next_action", claim_id),
                "supporting_facts_json": _facts(
                    row["supporting_facts_json"], claim_id
                ),
                "created_by": _text(row, "created_by", claim_id),
                "decided_at": _timestamp(row["decided_at"], claim_id),
            }
        )
    return normalized


def replace_output_rows(
    rows: Sequence[Mapping[str, Any]],
    config: RuntimeConfig,
) -> int:
    """Replace the published disposition snapshot in one transaction."""

    normalized = validate_rows(rows)
    postgres = config.postgres
    delete_query = sql.SQL("delete from {}.{}").format(
        sql.Identifier(postgres.output_schema),
        sql.Identifier(postgres.output_table),
    )
    insert_query = sql.SQL(
        """
        insert into {}.{} (
          claim_id,
          disposition,
          disposition_confidence,
          primary_reason_code,
          reason_summary,
          next_action,
          supporting_facts_json,
          created_by,
          decided_at
        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
    ).format(
        sql.Identifier(postgres.output_schema),
        sql.Identifier(postgres.output_table),
    )
    values = [
        (
            row["claim_id"],
            row["disposition"],
            row["disposition_confidence"],
            row["primary_reason_code"],
            row["reason_summary"],
            row["next_action"],
            Jsonb(row["supporting_facts_json"]),
            row["created_by"],
            row["decided_at"],
        )
        for row in normalized
    ]

    with connect_postgres(postgres) as connection:
        connection.execute(OUTPUT_DDL)
        connection.execute(delete_query)
        with connection.cursor() as cursor:
            cursor.executemany(insert_query, values)
    return len(normalized)


def write_payload_json(payload: str, config: RuntimeConfig) -> int:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OutputContractError("publication payload must be valid JSON") from exc
    if not isinstance(decoded, list):
        raise OutputContractError("publication payload must be a JSON list")
    normalized = validate_rows(decoded)
    replace_output_rows(normalized, config)
    return len(normalized)
