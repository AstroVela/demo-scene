"""Explicit orchestration for the standalone customer service audit SQL DAG."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping, Sequence

import pyarrow as pa
import pyarrow.parquet as pq
import vane

from .call_ai import build_call_ai_relation, configure_provider_credentials
from .config import DEFAULT_CONFIG_PATH, RuntimeConfig, load_runtime_config
from .minio_store import MinioStore
from .output_writer import publish_analysis_json
from .vane_udfs import (
    AsrTranscribeActor,
    build_minio_udfs,
    stable_json,
    stateless_udf_specs,
    transcript_quality_json,
    validate_call_analysis_json,
)


SQL_ROOT = Path(__file__).resolve().parent / "sql"
SQL_STAGES = (
    SQL_ROOT / "staging/stg_calls.sql",
    SQL_ROOT / "staging/stg_run_config.sql",
)
CALL_INPUT_STAGE = SQL_ROOT / "intermediate/int_call_inputs.sql"
CALL_PROBE_UDF_STAGE = SQL_ROOT / "intermediate/int_call_probe_udf.sql"
CALL_FACT_STAGE = SQL_ROOT / "intermediate/int_call_facts.sql"
CALL_TRANSCRIPT_UDF_STAGE = SQL_ROOT / "intermediate/int_call_transcript_udf.sql"
TRANSCRIPT_QUALITY_UDF_STAGE = (
    SQL_ROOT / "intermediate/int_transcript_quality_udf.sql"
)
TRANSCRIPT_FACT_STAGE = SQL_ROOT / "intermediate/int_transcript_facts.sql"
ANALYSIS_VALIDATION_INPUT_STAGE = (
    SQL_ROOT / "intermediate/int_analysis_validation_inputs.sql"
)
ANALYSIS_VALIDATION_UDF_STAGE = (
    SQL_ROOT / "intermediate/int_analysis_validation_udf.sql"
)
SQL_FINAL_STAGES = (
    SQL_ROOT / "intermediate/int_analysis_facts.sql",
    SQL_ROOT / "marts/call_audit_report.sql",
)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CREATE_RELATION_AS = re.compile(
    r"(?:--[^\r\n]*(?:\r?\n|$))*\s*"
    r"create\s+or\s+replace\s+(?:table|view)\s+"
    r"(?P<target>[A-Za-z_][A-Za-z0-9_]*)\s+as\s+(?P<query>.+)",
    re.IGNORECASE | re.DOTALL,
)
_WAV_KEY = re.compile(r"^(?P<call_id>[^/]+)\.wav$", re.IGNORECASE)
_CALL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class RuntimeConnectionError(ConnectionError):
    """Raised when one or more required storage services are unavailable."""


class RunnerWorkspace:
    """Stage driver-local Arrow data as Parquet scans for the active Runner."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.counter = 0

    def stage_table(self, name: str, table: pa.Table) -> Path:
        """Persist Arrow input as a Parquet scan for the configured Runner."""

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
        "asr_engine": config.asr.engine,
        "asr_model": config.asr.model,
        "asr_language": config.asr.language,
        "min_text_chars": config.asr.min_text_chars,
        "ai_provider": config.ai.provider,
        "ai_model": config.ai.model,
        "minio_bucket": config.minio.bucket,
    }


def list_call_rows(config: RuntimeConfig) -> list[dict[str, Any]]:
    """List the ordered MinIO recording snapshot as call manifest rows."""

    store = MinioStore(config.minio)
    keys = store.list_object_keys(
        config.minio.bucket,
        config.minio.recordings_prefix,
    )
    rows: list[dict[str, Any]] = []
    for object_key in keys:
        relative = object_key[len(config.minio.recordings_prefix):]
        match = _WAV_KEY.fullmatch(relative)
        if match is None:
            continue
        call_id = match.group("call_id")
        if not _CALL_ID.fullmatch(call_id):
            continue
        rows.append(
            {
                "call_id": call_id,
                "bucket": config.minio.bucket,
                "object_key": object_key,
            }
        )
    return rows


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


def materialize_relation(relation: Any) -> pa.Table:
    """Materialize a Relation through the active Vane Runner write API."""

    with TemporaryDirectory(prefix="audit-runner-result-") as root:
        path = Path(root) / "result.parquet"
        relation.write_parquet(str(path))
        return pq.read_table(path)


def probe_runtime(config: RuntimeConfig) -> None:
    """Fail before computation if MinIO is unavailable."""

    try:
        MinioStore(config.minio).probe()
    except Exception as exc:
        raise RuntimeConnectionError(f"MinIO ({config.minio.endpoint}): {exc}")


def attach_runtime_functions(connection: Any, config: RuntimeConfig) -> None:
    """Attach functions used by Runner-executed SQL to its connection."""

    for spec in stateless_udf_specs(build_minio_udfs(config.minio)):
        vane.attach_function(
            spec.function,
            connection=connection,
            alias=spec.alias,
            parameters=list(spec.parameters),
            replace=True,
        )

    def transcript_quality_for_run(transcript_json: str) -> str:
        return transcript_quality_json(
            transcript_json,
            config.asr.min_text_chars,
        )

    vane.attach_function(
        transcript_quality_for_run,
        connection=connection,
        alias="transcript_quality_json",
        parameters=["VARCHAR"],
        return_dtype="VARCHAR",
        replace=True,
    )
    vane.attach_function(
        validate_call_analysis_json,
        connection=connection,
        alias="validate_call_analysis_json",
        parameters=["VARCHAR", "VARCHAR", "VARCHAR"],
        replace=True,
    )
    if config.runner == "ray":
        vane.attach_function(
            AsrTranscribeActor(config.minio, config.asr),
            connection=connection,
            alias="asr_transcribe_json",
            parameters=["VARCHAR", "VARCHAR"],
            replace=True,
        )


def attach_local_asr_lookup(
    runner_connection: Any,
    driver_connection: Any,
    config: RuntimeConfig,
) -> None:
    """Run native ASR on the driver and expose immutable results as a task UDF."""

    locators = driver_connection.execute(
        "select cast(bucket as varchar), cast(object_key as varchar) "
        "from int_call_facts "
        "where audio_usable "
        "order by 1, 2"
    ).fetchall()
    actor = AsrTranscribeActor(config.minio, config.asr)
    results = {
        (bucket, object_key): actor(bucket, object_key)
        for bucket, object_key in locators
    }

    def asr_transcribe_json(bucket: str, object_key: str) -> str:
        return results[(bucket, object_key)]

    vane.attach_function(
        asr_transcribe_json,
        connection=runner_connection,
        alias="asr_transcribe_json",
        parameters=["VARCHAR", "VARCHAR"],
        return_dtype="VARCHAR",
        replace=True,
    )


def _sql_stage_parts(path: Path) -> tuple[str, str]:
    statement = path.read_text(encoding="utf-8")
    match = _CREATE_RELATION_AS.fullmatch(
        statement.strip().removesuffix(";").strip()
    )
    if match is None:
        raise RuntimeError(f"invalid SQL stage: {path}")
    return match.group("target"), match.group("query")


def _execute_runner_sql_file(
    connection: Any,
    runner_connection: Any,
    path: Path,
    *,
    workspace: RunnerWorkspace,
    source_relations: Sequence[str],
    materializer: Callable[[Any], pa.Table],
) -> None:
    """Run one SQL model through the active Vane Runner and register its result."""

    for source_name in source_relations:
        name = _safe_identifier(source_name)
        table = connection.execute(f"select * from {name}").to_arrow_table()
        workspace.relation_from_table(
            runner_connection,
            name,
            table,
        ).create_view(name, replace=True)
    target, query = _sql_stage_parts(path)
    register_or_replace_table(
        connection,
        target,
        materializer(runner_connection.sql(query)),
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
    table = connection.execute(
        f"select * from {_safe_identifier(relation_name)}"
    ).to_arrow_table()
    return table.to_pylist()


def _create_call_ai_table(
    connection: Any,
    config: RuntimeConfig,
    *,
    workspace: RunnerWorkspace,
) -> None:
    """Run transcript analysis and register its typed response relation."""

    transcript_rows = _relation_rows(connection, "int_transcript_facts")
    table = build_call_ai_relation(
        transcript_rows,
        connection,
        config,
        request_relation_factory=lambda value: workspace.relation_from_table(
            connection,
            "call_ai_request",
            value,
        ),
        response_materializer=materialize_relation,
        result_factory=lambda value: value,
    )
    register_or_replace_table(
        connection,
        "int_call_analysis_ai",
        table,
    )


def run_pipeline(
    config: RuntimeConfig,
) -> int:
    """Execute the complete DAG and publish per-call analysis JSON to MinIO."""

    configure_provider_credentials(config.ai)
    # The configured backend changes execution only; the relation code is shared.
    vane.configure(runner=config.runner)
    # Fail before computation if MinIO is unavailable.
    probe_runtime(config)
    # Start Ray before driver connections and attached UDFs enlarge the process.
    # Provider credentials must already be present so local workers inherit them.
    vane.get_or_create_runner()
    run_started_at = datetime.now(timezone.utc)
    call_rows = list_call_rows(config)

    with TemporaryDirectory(
        prefix=f"customer-service-audit-{config.runner}-"
    ) as workspace_root:
        workspace = RunnerWorkspace(Path(workspace_root))
        connection = vane.connect()
        runner_connection = vane.connect()
        try:
            # Register the MinIO recording snapshot and secret-free run settings.
            register_or_replace_table(
                connection,
                "audit_runtime_calls",
                rows_to_arrow(call_rows),
            )
            register_or_replace_table(
                connection,
                "audit_runtime_run_config",
                rows_to_arrow([build_run_config_row(config, run_started_at)]),
            )
            attach_runtime_functions(runner_connection, config)
            # Build the driver-local staging relations first.
            for sql_path in SQL_STAGES:
                _execute_sql_file(connection, sql_path)
            # Probe every discovered recording through direct Runner SQL.
            _execute_sql_file(connection, CALL_INPUT_STAGE)
            _execute_runner_sql_file(
                connection,
                runner_connection,
                CALL_PROBE_UDF_STAGE,
                workspace=workspace,
                source_relations=("int_call_inputs",),
                materializer=materialize_relation,
            )
            _execute_sql_file(connection, CALL_FACT_STAGE)
            # Transcribe each usable recording through the ASR boundary.
            if config.runner == "local":
                attach_local_asr_lookup(
                    runner_connection,
                    connection,
                    config,
                )
            _execute_runner_sql_file(
                connection,
                runner_connection,
                CALL_TRANSCRIPT_UDF_STAGE,
                workspace=workspace,
                source_relations=("int_call_facts",),
                materializer=materialize_relation,
            )
            _execute_runner_sql_file(
                connection,
                runner_connection,
                TRANSCRIPT_QUALITY_UDF_STAGE,
                workspace=workspace,
                source_relations=("int_call_transcript_udf",),
                materializer=materialize_relation,
            )
            _execute_sql_file(connection, TRANSCRIPT_FACT_STAGE)
            # Analyze every usable transcript through the real AI boundary.
            _create_call_ai_table(
                connection,
                config,
                workspace=workspace,
            )
            # Validate untrusted AI JSON through one direct Runner projection.
            _execute_sql_file(connection, ANALYSIS_VALIDATION_INPUT_STAGE)
            _execute_runner_sql_file(
                connection,
                runner_connection,
                ANALYSIS_VALIDATION_UDF_STAGE,
                workspace=workspace,
                source_relations=("int_analysis_validation_inputs",),
                materializer=materialize_relation,
            )
            for sql_path in SQL_FINAL_STAGES:
                _execute_sql_file(connection, sql_path)
            rows = connection.execute(
                "select * from call_audit_report order by call_id"
            ).to_arrow_table().to_pylist()
        finally:
            runner_connection.close()
            connection.close()

    # Validate the output contract before publishing analysis JSON to MinIO.
    return publish_analysis_json(rows, config, run_started_at)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the standalone customer service audit SQL pipeline."
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
        file_count = run_pipeline(config)
    except Exception as exc:
        print(f"pipeline failed: {exc}", file=sys.stderr)
        return 1
    print(f"published {file_count} call analysis files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
