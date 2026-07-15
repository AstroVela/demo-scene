"""Explicit orchestration for the standalone claims disposition SQL DAG."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

import duckdb
import pyarrow as pa
import vane

from .config import DEFAULT_CONFIG_PATH, RuntimeConfig, load_runtime_config
from .minio_store import MinioStore
from .output_writer import replace_output_rows
from .pg import connect_postgres, probe_postgres, read_claim_rows
from .photo_ai import build_photo_ai_relation
from .vane_udfs import (
    DocumentOcrActor,
    build_minio_udfs,
    stable_json,
    stateless_udf_specs,
)


SQL_ROOT = Path(__file__).resolve().parent / "sql"
SQL_STAGES = (
    SQL_ROOT / "staging/stg_claims.sql",
    SQL_ROOT / "staging/stg_claim_materials.sql",
    SQL_ROOT / "staging/stg_run_config.sql",
    SQL_ROOT / "intermediate/int_claim_material_facts.sql",
)
SQL_FINAL_STAGES = (
    SQL_ROOT / "intermediate/int_claim_damage_facts.sql",
    SQL_ROOT / "intermediate/int_claim_decision_facts.sql",
    SQL_ROOT / "marts/claim_disposition.sql",
)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class RuntimeConnectionError(ConnectionError):
    """Raised when one or more required storage services are unavailable."""


def build_run_config_row(
    config: RuntimeConfig,
    run_started_at: datetime,
) -> dict[str, Any]:
    """Build the single secret-free row consumed by SQL models."""

    if run_started_at.tzinfo is None or run_started_at.utcoffset() is None:
        raise ValueError("run_started_at must be timezone-aware")
    return {
        "runtime_config_version": config.version,
        "run_started_at": run_started_at.isoformat(),
        "ocr_engine": config.ocr.engine,
        "ocr_device": config.ocr.device,
        "required_fields_json": stable_json(list(config.ocr.required_fields)),
        "minimum_text_confidence": config.ocr.minimum_text_confidence,
        "ai_provider": config.ai.provider,
        "ai_model": config.ai.model,
        "minio_bucket": config.minio.bucket,
    }


def _arrow_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return stable_json(value)
    return value


def rows_to_arrow(rows: list[Mapping[str, Any]]) -> pa.Table:
    """Normalize JSON values and convert runtime rows to Arrow."""

    normalized = [
        {key: _arrow_value(value) for key, value in row.items()}
        for row in rows
    ]
    return pa.Table.from_pylist(normalized)


def read_claim_rows_with_json(config: RuntimeConfig) -> list[dict[str, Any]]:
    """Read the ordered PostgreSQL snapshot with stable JSON strings."""

    with connect_postgres(config.postgres) as connection:
        rows = read_claim_rows(connection, config.postgres)
    return [
        {key: _arrow_value(value) for key, value in dict(row).items()}
        for row in rows
    ]


def _safe_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid DuckDB identifier: {value!r}")
    return value


def register_or_replace_table(
    connection: Any,
    target: str,
    table: pa.Table,
) -> None:
    """Replace a DuckDB table from an Arrow snapshot without name interpolation."""

    name = _safe_identifier(target)
    temporary = f"__{name}_arrow_input"
    try:
        connection.unregister(temporary)
    except Exception:
        pass
    connection.register(temporary, table)
    try:
        connection.execute(
            f"create or replace table {name} as select * from {temporary}"
        )
    finally:
        connection.unregister(temporary)


def probe_runtime(config: RuntimeConfig) -> None:
    """Report every unavailable storage service in one credential-free error."""

    failures: list[str] = []
    try:
        probe_postgres(config.postgres)
    except Exception as exc:
        failures.append(f"PostgreSQL ({config.postgres.raw_relation}): {exc}")
    try:
        MinioStore(config.minio).probe()
    except Exception as exc:
        failures.append(f"MinIO ({config.minio.endpoint}): {exc}")
    if failures:
        raise RuntimeConnectionError("; ".join(failures))


def attach_runtime_functions(connection: Any, config: RuntimeConfig) -> None:
    """Attach all SQL-callable Vane functions to the active connection."""

    for spec in stateless_udf_specs(build_minio_udfs(config.minio)):
        vane.attach_function(
            spec.function,
            connection=connection,
            alias=spec.alias,
            parameters=list(spec.parameters),
            replace=True,
        )
    vane.attach_function(
        DocumentOcrActor(config.minio),
        connection=connection,
        alias="document_ocr_json",
        parameters=["VARCHAR", "VARCHAR"],
        replace=True,
    )


def _execute_sql_file(connection: Any, path: Path) -> None:
    try:
        statement = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read SQL stage {path}: {exc}") from exc
    connection.execute(statement)


def _relation_rows(connection: Any, relation_name: str) -> list[dict[str, Any]]:
    relation = connection.sql(f"select * from {_safe_identifier(relation_name)}")
    columns = list(relation.columns)
    return [dict(zip(columns, row)) for row in relation.fetchall()]


def _create_photo_ai_table(
    connection: Any,
    config: RuntimeConfig,
) -> None:
    material_rows = _relation_rows(connection, "int_claim_material_facts")
    result = build_photo_ai_relation(material_rows, connection, config)
    register_or_replace_table(
        connection,
        "int_claim_photo_ai",
        result.to_arrow_table(),
    )


def run_pipeline(config: RuntimeConfig) -> int:
    """Execute the complete DAG and atomically publish its validated mart."""

    vane.configure(runner="local")
    probe_runtime(config)
    run_started_at = datetime.now(timezone.utc)
    claim_rows = read_claim_rows_with_json(config)

    connection = duckdb.connect()
    try:
        register_or_replace_table(
            connection,
            "claims_runtime_claims",
            rows_to_arrow(claim_rows),
        )
        register_or_replace_table(
            connection,
            "claims_runtime_run_config",
            rows_to_arrow([build_run_config_row(config, run_started_at)]),
        )
        attach_runtime_functions(connection, config)
        for sql_path in SQL_STAGES:
            _execute_sql_file(connection, sql_path)
        _create_photo_ai_table(connection, config)
        for sql_path in SQL_FINAL_STAGES:
            _execute_sql_file(connection, sql_path)
        rows = connection.sql(
            "select * from claim_disposition order by claim_id"
        ).to_arrow_table().to_pylist()
    finally:
        connection.close()

    return replace_output_rows(rows, config)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the standalone claims disposition SQL pipeline."
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
        row_count = run_pipeline(config)
    except Exception as exc:
        print(f"pipeline failed: {exc}", file=sys.stderr)
        return 1
    print(f"published {row_count} claim dispositions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
