"""Explicit single-connection orchestration for the eight-node SQL DAG."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import duckdb
import pyarrow as pa
import vane

from .ai import build_evidence_ai_relation
from .config import RuntimeConfig
from .minio_store import MinioStore
from .output_writer import PublishedOutputs, write_outputs
from .pg import connect_postgres, probe_postgres, read_source_rows
from .source_data import SourceBundle, source_bundle_from_rows
from .vane_functions import EvidenceOcrActor, validate_audit_fact_json_udf
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


class RuntimeConnectionError(ConnectionError):
    """Raised when PostgreSQL or MinIO cannot be reached."""


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


def _execute_sql_file(connection: Any, path: Path) -> None:
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


def _register_inputs(connection: Any, source: SourceBundle) -> None:
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

    configure_runner(runner=config.runner)
    runtime_probe(config)
    source = source_loader(config)
    executed: list[str] = []
    connection = connection_factory()
    try:
        _register_inputs(connection, source)
        runtime_function_attacher(connection, config)
        for relation_name, sql_path in PRE_AI_STAGES:
            _execute_sql_file(connection, sql_path)
            executed.append(relation_name)

        ocr_rows = _relation_rows(
            connection,
            "int_evidence_ocr",
            order_by="file_id",
        )
        ai_relation = ai_relation_builder(
            ocr_rows,
            connection,
            source,
            config,
        )
        register_or_replace_table(
            connection,
            "int_evidence_ai",
            ai_relation.to_arrow_table(),
        )
        executed.append("int_evidence_ai")

        for relation_name, sql_path in POST_AI_STAGES:
            _execute_sql_file(connection, sql_path)
            executed.append(relation_name)
        findings = _relation_rows(connection, "audit_findings", order_by="rule_id")
        summaries = _relation_rows(connection, "audit_summary", order_by="project_id")
    finally:
        connection.close()

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
