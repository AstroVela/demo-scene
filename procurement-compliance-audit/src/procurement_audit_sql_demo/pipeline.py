"""Explicit single-connection orchestration for the eight-node SQL DAG."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import inspect
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import vane

from .ai import build_evidence_ai_relation
from .config import RuntimeConfig
from .minio_store import MinioStore
from .output_writer import PublishedOutputs, write_outputs
from .pg import connect_postgres, probe_postgres, read_source_rows
from .source_data import SourceBundle, source_bundle_from_rows
from .vane_functions import (
    EVIDENCE_OCR_BATCH_SCHEMA,
    EvidenceOcrActor,
    build_evidence_ocr_batch_actor,
    validate_audit_fact_json_udf,
)
from .verify_outputs import verify_fixture_outputs


SQL_ROOT = Path(__file__).resolve().parent / "sql"
PRE_AI_STAGES = (
    ("stg_scores", SQL_ROOT / "staging/stg_scores.sql"),
    ("stg_evidence_images", SQL_ROOT / "staging/stg_evidence_images.sql"),
    ("int_evidence_ocr", SQL_ROOT / "intermediate/int_evidence_ocr.sql"),
)
POST_AI_STAGES = (
    ("int_conflict_facts", SQL_ROOT / "intermediate/int_conflict_facts.sql"),
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
    relation = connection.sql(f"select * from {name}{order}")
    columns = list(relation.columns)
    return [dict(zip(columns, row)) for row in relation.fetchall()]


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


def attach_runtime_functions(connection: Any, config: RuntimeConfig) -> None:
    """Attach the release-facing stateless and stateful SQL functions."""

    vane.attach_function(
        validate_audit_fact_json_udf,
        connection=connection,
        alias="validate_audit_fact_json",
        parameters=["VARCHAR"],
        replace=True,
    )
    vane.attach_function(
        EvidenceOcrActor(config.minio),
        connection=connection,
        alias="evidence_ocr_json",
        parameters=["VARCHAR", "VARCHAR"],
        replace=True,
    )


def _create_evidence_ocr_table(
    connection: Any,
    config: RuntimeConfig,
    *,
    workspace: RunnerWorkspace,
) -> None:
    """Read MinIO evidence and run OCR through the active Vane Runner."""

    source = workspace.relation_from_table(
        connection,
        "evidence_ocr_input",
        connection.sql("select * from stg_evidence_images").to_arrow_table(),
    )
    relation = source.map_batches(
        build_evidence_ocr_batch_actor(config.minio),
        schema=EVIDENCE_OCR_BATCH_SCHEMA,
        actor_number=1,
        gpus=0,
    )
    register_or_replace_table(
        connection,
        "int_evidence_ocr",
        materialize_relation(relation),
    )


def _rewrite_conflict_validation_cte(query: str) -> str:
    result, count = re.subn(
        r"with\s+validated\s+as\s+materialized\s*\(.*?\)\s*select",
        "with validated as (select * from __runner_validated_audit_facts)\nselect",
        query,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if count != 1:
        raise RuntimeError("cannot isolate int_conflict_facts validation CTE")
    return result


def _create_runner_conflict_facts(
    connection: Any,
    path: Path,
    *,
    workspace: RunnerWorkspace,
) -> None:
    """Validate each AI fact through Vane before trusted-role filtering."""

    inputs = _relation_rows(connection, "int_evidence_ai", order_by="file_id")
    roles = {
        row["file_id"]: row
        for row in _relation_rows(
            connection,
            "stg_evidence_images",
            order_by="file_id",
        )
    }
    validated_rows = []
    for row in inputs:
        source = workspace.relation_from_table(
            connection,
            "audit_fact_validation_input",
            pa.table({"raw_response": [row["raw_response"]]}),
        )
        relation = source.project(
            "validate_audit_fact_json(raw_response) as fact_json"
        )
        result = materialize_relation(relation)
        if result.num_rows != 1:
            raise RuntimeError("audit fact validation must return exactly one row")
        evidence = roles[row["file_id"]]
        validated_rows.append(
            {
                "project_id": evidence["project_id"],
                "file_id": row["file_id"],
                "role": evidence["role"],
                "fact_json": result.column("fact_json")[0].as_py(),
            }
        )
    register_or_replace_table(
        connection,
        "__runner_validated_audit_facts",
        pa.Table.from_pylist(
            validated_rows,
            schema=pa.schema(
                [
                    ("project_id", pa.string()),
                    ("file_id", pa.string()),
                    ("role", pa.string()),
                    ("fact_json", pa.string()),
                ]
            ),
        ),
    )
    statement = path.read_text(encoding="utf-8")
    match = _CREATE_RELATION_AS.fullmatch(
        statement.strip().removesuffix(";").strip()
    )
    if match is None:
        raise RuntimeError(f"invalid int_conflict_facts SQL stage: {path}")
    connection.execute(
        "create or replace view int_conflict_facts as "
        + _rewrite_conflict_validation_cte(match.group("query"))
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
    runtime_probe: Callable[[RuntimeConfig], None] = probe_runtime,
    runtime_function_attacher: Callable[[Any, RuntimeConfig], None] = attach_runtime_functions,
    ai_relation_builder: Callable[..., Any] = build_evidence_ai_relation,
    connection_factory: Callable[[], Any] = duckdb.connect,
    source_loader: Callable[[RuntimeConfig], SourceBundle] = read_source_bundle,
    output_publisher: Callable[..., PublishedOutputs] = write_outputs,
) -> PipelineResult:
    """Execute all eight core relations, verify the fixture, and publish JSONL."""

    # The configured backend changes execution only; the relation code is shared.
    configure_runner(runner=config.runner)
    # Fail before computation if either PostgreSQL or MinIO is unavailable.
    runtime_probe(config)
    source = source_loader(config)
    executed: list[str] = []
    with TemporaryDirectory(
        prefix=f"procurement-audit-{config.runner}-"
    ) as workspace_root:
        workspace = RunnerWorkspace(Path(workspace_root))
        connection = connection_factory()
        try:
            # Register the validated PostgreSQL snapshot and Vane functions.
            _register_inputs(connection, source)
            runtime_function_attacher(connection, config)
            # Normalize scores and OCR MinIO evidence through the active Runner.
            for relation_name, sql_path in PRE_AI_STAGES:
                if relation_name == "int_evidence_ocr":
                    _create_evidence_ocr_table(
                        connection,
                        config,
                        workspace=workspace,
                    )
                else:
                    _execute_sql_file(connection, sql_path)
                executed.append(relation_name)

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
                response_materializer=materialize_relation,
                result_factory=lambda table: table,
            )
            register_or_replace_table(
                connection,
                "int_evidence_ai",
                ai_table,
            )
            executed.append("int_evidence_ai")

            # Validate AI facts, compute score impact, and build both marts.
            for relation_name, sql_path in POST_AI_STAGES:
                if relation_name == "int_conflict_facts":
                    _create_runner_conflict_facts(
                        connection,
                        sql_path,
                        workspace=workspace,
                    )
                else:
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
