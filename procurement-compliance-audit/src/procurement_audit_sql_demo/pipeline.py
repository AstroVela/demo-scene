"""Explicit orchestration for the eight-node SQL DAG."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import vane

from .ai import build_evidence_ai_relation, configure_provider_credentials
from .config import RuntimeConfig
from .minio_store import MinioStore
from .output_writer import PublishedOutputs, write_outputs
from .pg import connect_postgres, probe_postgres, read_source_rows
from .source_data import SourceBundle, source_bundle_from_rows
from .vane_functions import (
    EvidenceOcrActor,
    validate_audit_fact_json_udf,
)
from .verify_outputs import verify_fixture_outputs


SQL_ROOT = Path(__file__).resolve().parent / "sql"
PRE_AI_STAGES = (
    ("stg_scores", SQL_ROOT / "staging/stg_scores.sql"),
    ("stg_evidence_images", SQL_ROOT / "staging/stg_evidence_images.sql"),
)
EVIDENCE_OCR_UDF_STAGE = SQL_ROOT / "intermediate/int_evidence_ocr_udf.sql"
EVIDENCE_OCR_STAGE = SQL_ROOT / "intermediate/int_evidence_ocr.sql"
CONFLICT_VALIDATION_INPUT_STAGE = SQL_ROOT / "intermediate/int_conflict_validation_inputs.sql"
CONFLICT_VALIDATION_UDF_STAGE = SQL_ROOT / "intermediate/int_conflict_validation_udf.sql"
CONFLICT_FACT_STAGE = SQL_ROOT / "intermediate/int_conflict_facts.sql"
POST_AI_STAGES = (
    ("int_score_metrics", SQL_ROOT / "intermediate/int_score_metrics.sql"),
    ("audit_findings", SQL_ROOT / "marts/audit_findings.sql"),
    ("audit_summary", SQL_ROOT / "marts/audit_summary.sql"),
)
CORE_RELATIONS = (
    "stg_scores",
    "stg_evidence_images",
    "int_evidence_ocr",
    "int_evidence_ai",
    "int_conflict_facts",
    "int_score_metrics",
    "audit_findings",
    "audit_summary",
)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CREATE_RELATION_AS = re.compile(
    r"create\s+or\s+replace\s+(?:table|view)\s+"
    r"(?P<target>[A-Za-z_][A-Za-z0-9_]*)\s+as\s+(?P<query>.+)",
    re.IGNORECASE | re.DOTALL,
)


class RuntimeConnectionError(ConnectionError):
    """Raised when PostgreSQL or MinIO cannot be reached."""


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


@dataclass(frozen=True)
class PipelineResult:
    executed_relations: tuple[str, ...]
    findings: tuple[dict[str, Any], ...]
    summary: dict[str, Any]
    finding_count: int
    summary_count: int
    findings_path: Path
    summary_path: Path


def _safe_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid DuckDB identifier: {value!r}")
    return value


def _sql_literal(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def register_or_replace_table(
    connection: Any,
    name: str,
    table: pa.Table,
) -> None:
    """Materialize an Arrow snapshot without interpolating unsafe identifiers."""

    target = _safe_identifier(name)
    temporary = f"__{target}_arrow_input"
    try:
        connection.unregister(temporary)
    except Exception:
        pass
    connection.register(temporary, table)
    try:
        connection.execute(
            f"create or replace table {target} as select * from {temporary}"
        )
    finally:
        connection.unregister(temporary)


def materialize_relation(relation: Any) -> pa.Table:
    """Materialize a Relation through the active Vane Runner write API."""

    with TemporaryDirectory(prefix="procurement-runner-result-") as root:
        path = Path(root) / "result.parquet"
        relation.write_parquet(str(path))
        return pq.read_table(path)


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
    *,
    order_by: str | None = None,
) -> list[dict[str, Any]]:
    name = _safe_identifier(relation_name)
    order = f" order by {_safe_identifier(order_by)}" if order_by else ""
    table = connection.execute(f"select * from {name}{order}").to_arrow_table()
    return table.to_pylist()


def read_source_bundle(config: RuntimeConfig) -> SourceBundle:
    """Read and validate the complete PostgreSQL source snapshot."""

    with connect_postgres(config.postgres) as postgres:
        rows = read_source_rows(postgres, config.postgres)
    return source_bundle_from_rows(*rows, expected_bucket=config.minio.bucket)


def probe_runtime(config: RuntimeConfig) -> None:
    """Report every unavailable source service without printing credentials."""

    failures: list[str] = []
    try:
        probe_postgres(config.postgres)
    except Exception as exc:
        failures.append(f"PostgreSQL ({config.postgres.raw_schema}): {exc}")
    try:
        MinioStore(config.minio).probe()
    except Exception as exc:
        failures.append(f"MinIO ({config.minio.endpoint}): {exc}")
    if failures:
        raise RuntimeConnectionError("; ".join(failures))


def attach_runtime_functions(
    connection: Any,
    config: RuntimeConfig,
    local_ocr_results: Mapping[tuple[str, str], str] | None = None,
) -> None:
    """Attach the release-facing stateless and stateful SQL functions."""

    vane.attach_function(
        validate_audit_fact_json_udf,
        connection=connection,
        alias="validate_audit_fact_json",
        parameters=["VARCHAR"],
        replace=True,
    )
    if config.runner == "ray":
        ocr_function = EvidenceOcrActor(config.minio)
        return_dtype = None
    else:
        if local_ocr_results is None:
            raise ValueError("local OCR results are required for the local Runner")
        results = dict(local_ocr_results)

        def ocr_function(bucket: str, object_key: str) -> str:
            return results[(bucket, object_key)]

        return_dtype = "VARCHAR"
    vane.attach_function(
        ocr_function,
        connection=connection,
        alias="evidence_ocr_json",
        parameters=["VARCHAR", "VARCHAR"],
        return_dtype=return_dtype,
        replace=True,
    )


def build_local_evidence_ocr_results(
    source: SourceBundle,
    config: RuntimeConfig,
) -> dict[tuple[str, str], str]:
    """Run native OCR once on the driver before LocalRunner fragment execution."""

    locators = sorted(
        {
            (row["bucket"], row["object_key"])
            for row in source.evidence.to_pylist()
            if row["media_type"] == "image/png"
        }
    )
    ocr = EvidenceOcrActor(config.minio)
    return {
        (bucket, object_key): ocr(bucket, object_key)
        for bucket, object_key in locators
    }


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
    source_relations: tuple[str, ...],
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


def _register_inputs(connection: Any, source: SourceBundle) -> None:
    """Register the validated PostgreSQL snapshot as SQL source tables."""

    register_or_replace_table(connection, "input_project", source.project)
    register_or_replace_table(connection, "input_suppliers", source.suppliers)
    register_or_replace_table(connection, "input_scores", source.scores)
    register_or_replace_table(connection, "input_evidence", source.evidence)


def run_pipeline(
    config: RuntimeConfig,
    *,
    configure_runner: Callable[..., Any] = vane.configure,
    initialize_runner: Callable[[], Any] = vane.get_or_create_runner,
    runtime_probe: Callable[[RuntimeConfig], None] = probe_runtime,
    runtime_function_attacher: Callable[..., None] = attach_runtime_functions,
    ai_relation_builder: Callable[..., Any] = build_evidence_ai_relation,
    connection_factory: Callable[[], Any] = vane.connect,
    source_loader: Callable[[RuntimeConfig], SourceBundle] = read_source_bundle,
    local_ocr_result_builder: Callable[
        [SourceBundle, RuntimeConfig], Mapping[tuple[str, str], str]
    ] = build_local_evidence_ocr_results,
    output_publisher: Callable[..., PublishedOutputs] = write_outputs,
    relation_materializer: Callable[[Any], pa.Table] = materialize_relation,
) -> PipelineResult:
    """Execute all eight core relations, verify the fixture, and publish JSONL."""

    configure_provider_credentials(config.ai)
    # The configured backend changes execution only; the relation code is shared.
    configure_runner(runner=config.runner)
    # Fail before computation if either PostgreSQL or MinIO is unavailable.
    runtime_probe(config)
    # Start Ray before driver connections and attached UDFs enlarge the process.
    # Provider credentials must already be present so local workers inherit them.
    initialize_runner()
    source = source_loader(config)
    local_ocr_results = (
        local_ocr_result_builder(source, config)
        if config.runner == "local"
        else None
    )
    executed: list[str] = []
    with TemporaryDirectory(
        prefix=f"procurement-audit-{config.runner}-"
    ) as workspace_root:
        workspace = RunnerWorkspace(Path(workspace_root))
        connection = connection_factory()
        runner_connection = connection_factory()
        try:
            # Register the validated PostgreSQL snapshot on the driver.
            _register_inputs(connection, source)
            runtime_function_attacher(runner_connection, config, local_ocr_results)
            # Normalize the PostgreSQL inputs on the driver.
            for relation_name, sql_path in PRE_AI_STAGES:
                _execute_sql_file(connection, sql_path)
                executed.append(relation_name)
            # Keep OCR as a direct Runner SQL projection and parse its JSON
            # contract in the following pure SQL relation.
            _execute_runner_sql_file(
                connection,
                runner_connection,
                EVIDENCE_OCR_UDF_STAGE,
                workspace=workspace,
                source_relations=("stg_evidence_images",),
                materializer=relation_materializer,
            )
            _execute_sql_file(connection, EVIDENCE_OCR_STAGE)
            executed.append("int_evidence_ocr")

            # Bind each qualified OCR record to one multimodal evidence request.
            ocr_rows = _relation_rows(
                connection,
                "int_evidence_ocr",
                order_by="file_id",
            )
            ai_table = ai_relation_builder(
                ocr_rows,
                connection,
                source,
                config,
                request_relation_factory=lambda table: workspace.relation_from_table(
                    connection,
                    "evidence_ai_request",
                    table,
                ),
                response_materializer=relation_materializer,
                result_factory=lambda table: table,
            )
            register_or_replace_table(
                connection,
                "int_evidence_ai",
                ai_table,
            )
            executed.append("int_evidence_ai")

            # Bind model output to trusted metadata before remote validation.
            _execute_sql_file(connection, CONFLICT_VALIDATION_INPUT_STAGE)
            _execute_runner_sql_file(
                connection,
                runner_connection,
                CONFLICT_VALIDATION_UDF_STAGE,
                workspace=workspace,
                source_relations=("int_conflict_validation_inputs",),
                materializer=relation_materializer,
            )
            # Parse validated facts and apply the role contract in pure SQL.
            _execute_sql_file(connection, CONFLICT_FACT_STAGE)
            executed.append("int_conflict_facts")

            # Compute score impact and build both marts.
            for relation_name, sql_path in POST_AI_STAGES:
                _execute_sql_file(connection, sql_path)
                executed.append(relation_name)
            findings = _relation_rows(
                connection,
                "audit_findings",
                order_by="rule_id",
            )
            summaries = _relation_rows(
                connection,
                "audit_summary",
                order_by="project_id",
            )
        finally:
            runner_connection.close()
            connection.close()

    # Assert the fixture story before atomically replacing the JSONL outputs.
    verify_fixture_outputs(findings, summaries)
    evidence_ids = frozenset(row["file_id"] for row in source.evidence.to_pylist())
    published = output_publisher(
        findings,
        summaries,
        config.output_dir,
        evidence_ids,
    )
    return PipelineResult(
        executed_relations=tuple(executed),
        findings=tuple(findings),
        summary=dict(summaries[0]),
        finding_count=published.finding_count,
        summary_count=published.summary_count,
        findings_path=published.findings_path,
        summary_path=published.summary_path,
    )
