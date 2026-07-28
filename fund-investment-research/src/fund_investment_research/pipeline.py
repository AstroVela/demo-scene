"""Ray-only end-to-end Relation pipeline for the synthetic research case."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import vane

from .ai import ROLE_INSTRUCTIONS, SYSTEM_MESSAGE, extract_document_with_vane
from .config import RuntimeConfig
from .contracts import stable_json
from .domain_logic import (
    audio_fact_candidates,
    bind_ai_facts,
    extract_number_tokens,
    glossary_fingerprint,
    has_uncertain_number,
    sorted_unique,
)
from .evidence_report import render_evidence_report
from .minio_store import MinioStore
from .output_writer import PublishedOutputs, publish_outputs
from .source_data import (
    BusinessSnapshot,
    SourceContractError,
    SourceObject,
    fetch_and_validate_source,
    load_business_snapshot,
)
from .stage_state import StageStateStore
from .vane_functions import (
    configured_asr_actor,
    configured_glossary_function,
    configured_ocr_actor,
)


PACKAGE_ROOT = Path(__file__).resolve().parent
STATUS_SQL_PATH = PACKAGE_ROOT / "sql" / "research_signals.sql"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


class PipelineIncompleteError(RuntimeError):
    """Raised after valid per-source work is persisted but publication is unsafe."""


@dataclass(frozen=True)
class PipelineResult:
    run_id: str
    scenario: str
    published: PublishedOutputs
    signal_count: int
    fact_count: int
    review_task_count: int
    recomputed_stages: tuple[dict[str, str], ...]


class RunnerWorkspace:
    """Stage driver Arrow values as Parquet so every Vane plan is Ray-portable."""

    def __init__(self, root: Path, connection: Any):
        self.root = root
        self.connection = connection
        self.counter = 0

    def _path(self, name: str) -> Path:
        if not _SAFE_NAME.fullmatch(name):
            raise ValueError(f"unsafe workspace name: {name!r}")
        self.counter += 1
        return self.root / f"{self.counter:03d}-{name}.parquet"

    @staticmethod
    def _literal(path: Path) -> str:
        return "'" + str(path).replace("'", "''") + "'"

    def stage_table(self, name: str, table: pa.Table) -> Path:
        path = self._path(name)
        pq.write_table(table, path)
        return path

    def relation_from_table(self, name: str, table: pa.Table):
        path = self.stage_table(name, table)
        return self.connection.sql(
            f"select * from read_parquet({self._literal(path)})"
        )

    def materialize(self, relation: Any, name: str) -> pa.Table:
        path = self._path(name)
        relation.write_parquet(str(path))
        return pq.read_table(path)

    def register_view(self, name: str, table: pa.Table) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"unsafe DuckDB view name: {name!r}")
        path = self.stage_table(name.replace("_", "-"), table)
        self.connection.execute(
            f"create or replace view {name} as "
            f"select * from read_parquet({self._literal(path)})"
        )


def _probe_json(url: str, expected_model: str | None, label: str) -> None:
    try:
        response = httpx.get(url, timeout=10.0, trust_env=False)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise ConnectionError(f"{label} health check failed at {url}") from exc
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise ConnectionError(f"{label} health response is invalid")
    if expected_model is not None and payload.get("model") != expected_model:
        raise ConnectionError(
            f"{label} model mismatch; expected {expected_model!r}, "
            f"got {payload.get('model')!r}"
        )


def probe_runtime(config: RuntimeConfig) -> None:
    MinioStore(config.minio).probe()
    _probe_json(config.asr.health_url, config.asr.model, "ASR")
    _probe_json(config.ai.health_url, config.ai.model, "AI")


def _stage_version(base: str, dependency: str | None = None) -> str:
    return base if dependency is None else f"{base}+{dependency[:12]}"


def _arrow_from_rows(rows: list[dict[str, Any]], schema: pa.Schema | None = None) -> pa.Table:
    if schema is not None:
        return pa.Table.from_pylist(rows, schema=schema)
    if not rows:
        raise ValueError("cannot build Arrow input from zero rows")
    return pa.Table.from_pylist(rows)


def _source_input_table(objects: list[SourceObject]) -> pa.Table:
    return pa.table(
        {
            "source_id": pa.array(
                [row.metadata["source_id"] for row in objects], type=pa.string()
            ),
            "bucket": pa.array(
                [row.metadata["bucket"] for row in objects], type=pa.string()
            ),
            "object_key": pa.array(
                [row.metadata["object_key"] for row in objects], type=pa.string()
            ),
            "media_type": pa.array(
                [row.metadata["media_type"] for row in objects], type=pa.string()
            ),
            "object_bytes": pa.array(
                [row.content for row in objects], type=pa.binary()
            ),
        }
    )


def _asr_schema() -> dict[str, str]:
    return {
        "source_id": "VARCHAR",
        "segment_id": "VARCHAR",
        "start_seconds": "DOUBLE",
        "end_seconds": "DOUBLE",
        "raw_text": "VARCHAR",
        "language": "VARCHAR",
        "asr_confidence": "DOUBLE",
        "confidence_method": "VARCHAR",
        "source_locator": "VARCHAR",
    }


def _corrected_schema() -> dict[str, str]:
    return {
        **_asr_schema(),
        "corrected_text": "VARCHAR",
        "corrections_json": "VARCHAR",
        "knowledge_status": "VARCHAR",
    }


def _ocr_schema() -> dict[str, str]:
    return {
        "source_id": "VARCHAR",
        "ocr_status": "VARCHAR",
        "ocr_text": "VARCHAR",
        "ocr_confidence": "DOUBLE",
        "source_locator": "VARCHAR",
        "page_image_bytes": "BLOB",
        "error_code": "VARCHAR",
    }


def _cached_or_pending(
    state: StageStateStore,
    *,
    resume: bool,
    scenario: str,
    source: dict[str, Any],
    stage: str,
    version: str,
) -> dict[str, Any] | None:
    if not resume:
        return None
    return state.successful_result(
        logical_scenario=scenario,
        source_id=source["source_id"],
        source_sha256=source["sha256"],
        stage=stage,
        stage_version=version,
    )


def _begin_stage(
    state: StageStateStore,
    recomputed: list[dict[str, str]],
    *,
    run_id: str,
    scenario: str,
    source: dict[str, Any],
    stage: str,
    version: str,
) -> None:
    state.begin(
        run_id=run_id,
        logical_scenario=scenario,
        source_id=source["source_id"],
        source_sha256=source["sha256"],
        stage=stage,
        stage_version=version,
    )
    recomputed.append(
        {
            "source_id": source["source_id"],
            "source_sha256": source["sha256"],
            "stage": stage,
            "stage_version": version,
        }
    )


def _finish_stage(
    state: StageStateStore,
    *,
    scenario: str,
    source: dict[str, Any],
    stage: str,
    version: str,
    status: str,
    error_code: str | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    state.finish(
        logical_scenario=scenario,
        source_id=source["source_id"],
        source_sha256=source["sha256"],
        stage=stage,
        stage_version=version,
        status=status,
        error_code=error_code,
        result=result,
    )


def _run_asr(
    *,
    objects: list[SourceObject],
    snapshot: BusinessSnapshot,
    config: RuntimeConfig,
    state: StageStateStore,
    workspace: RunnerWorkspace,
    run_id: str,
    resume: bool,
    recomputed: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    stage = "asr"
    version = config.versions.asr
    results: dict[str, dict[str, Any]] = {}
    pending: list[SourceObject] = []
    for obj in objects:
        cached = _cached_or_pending(
            state,
            resume=resume,
            scenario=snapshot.logical_scenario,
            source=obj.metadata,
            stage=stage,
            version=version,
        )
        if cached is not None:
            results[obj.metadata["source_id"]] = dict(cached["row"])
        else:
            _begin_stage(
                state,
                recomputed,
                run_id=run_id,
                scenario=snapshot.logical_scenario,
                source=obj.metadata,
                stage=stage,
                version=version,
            )
            pending.append(obj)
    if pending:
        relation = workspace.relation_from_table("asr-input", _source_input_table(pending))
        transcribed = relation.map_batches(
            configured_asr_actor(
                base_url=config.asr.base_url,
                model=config.asr.model,
                language=config.asr.language,
                timeout_seconds=config.asr.timeout_seconds,
            ),
            schema=_asr_schema(),
            batch_size=config.asr.batch_size,
            actor_number=1,
            gpus=0,
            execution_backend="ray_actor",
        )
        try:
            table = workspace.materialize(transcribed, "asr-output")
        except Exception as exc:
            for obj in pending:
                _finish_stage(
                    state,
                    scenario=snapshot.logical_scenario,
                    source=obj.metadata,
                    stage=stage,
                    version=version,
                    status="failed",
                    error_code=f"ASR_SYSTEM:{type(exc).__name__}",
                )
            raise
        for row in table.to_pylist():
            source_id = row["source_id"]
            results[source_id] = row
            source = next(obj.metadata for obj in pending if obj.metadata["source_id"] == source_id)
            _finish_stage(
                state,
                scenario=snapshot.logical_scenario,
                source=source,
                stage=stage,
                version=version,
                status="succeeded",
                result={"row": row},
            )

    correction_stage = "asr_correction"
    glossary_hash = glossary_fingerprint(snapshot.domain_terms)
    correction_dependency = hashlib.sha256(
        f"{config.versions.asr}:{glossary_hash}".encode("utf-8")
    ).hexdigest()
    correction_version = _stage_version(
        config.versions.correction,
        correction_dependency,
    )
    corrected: dict[str, dict[str, Any]] = {}
    correction_pending: list[dict[str, Any]] = []
    for obj in objects:
        cached = _cached_or_pending(
            state,
            resume=resume,
            scenario=snapshot.logical_scenario,
            source=obj.metadata,
            stage=correction_stage,
            version=correction_version,
        )
        if cached is not None:
            corrected[obj.metadata["source_id"]] = dict(cached["row"])
        else:
            _begin_stage(
                state,
                recomputed,
                run_id=run_id,
                scenario=snapshot.logical_scenario,
                source=obj.metadata,
                stage=correction_stage,
                version=correction_version,
            )
            correction_pending.append(results[obj.metadata["source_id"]])
    if correction_pending:
        correction_input_schema = pa.schema(
            [
                ("source_id", pa.string()),
                ("segment_id", pa.string()),
                ("start_seconds", pa.float64()),
                ("end_seconds", pa.float64()),
                ("raw_text", pa.string()),
                ("language", pa.string()),
                ("asr_confidence", pa.float64()),
                ("confidence_method", pa.string()),
                ("source_locator", pa.string()),
            ]
        )
        relation = workspace.relation_from_table(
            "correction-input",
            pa.Table.from_pylist(correction_pending, schema=correction_input_schema),
        )
        relation = relation.map_batches(
            configured_glossary_function(snapshot.domain_terms),
            schema=_corrected_schema(),
            batch_size=64,
            execution_backend="ray_task",
        )
        try:
            table = workspace.materialize(relation, "correction-output")
        except Exception as exc:
            pending_ids = {row["source_id"] for row in correction_pending}
            for obj in objects:
                if obj.metadata["source_id"] in pending_ids:
                    _finish_stage(
                        state,
                        scenario=snapshot.logical_scenario,
                        source=obj.metadata,
                        stage=correction_stage,
                        version=correction_version,
                        status="failed",
                        error_code=f"CORRECTION_SYSTEM:{type(exc).__name__}",
                    )
            raise
        for row in table.to_pylist():
            source_id = row["source_id"]
            corrected[source_id] = row
            source = next(obj.metadata for obj in objects if obj.metadata["source_id"] == source_id)
            _finish_stage(
                state,
                scenario=snapshot.logical_scenario,
                source=source,
                stage=correction_stage,
                version=correction_version,
                status="succeeded",
                result={"row": row},
            )
    transcript_rows: list[dict[str, Any]] = []
    correction_rows: list[dict[str, Any]] = []
    for obj in objects:
        row = corrected[obj.metadata["source_id"]]
        events = json.loads(row.pop("corrections_json"))
        transcript_rows.append(
            {
                **row,
                "source_sha256": obj.metadata["sha256"],
                "asr_model": config.asr.model,
                "asr_stage_version": version,
                "correction_stage_version": correction_version,
            }
        )
        correction_rows.extend(events)
    return transcript_rows, correction_rows


def _serialize_ocr_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: value for key, value in row.items() if key != "page_image_bytes"},
        "page_image_base64": base64.b64encode(row["page_image_bytes"]).decode("ascii"),
    }


def _deserialize_ocr_row(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    encoded = result.pop("page_image_base64")
    result["page_image_bytes"] = base64.b64decode(encoded)
    return result


def _run_ocr(
    *,
    objects: list[SourceObject],
    snapshot: BusinessSnapshot,
    config: RuntimeConfig,
    state: StageStateStore,
    workspace: RunnerWorkspace,
    run_id: str,
    resume: bool,
    recomputed: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[str]]:
    stage = "ocr"
    version = config.versions.ocr
    results: dict[str, dict[str, Any]] = {}
    pending: list[SourceObject] = []
    failures: list[str] = []
    for obj in objects:
        cached = _cached_or_pending(
            state,
            resume=resume,
            scenario=snapshot.logical_scenario,
            source=obj.metadata,
            stage=stage,
            version=version,
        )
        if cached is not None:
            results[obj.metadata["source_id"]] = _deserialize_ocr_row(cached["row"])
        else:
            _begin_stage(
                state,
                recomputed,
                run_id=run_id,
                scenario=snapshot.logical_scenario,
                source=obj.metadata,
                stage=stage,
                version=version,
            )
            pending.append(obj)
    if pending:
        relation = workspace.relation_from_table("ocr-input", _source_input_table(pending))
        ocr_relation = relation.map_batches(
            configured_ocr_actor(
                minimum_confidence=config.ocr.minimum_confidence
            ),
            schema=_ocr_schema(),
            batch_size=config.ocr.batch_size,
            actor_number=1,
            gpus=0,
            execution_backend="ray_actor",
        )
        try:
            table = workspace.materialize(ocr_relation, "ocr-output")
        except Exception as exc:
            for obj in pending:
                _finish_stage(
                    state,
                    scenario=snapshot.logical_scenario,
                    source=obj.metadata,
                    stage=stage,
                    version=version,
                    status="failed",
                    error_code=f"OCR_SYSTEM:{type(exc).__name__}",
                )
            raise
        for row in table.to_pylist():
            source_id = row["source_id"]
            source = next(obj.metadata for obj in pending if obj.metadata["source_id"] == source_id)
            results[source_id] = row
            status = row["ocr_status"]
            _finish_stage(
                state,
                scenario=snapshot.logical_scenario,
                source=source,
                stage=stage,
                version=version,
                status=status,
                error_code=row["error_code"],
                result={"row": _serialize_ocr_row(row)},
            )
            if status != "succeeded":
                failures.append(f"{source_id}:{row['error_code']}")
    return [results[obj.metadata["source_id"]] for obj in objects], failures


def _run_document_ai(
    *,
    objects: list[SourceObject],
    ocr_rows: list[dict[str, Any]],
    snapshot: BusinessSnapshot,
    config: RuntimeConfig,
    state: StageStateStore,
    workspace: RunnerWorkspace,
    run_id: str,
    resume: bool,
    recomputed: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    stage = "ai_extract"
    version = _stage_version(
        config.versions.ai_extract,
        config.versions.ocr,
    )
    facts: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    failures: list[str] = []
    ocr_by_source = {row["source_id"]: row for row in ocr_rows}
    for obj in objects:
        source = obj.metadata
        ocr = ocr_by_source[source["source_id"]]
        if ocr["ocr_status"] != "succeeded":
            continue
        cached = _cached_or_pending(
            state,
            resume=resume,
            scenario=snapshot.logical_scenario,
            source=source,
            stage=stage,
            version=version,
        )
        response: dict[str, Any] | None = None
        if cached is not None:
            response = dict(cached["response"])
        else:
            _begin_stage(
                state,
                recomputed,
                run_id=run_id,
                scenario=snapshot.logical_scenario,
                source=source,
                stage=stage,
                version=version,
            )
            try:
                response = extract_document_with_vane(
                    source_role=source["source_role"],
                    ocr_text=ocr["ocr_text"],
                    image_bytes=ocr["page_image_bytes"],
                    ai_config=config.ai,
                    relation_factory=workspace.relation_from_table,
                    materialize=workspace.materialize,
                    request_name=source["source_id"].lower(),
                )
            except Exception as exc:
                _finish_stage(
                    state,
                    scenario=snapshot.logical_scenario,
                    source=source,
                    stage=stage,
                    version=version,
                    status="failed",
                    error_code=f"AI_CONTRACT:{type(exc).__name__}",
                )
                failures.append(
                    f"{source['source_id']}:AI_CONTRACT:{' '.join(str(exc).split())}"
                )
                continue
            _finish_stage(
                state,
                scenario=snapshot.logical_scenario,
                source=source,
                stage=stage,
                version=version,
                status="succeeded",
                result={"response": response},
            )
        bound_facts, bound_edges = bind_ai_facts(
            response,
            source=source,
            signal_ids=snapshot.signals_for_source(source["source_id"]),
            model_version=config.ai.model,
            pipeline_version=config.versions.pipeline,
        )
        facts.extend(bound_facts)
        edges.extend(bound_edges)
    return facts, edges, failures


def _uncertain_audio_fact(
    transcript: dict[str, Any],
    source: dict[str, Any],
    pipeline_version: str,
) -> dict[str, Any]:
    return {
        "fact_id": "FACT-SRC-AUDIO-UNC-001",
        "company_id": source["company_id"],
        "signal_id": None,
        "source_id": source["source_id"],
        "fact_type": "statement",
        "entity_id": source["company_id"],
        "metric_code": "DOR",
        "value_numeric": None,
        "value_text": "ambiguous: six or sixteen months",
        "unit": "months",
        "period_start": None,
        "period_end": None,
        "source_quote": transcript["corrected_text"],
        "source_locator": transcript["source_locator"],
        "knowledge_kind": "uncertainty",
        "trust_tier": source["trust_tier"],
        "confidence": 0.5,
        "extraction_method": "uncertain_number_gate",
        "model_version": "whisper-small",
        "pipeline_version": pipeline_version,
        "review_required": True,
    }


def _run_signal_sql(
    *,
    workspace: RunnerWorkspace,
    snapshot: BusinessSnapshot,
    facts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    signal_input = [
        {
            "signal_id": row["signal_id"],
            "thesis_id": row["thesis_id"],
            "company_id": row["company_id"],
        }
        for row in snapshot.signals
    ]
    condition_input = [
        {
            "thesis_id": row["thesis_id"],
            "metric_code": row["metric_code"],
            "operator": row["operator"],
            "threshold_numeric": row["threshold_numeric"],
        }
        for row in snapshot.conditions
    ]
    fact_input = [
        {
            "fact_id": row["fact_id"],
            "signal_id": row["signal_id"],
            "metric_code": row["metric_code"],
            "value_numeric": row["value_numeric"],
            "value_text": row["value_text"],
            "trust_tier": row["trust_tier"],
            "knowledge_kind": row["knowledge_kind"],
        }
        for row in facts
    ]
    workspace.register_view(
        "incoming_signals",
        pa.Table.from_pylist(
            signal_input,
            schema=pa.schema(
                [
                    ("signal_id", pa.string()),
                    ("thesis_id", pa.string()),
                    ("company_id", pa.string()),
                ]
            ),
        ),
    )
    workspace.register_view(
        "thesis_conditions",
        pa.Table.from_pylist(
            condition_input,
            schema=pa.schema(
                [
                    ("thesis_id", pa.string()),
                    ("metric_code", pa.string()),
                    ("operator", pa.string()),
                    ("threshold_numeric", pa.float64()),
                ]
            ),
        ),
    )
    workspace.register_view(
        "research_facts",
        pa.Table.from_pylist(
            fact_input,
            schema=pa.schema(
                [
                    ("fact_id", pa.string()),
                    ("signal_id", pa.string()),
                    ("metric_code", pa.string()),
                    ("value_numeric", pa.float64()),
                    ("value_text", pa.string()),
                    ("trust_tier", pa.int64()),
                    ("knowledge_kind", pa.string()),
                ]
            ),
        ),
    )
    statement = STATUS_SQL_PATH.read_text(encoding="utf-8")
    relation = workspace.connection.sql(statement)
    rows = workspace.materialize(relation, "research-signals").to_pylist()
    for row in rows:
        row["has_trusted_regulatory_conflict"] = bool(
            row["has_trusted_regulatory_conflict"]
        )
    return rows


def _review_tasks(
    *,
    transcript: dict[str, Any],
    facts: list[dict[str, Any]],
    signals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fact_by_id = {row["fact_id"]: row for row in facts}
    by_signal: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        if fact.get("signal_id"):
            by_signal.setdefault(fact["signal_id"], []).append(fact)
    tasks = [
        {
            "task_id": "TASK-ASR-DOR-001",
            "signal_id": None,
            "task_type": "uncertain_number",
            "priority": "medium",
            "judgment_id": "AUDIO-DOR-NUMBER",
            "evidence_fact_ids": ["FACT-SRC-AUDIO-UNC-001"],
            "source_locator": transcript["source_locator"],
            "recommended_action": "Listen at the cited timestamp and confirm whether DOR was six or sixteen months.",
            "status": "open",
        }
    ]
    task_specs = {
        "SIG-CLINICAL": (
            "thesis_condition_review",
            "high",
            "COND-EFFICACY+COND-SAFETY",
            "Review overall efficacy/safety deviations while retaining the small-subgroup counter-evidence.",
        ),
        "SIG-REGULATORY": (
            "trusted_source_conflict",
            "high",
            "COND-REGULATORY",
            "Reconcile the official and expert BLA timing statements with both source owners.",
        ),
        "SIG-RUMOR": (
            "source_verification",
            "medium",
            "COND-REGULATORY",
            "Obtain an original filing or trusted confirmation before changing any thesis state.",
        ),
    }
    state_by_signal = {row["signal_id"]: row["state"] for row in signals}
    for signal_id, (task_type, priority, judgment, action) in task_specs.items():
        signal_facts = sorted(by_signal.get(signal_id, []), key=lambda row: row["fact_id"])
        if not signal_facts:
            raise ValueError(f"cannot create focused task without facts: {signal_id}")
        tasks.append(
            {
                "task_id": f"TASK-{signal_id[4:]}-001",
                "signal_id": signal_id,
                "task_type": task_type,
                "priority": priority,
                "judgment_id": judgment,
                "evidence_fact_ids": [row["fact_id"] for row in signal_facts],
                "source_locator": signal_facts[0]["source_locator"],
                "recommended_action": action,
                "status": "open",
            }
        )
    expected_task_states = {
        "SIG-CLINICAL": "thesis_review_required",
        "SIG-REGULATORY": "manual_review",
        "SIG-RUMOR": "insufficient_evidence",
    }
    for signal_id, expected in expected_task_states.items():
        if state_by_signal.get(signal_id) != expected:
            # The verifier will print the detailed state mismatch; this guard
            # prevents publishing tasks that contradict the SQL decision.
            raise ValueError(
                f"focused task state mismatch for {signal_id}: "
                f"expected {expected}, got {state_by_signal.get(signal_id)}"
            )
    return tasks


def _package_sha256() -> str:
    digest = hashlib.sha256()
    paths = sorted(
        [
            *PACKAGE_ROOT.glob("*.py"),
            *PACKAGE_ROOT.glob("sql/*.sql"),
        ],
        key=lambda path: str(path.relative_to(PACKAGE_ROOT)),
    )
    for path in paths:
        digest.update(str(path.relative_to(PACKAGE_ROOT)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _runtime_identity() -> dict[str, Any]:
    connection = duckdb.connect()
    try:
        engine_version, source_revision, _ = connection.execute(
            "pragma version"
        ).fetchone()
    finally:
        connection.close()
    distribution = importlib_metadata.distribution("vane-ai")
    direct_url = distribution.read_text("direct_url.json")
    return {
        "vane_distribution_version": importlib_metadata.version("vane-ai"),
        "vane_api_version": vane.__version__,
        "duckdb_python_version": duckdb.__version__,
        "duckdb_engine_version": engine_version,
        "duckdb_source_revision": source_revision,
        "vane_direct_url": json.loads(direct_url)["url"] if direct_url else None,
    }


def _quality_metrics(
    transcripts: list[dict[str, Any]],
    corrections: list[dict[str, Any]],
) -> dict[str, Any]:
    raw = " ".join(row["raw_text"] for row in transcripts)
    corrected = " ".join(row["corrected_text"] for row in transcripts)
    return {
        "raw_domain_term_hits": {
            "Lanxing Biotech": "lanxing biotech" in raw.casefold(),
            "Nectin-4": "nectin-4" in raw.casefold(),
        },
        "corrected_domain_term_hits": {
            "Lanxing Biotech": "lanxing biotech" in corrected.casefold(),
            "Nectin-4": "nectin-4" in corrected.casefold(),
        },
        "traceable_correction_count": len(corrections),
        "raw_number_tokens": extract_number_tokens(raw),
        "corrected_number_tokens": extract_number_tokens(corrected),
        "numbers_silently_rewritten": (
            extract_number_tokens(raw) != extract_number_tokens(corrected)
        ),
        "knowledge_status": transcripts[0]["knowledge_status"],
    }


def run_pipeline(config: RuntimeConfig, *, resume: bool = False) -> PipelineResult:
    """Execute real ASR/OCR/AI, deterministic SQL, and atomic publication."""

    if config.runner != "ray":
        raise ValueError("this Demo supports Ray Runner only")
    vane.configure(runner="ray")
    vane.runners.set_runner_ray(
        address=config.ray.address,
        noop_if_initialized=True,
    )
    probe_runtime(config)
    snapshot = load_business_snapshot(config)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    recomputed: list[dict[str, str]] = []
    gate_failures: list[str] = []
    source_objects: list[SourceObject] = []
    store = MinioStore(config.minio)
    with StageStateStore(config.postgres) as stage_state, tempfile.TemporaryDirectory(
        prefix="fund-research-ray-"
    ) as root:
        connection = vane.connect()
        workspace = RunnerWorkspace(Path(root), connection)
        try:
            for source in snapshot.sources:
                cached = _cached_or_pending(
                    stage_state,
                    resume=resume,
                    scenario=snapshot.logical_scenario,
                    source=source,
                    stage="source_gate",
                    version=config.versions.source_gate,
                )
                try:
                    obj = fetch_and_validate_source(source, store)
                except SourceContractError as exc:
                    _begin_stage(
                        stage_state,
                        recomputed,
                        run_id=run_id,
                        scenario=snapshot.logical_scenario,
                        source=source,
                        stage="source_gate",
                        version=config.versions.source_gate,
                    )
                    _finish_stage(
                        stage_state,
                        scenario=snapshot.logical_scenario,
                        source=source,
                        stage="source_gate",
                        version=config.versions.source_gate,
                        status="quarantined",
                        error_code=exc.code,
                    )
                    gate_failures.append(f"{source['source_id']}:{exc.code}")
                    continue
                if cached is None:
                    _begin_stage(
                        stage_state,
                        recomputed,
                        run_id=run_id,
                        scenario=snapshot.logical_scenario,
                        source=source,
                        stage="source_gate",
                        version=config.versions.source_gate,
                    )
                    _finish_stage(
                        stage_state,
                        scenario=snapshot.logical_scenario,
                        source=source,
                        stage="source_gate",
                        version=config.versions.source_gate,
                        status="succeeded",
                        result={"validated": True, "byte_length": len(obj.content)},
                    )
                source_objects.append(obj)

            audio_objects = [
                obj for obj in source_objects if obj.metadata["media_type"] == "audio/wav"
            ]
            document_objects = [
                obj
                for obj in source_objects
                if obj.metadata["media_type"] in {"application/pdf", "image/png"}
            ]
            transcripts, corrections = _run_asr(
                objects=audio_objects,
                snapshot=snapshot,
                config=config,
                state=stage_state,
                workspace=workspace,
                run_id=run_id,
                resume=resume,
                recomputed=recomputed,
            )
            ocr_rows, ocr_failures = _run_ocr(
                objects=document_objects,
                snapshot=snapshot,
                config=config,
                state=stage_state,
                workspace=workspace,
                run_id=run_id,
                resume=resume,
                recomputed=recomputed,
            )
            document_facts, edges, ai_failures = _run_document_ai(
                objects=document_objects,
                ocr_rows=ocr_rows,
                snapshot=snapshot,
                config=config,
                state=stage_state,
                workspace=workspace,
                run_id=run_id,
                resume=resume,
                recomputed=recomputed,
            )
            failures = [*gate_failures, *ocr_failures, *ai_failures]
            if failures:
                raise PipelineIncompleteError(
                    "source processing did not produce a complete snapshot; "
                    + ", ".join(failures)
                )
            if len(transcripts) != 1:
                raise PipelineIncompleteError("exactly one audio transcript is required")
            audio_source = audio_objects[0].metadata
            audio_facts = audio_fact_candidates(
                transcripts[0],
                source=audio_source,
                pipeline_version=config.versions.pipeline,
            )
            if not has_uncertain_number(transcripts[0]["corrected_text"]):
                raise PipelineIncompleteError(
                    "ASR output did not preserve the fixture's ambiguous DOR number"
                )
            audio_facts.append(
                _uncertain_audio_fact(
                    transcripts[0],
                    audio_source,
                    config.versions.pipeline,
                )
            )
            facts = sorted([*audio_facts, *document_facts], key=lambda row: row["fact_id"])
            edges = sorted(edges, key=lambda row: row["edge_id"])
            signals = _run_signal_sql(
                workspace=workspace,
                snapshot=snapshot,
                facts=facts,
            )
            tasks = _review_tasks(
                transcript=transcripts[0],
                facts=facts,
                signals=signals,
            )
            current_hashes = {
                source["source_id"]: source["sha256"] for source in snapshot.sources
            }
            processing_status = stage_state.rows_for_sources(
                logical_scenario=snapshot.logical_scenario,
                current_hashes=current_hashes,
            )
            report = render_evidence_report(
                snapshot=snapshot,
                facts=facts,
                edges=edges,
                signals=signals,
                tasks=tasks,
            )
            quality = _quality_metrics(transcripts, corrections)
            package_sha = _package_sha256()
            manifest = {
                "run_id": run_id,
                "logical_scenario": snapshot.logical_scenario,
                "fixture_variant": snapshot.fixture_variant,
                "runner": "ray",
                "resume": resume,
                "resume_scope": recomputed if resume else [],
                "resume_recomputed_source_ids": (
                    sorted_unique(row["source_id"] for row in recomputed)
                    if resume
                    else []
                ),
                "input_sha256": current_hashes,
                "glossary_sha256": glossary_fingerprint(snapshot.domain_terms),
                "pipeline_sha256": package_sha,
                "prompt_sha256": hashlib.sha256(
                    (SYSTEM_MESSAGE + stable_json(ROLE_INSTRUCTIONS)).encode("utf-8")
                ).hexdigest(),
                "status_sql_sha256": hashlib.sha256(
                    STATUS_SQL_PATH.read_bytes()
                ).hexdigest(),
                "versions": {
                    key: getattr(config.versions, key)
                    for key in config.versions.__dataclass_fields__
                },
                "models": {
                    "asr": config.asr.model,
                    "ocr": config.ocr.backend,
                    "ai": config.ai.model,
                },
                "runtime": _runtime_identity(),
                "approved_thesis_versions": {
                    row["thesis_id"]: row["thesis_version"] for row in snapshot.theses
                },
                "relation_row_counts": {
                    "transcript_segments": len(transcripts),
                    "asr_corrections": len(corrections),
                    "research_facts": len(facts),
                    "thesis_impact_edges": len(edges),
                    "research_signals": len(signals),
                    "review_tasks": len(tasks),
                    "source_processing_status": len(processing_status),
                },
                "decision_contract": "deterministic_sql",
                "published_at": datetime.now(timezone.utc).isoformat(),
            }
            published = publish_outputs(
                output_root=config.output_dir,
                scenario=snapshot.logical_scenario,
                run_id=run_id,
                outputs={
                    "transcript_segments": sorted(
                        transcripts, key=lambda row: (row["source_id"], row["segment_id"])
                    ),
                    "asr_corrections": sorted(
                        corrections, key=lambda row: row["correction_id"]
                    ),
                    "research_facts": facts,
                    "thesis_impact_edges": edges,
                    "research_signals": sorted(
                        signals, key=lambda row: row["signal_id"]
                    ),
                    "review_tasks": sorted(tasks, key=lambda row: row["task_id"]),
                    "source_processing_status": processing_status,
                    "asr_quality_metrics": quality,
                    "run_manifest": manifest,
                },
                evidence_report=report,
            )
        finally:
            connection.close()
    return PipelineResult(
        run_id=run_id,
        scenario=snapshot.logical_scenario,
        published=published,
        signal_count=len(signals),
        fact_count=len(facts),
        review_task_count=len(tasks),
        recomputed_stages=tuple(recomputed),
    )
