"""Shared contracts for the claims evidence graph POC."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa


def _default_path(env_name: str, fallback: str | Path) -> Path:
    return Path(os.environ.get(env_name, str(fallback)))


DEFAULT_WORKSPACE_ROOT = _default_path("CLAIMS_POC_WORKSPACE_ROOT", "poc-data")
DEFAULT_DATA_ROOT = _default_path(
    "CLAIMS_POC_DATA_ROOT",
    DEFAULT_WORKSPACE_ROOT / "claims-poc",
)
DEFAULT_OUTPUT_DIR = _default_path(
    "CLAIMS_POC_OUTPUT_DIR",
    DEFAULT_DATA_ROOT / "outputs",
)

SUPPORTED_MEDIA_TYPES = {"image/jpeg", "image/png"}
SUPPORTED_RUN_PROFILES = {"baseline", "semantic", "semantic_strict"}
SEMANTIC_RUN_PROFILES = {"semantic", "semantic_strict"}

CLAIMS_REQUIRED_FIELDS = {
    "claim_id",
    "scenario",
    "description",
    "is_real_claim",
    "source_note",
}

CLAIM_FILES_REQUIRED_FIELDS = {
    "claim_id",
    "file_id",
    "role",
    "media_type",
    "source_dataset",
    "source_url",
    "raw_path",
    "poc_path",
    "notes",
}

PHOTO_HUMAN_LABELS_REQUIRED_FIELDS = {
    "claim_id",
    "file_id",
    "usable_for_review",
    "vehicle_visible",
    "target_vehicle_clear",
    "damage_visible",
    "damaged_parts_json",
    "damage_types_json",
    "severity_label",
    "needs_reshoot",
    "labeler_id",
    "labeled_at",
    "adjudication_status",
}

class ContractError(ValueError):
    """Raised when input or output data violates the POC contract."""


@dataclass(frozen=True)
class RunConfig:
    """Resolved runtime configuration for one POC execution."""

    data_root: Path = DEFAULT_DATA_ROOT
    workspace_root: Path = DEFAULT_WORKSPACE_ROOT
    output_dir: Path = DEFAULT_OUTPUT_DIR
    mode: str = "offline"
    profile: str = "baseline"
    batch_size: int = 8
    execution_backend: str = "local"
    runner: str = ""
    write_parquet: bool = True
    fail_on_warnings: bool = False
    photo_labels_path: Path | None = None
    image_model_provider: str = "openai"
    image_model: str = "Qwen2.5-VL-3B-Instruct"
    image_model_base_url: str = "http://127.0.0.1:8001/v1"
    image_model_api_key: str = "EMPTY"
    image_model_max_tokens: int = 768
    image_model_temperature: float = 0.0
    image_model_version: str = ""
    prompt_version: str = "photo_damage_v1"
    response_schema_version: str = "photo_damage_v1"
    max_image_model_errors: int = 0

    @property
    def requires_image_semantics(self) -> bool:
        return self.mode == "ai" or self.profile in SEMANTIC_RUN_PROFILES

    @property
    def enforces_image_model_error_budget(self) -> bool:
        return self.mode == "ai" or self.profile == "semantic_strict"

    @classmethod
    def from_args(cls, args: Any) -> "RunConfig":
        photo_labels_path = (
            Path(args.photo_labels_path).expanduser().resolve()
            if args.photo_labels_path
            else None
        )
        return cls(
            data_root=Path(args.data_root).expanduser().resolve(),
            workspace_root=Path(args.workspace_root).expanduser().resolve(),
            output_dir=Path(args.output_dir).expanduser().resolve(),
            mode=args.mode,
            profile=args.profile,
            batch_size=args.batch_size,
            execution_backend=args.execution_backend,
            runner=args.runner,
            write_parquet=not args.skip_parquet,
            fail_on_warnings=args.fail_on_warnings,
            photo_labels_path=photo_labels_path,
            image_model_provider=args.image_model_provider,
            image_model=args.image_model,
            image_model_base_url=args.image_model_base_url,
            image_model_api_key=args.image_model_api_key,
            image_model_max_tokens=args.image_model_max_tokens,
            image_model_temperature=args.image_model_temperature,
            image_model_version=args.image_model_version,
            prompt_version=args.prompt_version,
            response_schema_version=args.response_schema_version,
            max_image_model_errors=args.max_image_model_errors,
        )


@dataclass(frozen=True)
class TableContract:
    """Arrow and DuckDB schema contract for one output table."""

    name: str
    fields: dict[str, pa.DataType]

    @property
    def column_names(self) -> list[str]:
        return list(self.fields)

    def arrow_table(self, rows: list[dict[str, Any]]) -> pa.Table:
        return table_from_rows(rows, self.fields)

    def duckdb_schema(self) -> dict[str, Any]:
        return {name: duckdb_type(data_type) for name, data_type in self.fields.items()}


def duckdb_type(data_type: pa.DataType) -> Any:
    if pa.types.is_string(data_type):
        return duckdb.sqltypes.VARCHAR
    if pa.types.is_boolean(data_type):
        return duckdb.sqltypes.BOOLEAN
    if pa.types.is_int64(data_type):
        return duckdb.sqltypes.BIGINT
    if pa.types.is_float64(data_type):
        return duckdb.sqltypes.DOUBLE
    if pa.types.is_binary(data_type):
        return duckdb.sqltypes.BLOB
    raise TypeError(f"Unsupported Arrow type for DuckDB schema: {data_type}")


def table_from_rows(
    rows: list[dict[str, Any]],
    fields: dict[str, pa.DataType],
) -> pa.Table:
    return pa.table(
        {
            name: pa.array([row.get(name) for row in rows], type=data_type)
            for name, data_type in fields.items()
        }
    )


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


CLAIM_FILES = TableContract(
    "claim_files",
    {
        "claim_id": pa.string(),
        "file_id": pa.string(),
        "role": pa.string(),
        "media_type": pa.string(),
        "source_dataset": pa.string(),
        "source_url": pa.string(),
        "raw_path": pa.string(),
        "poc_path": pa.string(),
        "absolute_path": pa.string(),
        "annotation_absolute_path": pa.string(),
        "file_exists": pa.bool_(),
        "file_size_bytes": pa.int64(),
        "sha256": pa.string(),
    },
)

PHOTO_INPUT = TableContract(
    "photo_input",
    {
        "claim_id": pa.string(),
        "file_id": pa.string(),
        "file_size_bytes": pa.int64(),
        "absolute_path": pa.string(),
        "file_bytes": pa.binary(),
    },
)

PHOTO_EVIDENCE = TableContract(
    "photo_evidence",
    {
        "claim_id": pa.string(),
        "file_id": pa.string(),
        "decode_ok": pa.bool_(),
        "decode_error": pa.string(),
        "image_format": pa.string(),
        "image_mode": pa.string(),
        "image_width": pa.int64(),
        "image_height": pa.int64(),
        "file_size_bytes": pa.int64(),
        "aspect_ratio": pa.float64(),
        "megapixels": pa.float64(),
        "brightness_mean": pa.float64(),
        "brightness_std": pa.float64(),
        "blur_score": pa.float64(),
        "quality_score": pa.float64(),
        "issue_flags_json": pa.string(),
        "perceptual_hash": pa.string(),
        "quality_rule_version": pa.string(),
        "needs_review": pa.bool_(),
        "evidence_node_id": pa.string(),
        "source_path": pa.string(),
    },
)

PHOTO_DAMAGE_EVIDENCE = TableContract(
    "photo_damage_evidence",
    {
        "claim_id": pa.string(),
        "file_id": pa.string(),
        "vehicle_visible": pa.bool_(),
        "target_vehicle_clear": pa.bool_(),
        "damage_visible": pa.bool_(),
        "damaged_parts_json": pa.string(),
        "damage_types_json": pa.string(),
        "severity_hint": pa.string(),
        "evidence_description": pa.string(),
        "uncertainty_reasons_json": pa.string(),
        "confidence": pa.float64(),
        "model_provider": pa.string(),
        "model_name": pa.string(),
        "model_version": pa.string(),
        "prompt_version": pa.string(),
        "response_schema_version": pa.string(),
        "model_run_id": pa.string(),
        "raw_response_ref": pa.string(),
        "needs_review": pa.bool_(),
        "evidence_node_id": pa.string(),
        "source_path": pa.string(),
    },
)

PHOTO_MODEL_RUNS = TableContract(
    "photo_model_runs",
    {
        "run_id": pa.string(),
        "claim_id": pa.string(),
        "file_id": pa.string(),
        "provider": pa.string(),
        "model_name": pa.string(),
        "model_version": pa.string(),
        "prompt_version": pa.string(),
        "schema_version": pa.string(),
        "input_image_sha256": pa.string(),
        "request_started_at": pa.string(),
        "request_finished_at": pa.string(),
        "latency_ms": pa.float64(),
        "status": pa.string(),
        "error_code": pa.string(),
        "error_message": pa.string(),
        "raw_response_ref": pa.string(),
    },
)

DOCUMENT_INPUT = TableContract(
    "document_input",
    {
        "claim_id": pa.string(),
        "file_id": pa.string(),
        "absolute_path": pa.string(),
        "annotation_absolute_path": pa.string(),
    },
)

DOCUMENT_EVIDENCE = TableContract(
    "document_evidence",
    {
        "claim_id": pa.string(),
        "file_id": pa.string(),
        "document_id": pa.string(),
        "field_name": pa.string(),
        "field_value": pa.string(),
        "field_type": pa.string(),
        "bbox_json": pa.string(),
        "source_image_path": pa.string(),
        "annotation_path": pa.string(),
        "confidence": pa.float64(),
        "needs_review": pa.bool_(),
        "evidence_node_id": pa.string(),
    },
)

REVIEW_TASKS = TableContract(
    "review_tasks",
    {
        "claim_id": pa.string(),
        "task_id": pa.string(),
        "task_type": pa.string(),
        "priority": pa.string(),
        "source_file_id": pa.string(),
        "evidence_node_id": pa.string(),
        "reason": pa.string(),
        "suggested_action": pa.string(),
        "status": pa.string(),
    },
)

EVIDENCE_NODES = TableContract(
    "evidence_nodes",
    {
        "claim_id": pa.string(),
        "node_id": pa.string(),
        "node_type": pa.string(),
        "source_file_id": pa.string(),
        "source_path": pa.string(),
        "payload_json": pa.string(),
        "created_by": pa.string(),
        "requires_human_confirmation": pa.bool_(),
    },
)

CLAIM_SUMMARY = TableContract(
    "claim_summary",
    {
        "claim_id": pa.string(),
        "file_count": pa.int64(),
        "photo_count": pa.int64(),
        "document_count": pa.int64(),
        "review_task_count": pa.int64(),
        "photo_review_count": pa.int64(),
        "document_review_count": pa.int64(),
        "claim_packet_status": pa.string(),
    },
)

PHOTO_HUMAN_LABELS = TableContract(
    "photo_human_labels",
    {
        "claim_id": pa.string(),
        "file_id": pa.string(),
        "usable_for_review": pa.bool_(),
        "vehicle_visible": pa.bool_(),
        "target_vehicle_clear": pa.bool_(),
        "damage_visible": pa.bool_(),
        "damaged_parts_json": pa.string(),
        "damage_types_json": pa.string(),
        "severity_label": pa.string(),
        "needs_reshoot": pa.bool_(),
        "labeler_id": pa.string(),
        "labeled_at": pa.string(),
        "adjudication_status": pa.string(),
    },
)

PHOTO_EVAL_METRICS = TableContract(
    "photo_eval_metrics",
    {
        "metric_name": pa.string(),
        "prediction_rule": pa.string(),
        "label_rule": pa.string(),
        "support": pa.int64(),
        "unmatched_label_count": pa.int64(),
        "true_positive": pa.int64(),
        "false_positive": pa.int64(),
        "true_negative": pa.int64(),
        "false_negative": pa.int64(),
        "precision": pa.float64(),
        "recall": pa.float64(),
        "f1": pa.float64(),
        "notes": pa.string(),
    },
)

PHOTO_DAMAGE_EVAL_METRICS = TableContract(
    "photo_damage_eval_metrics",
    {
        "metric_name": pa.string(),
        "prediction_field": pa.string(),
        "label_field": pa.string(),
        "support": pa.int64(),
        "unmatched_label_count": pa.int64(),
        "true_positive": pa.int64(),
        "false_positive": pa.int64(),
        "true_negative": pa.int64(),
        "false_negative": pa.int64(),
        "precision": pa.float64(),
        "recall": pa.float64(),
        "f1": pa.float64(),
        "notes": pa.string(),
    },
)

OUTPUT_TABLES = {
    contract.name: contract
    for contract in (
        CLAIM_FILES,
        PHOTO_EVIDENCE,
        PHOTO_DAMAGE_EVIDENCE,
        PHOTO_MODEL_RUNS,
        DOCUMENT_EVIDENCE,
        EVIDENCE_NODES,
        REVIEW_TASKS,
        CLAIM_SUMMARY,
        PHOTO_HUMAN_LABELS,
        PHOTO_EVAL_METRICS,
        PHOTO_DAMAGE_EVAL_METRICS,
    )
}
