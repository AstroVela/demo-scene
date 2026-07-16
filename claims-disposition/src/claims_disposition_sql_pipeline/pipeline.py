"""Explicit orchestration for the standalone claims disposition SQL DAG."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import inspect
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from typing import Any, Mapping, Sequence

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import vane

from .config import DEFAULT_CONFIG_PATH, RuntimeConfig, load_runtime_config
from .minio_store import MinioStore
from .output_writer import replace_output_rows
from .pg import connect_postgres, probe_postgres, read_claim_rows
from .photo_ai import build_photo_ai_relation
from .vane_udfs import (
    CLAIM_MATERIAL_BATCH_SCHEMA,
    DAMAGE_VALIDATION_BATCH_SCHEMA,
    DocumentOcrActor,
    build_claim_material_batch_actor,
    build_damage_validation_batch,
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
_CREATE_RELATION_AS = re.compile(
    r"create\s+or\s+replace\s+(?:table|view)\s+"
    r"(?P<target>[A-Za-z_][A-Za-z0-9_]*)\s+as\s+(?P<query>.+)",
    re.IGNORECASE | re.DOTALL,
)


class RuntimeConnectionError(ConnectionError):
    """Raised when one or more required storage services are unavailable."""


class RunnerWorkspace:
    """Stage driver-local Arrow data as scans visible to every Vane Runner."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.counter = 0

    def stage_table(self, name: str, table: pa.Table) -> Path:
        """Persist Arrow input as a Parquet scan for either Runner backend."""

        self.counter += 1
        path = self.root / f"{self.counter:03d}-{_safe_identifier(name)}.parquet"
        pq.write_table(table, path)
        return path

    def relation_from_table(
        self,
        connection: Any,
        name: str,
        table: pa.Table,
    ) -> Any:
        """Expose a staged Arrow snapshot as a Vane relation."""

        path = self.stage_table(name, table)
        return connection.sql(f"select * from read_parquet({_sql_literal(path)})")


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


def _sql_literal(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


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


def _ensure_runner_write_compatibility() -> None:
    """Bridge the pinned wheel's LocalRunner/progress signature mismatch."""

    from duckdb.runners.progress import ProgressRenderer

    original_update = ProgressRenderer.update
    if "allow_terminal" in inspect.signature(original_update).parameters:
        return

    def compatible_update(
        self: Any,
        *,
        force: bool = False,
        allow_terminal: bool = False,
    ) -> None:
        del allow_terminal
        original_update(self, force=force)

    ProgressRenderer.update = compatible_update


def materialize_relation(relation: Any) -> pa.Table:
    """Materialize a Relation through the active Vane Runner write API."""

    _ensure_runner_write_compatibility()
    with TemporaryDirectory(prefix="claims-runner-result-") as root:
        path = Path(root) / "result.parquet"
        relation.write_parquet(str(path))
        return pq.read_table(path)


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


def _claim_material_aggregation_query(path: Path) -> str:
    statement = path.read_text(encoding="utf-8")
    match = _CREATE_RELATION_AS.fullmatch(
        statement.strip().removesuffix(";").strip()
    )
    if match is None:
        raise RuntimeError(f"invalid claim material SQL stage: {path}")
    query = match.group("query")
    marker = "aggregated as ("
    marker_index = query.find(marker)
    if marker_index < 0:
        raise RuntimeError("claim material SQL stage is missing aggregated CTE")
    return (
        "with claims as (select * from stg_claims), "
        "run_config as (select * from stg_run_config), "
        "row_facts as (select * from __runner_claim_material_row_facts), "
        + query[marker_index:]
    )


def _create_runner_claim_material_facts(
    connection: Any,
    config: RuntimeConfig,
    path: Path,
    *,
    workspace: RunnerWorkspace,
) -> None:
    """Run MinIO checks, photo quality, and document OCR through Vane."""

    source = workspace.relation_from_table(
        connection,
        "claim_material_input",
        connection.sql("select * from stg_claim_materials").to_arrow_table(),
    )
    relation = source.map_batches(
        build_claim_material_batch_actor(
            config.minio,
            required_fields=config.ocr.required_fields,
            minimum_text_confidence=config.ocr.minimum_text_confidence,
        ),
        schema=CLAIM_MATERIAL_BATCH_SCHEMA,
        actor_number=1,
        gpus=0,
    )
    register_or_replace_table(
        connection,
        "__runner_claim_material_row_facts",
        materialize_relation(relation),
    )
    connection.execute(
        "create or replace table int_claim_material_facts as "
        + _claim_material_aggregation_query(path)
    )


def _damage_aggregation_query(path: Path) -> str:
    statement = path.read_text(encoding="utf-8")
    match = _CREATE_RELATION_AS.fullmatch(
        statement.strip().removesuffix(";").strip()
    )
    if match is None:
        raise RuntimeError(f"invalid claim damage SQL stage: {path}")
    query = match.group("query")
    marker = "aggregated_damage_facts as ("
    marker_index = query.find(marker)
    if marker_index < 0:
        raise RuntimeError("claim damage SQL stage is missing aggregate CTE")
    return (
        "with material_facts as (select * from int_claim_material_facts), "
        "classified_photo_results as "
        "(select * from __runner_classified_photo_results), "
        + query[marker_index:]
    )


def _create_runner_claim_damage_facts(
    connection: Any,
    path: Path,
    *,
    workspace: RunnerWorkspace,
) -> None:
    """Validate AI damage responses through Vane before SQL aggregation."""

    model_responses = connection.sql(
        """
        with photo_values as (
          select
            material_facts.claim_id,
            unnest(json_extract(material_facts.usable_photo_inputs_json, '$[*]'))
              as photo_json
          from int_claim_material_facts as material_facts
          where material_facts.model_input_usable
        ),
        model_inputs as (
          select
            claim_id,
            try_cast(json_extract(photo_json, '$.file_order') as integer)
              as file_order,
            json_extract_string(photo_json, '$.file_id') as file_id,
            json_extract_string(photo_json, '$.sha256') as photo_sha256,
            cast(json_extract(photo_json, '$.photo_quality') as varchar)
              as photo_quality_json
          from photo_values
        )
        select
          model_inputs.*,
          ai.raw_damage_response
        from model_inputs
        left join int_claim_photo_ai as ai
          on model_inputs.claim_id = ai.claim_id
         and model_inputs.file_id = ai.file_id
         and model_inputs.photo_sha256 = ai.photo_sha256
        """
    ).to_arrow_table()
    source = workspace.relation_from_table(
        connection,
        "damage_validation_input",
        model_responses,
    )
    relation = source.map_batches(
        build_damage_validation_batch(),
        schema=DAMAGE_VALIDATION_BATCH_SCHEMA,
        gpus=0,
    )
    register_or_replace_table(
        connection,
        "__runner_classified_photo_results",
        materialize_relation(relation),
    )
    connection.execute(
        "create or replace table int_claim_damage_facts as "
        + _damage_aggregation_query(path)
    )


def _execute_sql_file(
    connection: Any,
    path: Path,
) -> None:
    try:
        statement = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read SQL stage {path}: {exc}") from exc
    connection.execute(statement)


def _relation_rows(
    connection: Any,
    relation_name: str,
) -> list[dict[str, Any]]:
    relation = connection.sql(f"select * from {_safe_identifier(relation_name)}")
    columns = list(relation.columns)
    return [dict(zip(columns, row)) for row in relation.fetchall()]


def _create_photo_ai_table(
    connection: Any,
    config: RuntimeConfig,
    *,
    workspace: RunnerWorkspace,
) -> None:
    """Run multimodal inference and register its typed response relation."""

    material_rows = _relation_rows(connection, "int_claim_material_facts")
    table = build_photo_ai_relation(
        material_rows,
        connection,
        config,
        request_relation_factory=lambda value: workspace.relation_from_table(
            connection,
            "photo_ai_request",
            value,
        ),
        response_materializer=materialize_relation,
        result_factory=lambda value: value,
    )
    register_or_replace_table(
        connection,
        "int_claim_photo_ai",
        table,
    )


def run_pipeline(
    config: RuntimeConfig,
) -> int:
    """Execute the complete DAG and atomically publish its validated mart."""

    # The configured backend changes execution only; the relation code is shared.
    vane.configure(runner=config.runner)
    # Fail before computation if either PostgreSQL or MinIO is unavailable.
    probe_runtime(config)
    run_started_at = datetime.now(timezone.utc)
    claim_rows = read_claim_rows_with_json(config)

    with TemporaryDirectory(
        prefix=f"claims-disposition-{config.runner}-"
    ) as workspace_root:
        workspace = RunnerWorkspace(Path(workspace_root))
        connection = duckdb.connect()
        try:
            # Register the PostgreSQL snapshot and secret-free run settings.
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
            # Stage claims and enrich each material through the active Runner.
            for sql_path in SQL_STAGES:
                if sql_path.stem == "int_claim_material_facts":
                    _create_runner_claim_material_facts(
                        connection,
                        config,
                        sql_path,
                        workspace=workspace,
                    )
                    continue
                _execute_sql_file(connection, sql_path)
            # Bind verified MinIO photos to one multimodal request per image.
            _create_photo_ai_table(
                connection,
                config,
                workspace=workspace,
            )
            # Validate AI facts, apply deterministic rules, and build the mart.
            for sql_path in SQL_FINAL_STAGES:
                if sql_path.stem == "int_claim_damage_facts":
                    _create_runner_claim_damage_facts(
                        connection,
                        sql_path,
                        workspace=workspace,
                    )
                    continue
                _execute_sql_file(connection, sql_path)
            rows = connection.sql(
                "select * from claim_disposition order by claim_id"
            ).to_arrow_table().to_pylist()
        finally:
            connection.close()

    # Validate the output contract before replacing the PostgreSQL result table.
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
