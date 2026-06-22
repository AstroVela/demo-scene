"""Production-oriented offline pipeline for the claims evidence graph POC."""

from __future__ import annotations

import json
import os
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import vane

from claims_evidence_graph_pipeline.contracts import (
    CLAIM_FILES,
    CLAIM_SUMMARY,
    DOCUMENT_INPUT,
    EVIDENCE_NODES,
    OUTPUT_TABLES,
    PHOTO_DAMAGE_EVIDENCE,
    PHOTO_HUMAN_LABELS,
    PHOTO_INPUT,
    PHOTO_MODEL_RUNS,
    REVIEW_TASKS,
    SUPPORTED_RUN_PROFILES,
    ContractError,
    RunConfig,
    sha256_bytes,
    stable_json,
)
from claims_evidence_graph_pipeline.evaluation import (
    evaluate_photo_damage,
    evaluate_photo_quality,
)
from claims_evidence_graph_pipeline.photo_vlm import (
    check_image_model_service,
    run_photo_damage_vlm,
)
from claims_evidence_graph_pipeline.udfs import (
    FUNSD_DOCUMENT_EXTRACT_UDF,
    PHOTO_QUALITY_UDF,
    run_batch_udf,
    valid_bbox,
)
from claims_evidence_graph_pipeline.validation import (
    ValidationReport,
    validate_label_inputs,
    validate_manifests,
    validate_outputs,
)

PHOTO_LABEL_JSON_FIELDS = {"damaged_parts_json", "damage_types_json"}


@dataclass(frozen=True)
class PipelineResult:
    """Important artifacts from one pipeline run."""

    output_dir: Path
    tables: dict[str, pa.Table]
    validation: ValidationReport
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PipelineInputs:
    """Validated input rows and relation-ready input tables."""

    claim_rows: list[dict[str, Any]]
    manifest_rows: list[dict[str, Any]]
    photo_label_rows: list[dict[str, Any]]
    input_validation: ValidationReport
    claim_file_table: pa.Table
    photo_input_table: pa.Table
    document_input_table: pa.Table


@dataclass(frozen=True)
class EvidenceStageTables:
    """Core evidence tables produced before aggregation/output materialization."""

    claim_files: pa.Table
    photo_evidence: pa.Table
    document_evidence: pa.Table
    photo_damage_evidence: pa.Table
    photo_model_runs: pa.Table
    review_tasks: pa.Table
    claim_summary: pa.Table
    evidence_nodes: pa.Table


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as input_file:
        for line_no, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ContractError(f"Invalid JSONL at {path}:{line_no}") from exc
    return rows


def read_optional_jsonl(path: Path | None, *, label: str) -> list[dict[str, Any]]:
    if path is None:
        return []
    if not path.exists():
        raise ContractError(f"{label} file does not exist: {path}")
    return read_jsonl(path)


def normalize_json_string_fields(
    rows: list[dict[str, Any]],
    fields: set[str],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        normalized_row = dict(row)
        for field in fields:
            if field not in normalized_row:
                continue
            value = normalized_row[field]
            if isinstance(value, str) or value is None:
                continue
            normalized_row[field] = stable_json(value)
        normalized.append(normalized_row)
    return normalized


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def atomic_write_jsonl(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as output_file:
        for row in table.to_pylist():
            output_file.write(json.dumps(row, ensure_ascii=False, default=str))
            output_file.write("\n")
    os.replace(tmp_path, path)


def atomic_write_parquet(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    pq.write_table(table, tmp_path)
    os.replace(tmp_path, path)


def remove_output_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def clear_stale_table_outputs(output_dir: Path) -> None:
    for name in OUTPUT_TABLES:
        remove_output_path(output_dir / f"{name}.jsonl")
        remove_output_path(output_dir / "parquet" / name)


def resolve_manifest_path(workspace_root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return workspace_root / path


def load_claim_files(
    manifest_rows: list[dict[str, Any]],
    workspace_root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    claim_file_rows: list[dict[str, Any]] = []
    photo_input_rows: list[dict[str, Any]] = []
    document_input_rows: list[dict[str, Any]] = []

    for manifest_row in manifest_rows:
        absolute_path = resolve_manifest_path(
            workspace_root,
            str(manifest_row["raw_path"]),
        )
        annotation_path = resolve_manifest_path(
            workspace_root,
            manifest_row.get("annotation_raw_path"),
        )
        assert absolute_path is not None

        file_exists = absolute_path.exists()
        data = absolute_path.read_bytes() if file_exists else None
        file_size = len(data) if data is not None else None

        claim_file_rows.append(
            {
                "claim_id": manifest_row["claim_id"],
                "file_id": manifest_row["file_id"],
                "role": manifest_row["role"],
                "media_type": manifest_row["media_type"],
                "source_dataset": manifest_row["source_dataset"],
                "source_url": manifest_row["source_url"],
                "raw_path": manifest_row["raw_path"],
                "poc_path": manifest_row["poc_path"],
                "absolute_path": str(absolute_path),
                "annotation_absolute_path": str(annotation_path)
                if annotation_path is not None
                else None,
                "file_exists": file_exists,
                "file_size_bytes": file_size,
                "sha256": sha256_bytes(data) if data is not None else None,
            }
        )

        if not file_exists:
            continue

        if manifest_row["media_type"] == "image/jpeg":
            photo_input_rows.append(
                {
                    "claim_id": manifest_row["claim_id"],
                    "file_id": manifest_row["file_id"],
                    "file_size_bytes": file_size,
                    "absolute_path": str(absolute_path),
                    "file_bytes": data,
                }
            )
        elif manifest_row["media_type"] == "image/png":
            document_input_rows.append(
                {
                    "claim_id": manifest_row["claim_id"],
                    "file_id": manifest_row["file_id"],
                    "absolute_path": str(absolute_path),
                    "annotation_absolute_path": str(annotation_path)
                    if annotation_path is not None
                    else None,
                }
            )

    return claim_file_rows, photo_input_rows, document_input_rows


def validate_run_config(config: RunConfig) -> None:
    if config.mode not in {"offline", "ai"}:
        raise ContractError("--mode must be offline or ai")
    if config.profile not in SUPPORTED_RUN_PROFILES:
        raise ContractError(
            "--profile must be one of " + ", ".join(sorted(SUPPORTED_RUN_PROFILES))
        )
    if config.batch_size < 1:
        raise ContractError("--batch-size must be at least 1")
    if config.max_image_model_errors < 0:
        raise ContractError("--max-image-model-errors must be non-negative")


def load_pipeline_inputs(config: RunConfig) -> PipelineInputs:
    claims_path = config.data_root / "manifests" / "claims.jsonl"
    claim_files_path = config.data_root / "manifests" / "claim_files.jsonl"
    claim_rows = read_jsonl(claims_path)
    manifest_rows = read_jsonl(claim_files_path)
    photo_label_rows = normalize_json_string_fields(
        read_optional_jsonl(config.photo_labels_path, label="photo labels"),
        PHOTO_LABEL_JSON_FIELDS,
    )

    input_validation = validate_manifests(
        claim_rows,
        manifest_rows,
        config.workspace_root,
    )
    input_validation.extend(
        validate_label_inputs(
            photo_label_rows=photo_label_rows,
            file_rows=manifest_rows,
        )
    )
    input_validation.raise_if_failed(fail_on_warnings=config.fail_on_warnings)

    claim_file_rows, photo_input_rows, document_input_rows = load_claim_files(
        manifest_rows,
        config.workspace_root,
    )

    return PipelineInputs(
        claim_rows=claim_rows,
        manifest_rows=manifest_rows,
        photo_label_rows=photo_label_rows,
        input_validation=input_validation,
        claim_file_table=CLAIM_FILES.arrow_table(claim_file_rows),
        photo_input_table=PHOTO_INPUT.arrow_table(photo_input_rows),
        document_input_table=DOCUMENT_INPUT.arrow_table(document_input_rows),
    )


def run_evidence_stages(
    conn: Any,
    inputs: PipelineInputs,
    config: RunConfig,
    *,
    run_image_semantics: bool,
) -> EvidenceStageTables:
    photo_table = run_batch_udf(
        conn,
        inputs.photo_input_table,
        PHOTO_QUALITY_UDF,
        batch_size=config.batch_size,
        execution_backend=config.execution_backend,
    )

    photo_damage_table = PHOTO_DAMAGE_EVIDENCE.arrow_table([])
    photo_model_run_table = PHOTO_MODEL_RUNS.arrow_table([])
    if run_image_semantics:
        photo_damage_table, photo_model_run_table = run_photo_damage_vlm(
            conn,
            inputs.photo_input_table,
            photo_table,
            config,
        )

    document_table = run_batch_udf(
        conn,
        inputs.document_input_table,
        FUNSD_DOCUMENT_EXTRACT_UDF,
        batch_size=config.batch_size,
        execution_backend=config.execution_backend,
    )

    review_task_table = build_review_tasks(
        inputs.claim_rows,
        inputs.claim_file_table,
        photo_table,
        document_table,
        photo_damage_table=photo_damage_table if run_image_semantics else None,
        photo_model_run_table=photo_model_run_table if run_image_semantics else None,
    )

    conn.register("claim_files", inputs.claim_file_table)
    conn.register("photo_evidence", photo_table)
    conn.register("document_evidence", document_table)
    conn.register("review_tasks", review_task_table)
    claim_summary_table = build_claim_summary(conn, inputs.claim_rows)

    evidence_node_table = build_evidence_nodes(
        inputs.claim_rows,
        inputs.claim_file_table,
        photo_table,
        document_table,
        review_task_table,
        photo_damage_table=photo_damage_table if run_image_semantics else None,
    )

    return EvidenceStageTables(
        claim_files=inputs.claim_file_table,
        photo_evidence=photo_table,
        document_evidence=document_table,
        photo_damage_evidence=photo_damage_table,
        photo_model_runs=photo_model_run_table,
        review_tasks=review_task_table,
        claim_summary=claim_summary_table,
        evidence_nodes=evidence_node_table,
    )


def assemble_output_tables(
    stages: EvidenceStageTables,
    inputs: PipelineInputs,
    config: RunConfig,
    *,
    run_image_semantics: bool,
) -> dict[str, pa.Table]:
    tables = {
        "claim_files": stages.claim_files,
        "photo_evidence": stages.photo_evidence,
        "document_evidence": stages.document_evidence,
        "evidence_nodes": stages.evidence_nodes,
        "review_tasks": stages.review_tasks,
        "claim_summary": stages.claim_summary,
    }
    if run_image_semantics:
        tables["photo_damage_evidence"] = stages.photo_damage_evidence
        tables["photo_model_runs"] = stages.photo_model_runs
    if config.photo_labels_path is not None:
        photo_label_table = PHOTO_HUMAN_LABELS.arrow_table(inputs.photo_label_rows)
        tables["photo_human_labels"] = photo_label_table
        if inputs.photo_label_rows:
            tables["photo_eval_metrics"] = evaluate_photo_quality(
                stages.photo_evidence,
                photo_label_table,
            )
            if run_image_semantics:
                tables["photo_damage_eval_metrics"] = evaluate_photo_damage(
                    stages.photo_damage_evidence,
                    photo_label_table,
                )
    return tables


def validate_pipeline_outputs(
    tables: dict[str, pa.Table],
    inputs: PipelineInputs,
    config: RunConfig,
    *,
    run_image_semantics: bool,
) -> ValidationReport:
    output_validation = validate_outputs(
        tables,
        expected_claim_count=len(inputs.claim_rows),
        expected_file_count=len(inputs.manifest_rows),
        expected_photo_count=inputs.photo_input_table.num_rows,
        expected_document_count=inputs.document_input_table.num_rows,
        semantic_required=run_image_semantics,
        expected_semantic_photo_count=sum(
            1 for row in tables["photo_evidence"].to_pylist() if row["decode_ok"]
        ),
    )
    output_validation.raise_if_failed(fail_on_warnings=config.fail_on_warnings)
    return output_validation


def write_pipeline_outputs(
    tables: dict[str, pa.Table],
    metadata: dict[str, Any],
    validation_report: dict[str, Any],
    config: RunConfig,
) -> None:
    clear_stale_table_outputs(config.output_dir)
    for name, table in tables.items():
        write_table_outputs(
            name,
            table,
            config.output_dir,
            write_parquet=config.write_parquet,
        )

    atomic_write_text(
        config.output_dir / "run_metadata.json",
        json.dumps(metadata, indent=2, ensure_ascii=False),
    )
    atomic_write_text(
        config.output_dir / "validation_report.json",
        json.dumps(validation_report, indent=2, ensure_ascii=False),
    )


def build_review_tasks(
    claim_rows: list[dict[str, Any]],
    claim_file_table: pa.Table,
    photo_table: pa.Table,
    document_table: pa.Table,
    photo_damage_table: pa.Table | None = None,
    photo_model_run_table: pa.Table | None = None,
) -> pa.Table:
    rows: list[dict[str, Any]] = []
    photos = photo_table.to_pylist()

    for photo in photos:
        if not photo["needs_review"]:
            continue
        if not photo["decode_ok"]:
            rows.append(
                {
                    "claim_id": photo["claim_id"],
                    "task_id": f"task:photo_decode_review:{photo['file_id']}",
                    "task_type": "photo_decode_review",
                    "priority": "high",
                    "source_file_id": photo["file_id"],
                    "evidence_node_id": photo["evidence_node_id"],
                    "reason": f"photo decode failed: {photo['decode_error']}",
                    "suggested_action": "Request a usable replacement damage photo.",
                    "status": "open",
                }
            )
            continue
        flags = json.loads(photo["issue_flags_json"])
        rows.append(
            {
                "claim_id": photo["claim_id"],
                "task_id": f"task:photo_quality_review:{photo['file_id']}",
                "task_type": "photo_quality_review",
                "priority": "high"
                if float(photo["quality_score"]) < 0.4
                else "medium",
                "source_file_id": photo["file_id"],
                "evidence_node_id": photo["evidence_node_id"],
                "reason": (
                    f"photo quality score {float(photo['quality_score']):.2f}; "
                    f"flags={flags}"
                ),
                "suggested_action": "Review whether the uploaded damage photo is usable.",
                "status": "open",
            }
        )

    seen_hashes: dict[tuple[str, str], str] = {}
    for photo in photos:
        if not photo["decode_ok"] or not photo["perceptual_hash"]:
            continue
        key = (photo["claim_id"], photo["perceptual_hash"])
        first_file_id = seen_hashes.get(key)
        if first_file_id is None:
            seen_hashes[key] = photo["file_id"]
            continue
        rows.append(
            {
                "claim_id": photo["claim_id"],
                "task_id": f"task:duplicate_photo_review:{photo['file_id']}",
                "task_type": "duplicate_photo_review",
                "priority": "medium",
                "source_file_id": photo["file_id"],
                "evidence_node_id": photo["evidence_node_id"],
                "reason": (
                    "photo perceptual hash matches "
                    f"{first_file_id}: {photo['perceptual_hash']}"
                ),
                "suggested_action": "Confirm whether duplicate photos should be retained.",
                "status": "open",
            }
        )

    model_runs = (
        photo_model_run_table
        if photo_model_run_table is not None
        else PHOTO_MODEL_RUNS.arrow_table([])
    )
    photo_evidence_node_by_file = {
        photo["file_id"]: photo["evidence_node_id"] for photo in photos
    }
    photo_damages = (
        photo_damage_table
        if photo_damage_table is not None
        else PHOTO_DAMAGE_EVIDENCE.arrow_table([])
    )
    damage_evidence_node_by_file = {
        damage["file_id"]: damage["evidence_node_id"]
        for damage in photo_damages.to_pylist()
    }
    failed_model_file_ids: set[str] = set()
    for run in model_runs.to_pylist():
        if run["status"] == "success":
            continue
        failed_model_file_ids.add(run["file_id"])
        evidence_node_id = damage_evidence_node_by_file.get(
            run["file_id"],
            photo_evidence_node_by_file.get(run["file_id"]),
        )
        rows.append(
            {
                "claim_id": run["claim_id"],
                "task_id": f"task:model_output_review:{run['run_id']}",
                "task_type": "model_output_review",
                "priority": "high",
                "source_file_id": run["file_id"],
                "evidence_node_id": evidence_node_id,
                "reason": (
                    f"image model status={run['status']}; "
                    f"error={run['error_code']}: {run['error_message']}"
                ),
                "suggested_action": "Review the image model output and retry or correct manually.",
                "status": "open",
            }
        )

    for damage in photo_damages.to_pylist():
        if not damage["needs_review"]:
            continue
        if damage["file_id"] in failed_model_file_ids:
            continue
        reasons = []
        if not damage["vehicle_visible"]:
            reasons.append("vehicle not visible")
        if not damage["target_vehicle_clear"]:
            reasons.append("target vehicle unclear")
        if float(damage["confidence"]) < 0.65:
            reasons.append(f"low confidence {float(damage['confidence']):.2f}")
        uncertainty = json.loads(damage["uncertainty_reasons_json"])
        if uncertainty:
            reasons.append("uncertainty=" + ",".join(str(item) for item in uncertainty))
        rows.append(
            {
                "claim_id": damage["claim_id"],
                "task_id": f"task:photo_damage_review:{damage['file_id']}",
                "task_type": "photo_damage_review",
                "priority": "high"
                if not damage["vehicle_visible"] or float(damage["confidence"]) < 0.4
                else "medium",
                "source_file_id": damage["file_id"],
                "evidence_node_id": damage["evidence_node_id"],
                "reason": "; ".join(reasons) or "photo damage model evidence needs review",
                "suggested_action": "Confirm the model-generated vehicle damage evidence.",
                "status": "open",
            }
        )

    for doc in document_table.to_pylist():
        if not doc["needs_review"]:
            continue
        reasons = []
        if not str(doc["field_value"] or "").strip():
            reasons.append("empty field value")
        if not valid_bbox(json.loads(doc["bbox_json"])):
            reasons.append("invalid bbox")
        rows.append(
            {
                "claim_id": doc["claim_id"],
                "task_id": f"task:document_field_review:{doc['evidence_node_id']}",
                "task_type": "document_field_review",
                "priority": "medium",
                "source_file_id": doc["file_id"],
                "evidence_node_id": doc["evidence_node_id"],
                "reason": "; ".join(reasons) or "document field needs review",
                "suggested_action": "Confirm or correct the extracted document span.",
                "status": "open",
            }
        )

    by_claim_media: dict[str, Counter[str]] = {
        str(claim["claim_id"]): Counter() for claim in claim_rows
    }
    for file_row in claim_file_table.to_pylist():
        media_type = file_row["media_type"]
        if media_type == "image/jpeg":
            by_claim_media[file_row["claim_id"]]["photo"] += 1
        elif media_type == "image/png":
            by_claim_media[file_row["claim_id"]]["document"] += 1

    for claim_id, counts in sorted(by_claim_media.items()):
        missing = []
        if counts["photo"] == 0:
            missing.append("photo")
        if counts["document"] == 0:
            missing.append("document")
        if not missing:
            continue
        rows.append(
            {
                "claim_id": claim_id,
                "task_id": f"task:missing_material_review:{claim_id}",
                "task_type": "missing_material_review",
                "priority": "high",
                "source_file_id": None,
                "evidence_node_id": f"evidence:claim:{claim_id}",
                "reason": "missing required material: " + ", ".join(missing),
                "suggested_action": "Request the missing claim packet material.",
                "status": "open",
            }
        )

    return REVIEW_TASKS.arrow_table(rows)


def build_claim_summary(conn: Any, claim_rows: list[dict[str, Any]]) -> pa.Table:
    rows = conn.sql(
        """
        with file_counts as (
            select
                claim_id,
                count(*)::bigint as file_count,
                sum(case when media_type = 'image/jpeg' then 1 else 0 end)::bigint
                    as photo_count,
                sum(case when media_type = 'image/png' then 1 else 0 end)::bigint
                    as document_count
            from claim_files
            group by claim_id
        ),
        review_counts as (
            select
                claim_id,
                count(*)::bigint as review_task_count,
                sum(case
                    when task_type in (
                        'photo_quality_review',
                        'photo_decode_review',
                        'duplicate_photo_review',
                        'photo_damage_review',
                        'model_output_review'
                    ) then 1 else 0 end)::bigint
                    as photo_review_count,
                sum(case when task_type = 'document_field_review' then 1 else 0 end)::bigint
                    as document_review_count
            from review_tasks
            group by claim_id
        )
        select
            file_counts.claim_id,
            file_counts.file_count,
            file_counts.photo_count,
            file_counts.document_count,
            coalesce(review_counts.review_task_count, 0)::bigint as review_task_count,
            coalesce(review_counts.photo_review_count, 0)::bigint as photo_review_count,
            coalesce(review_counts.document_review_count, 0)::bigint
                as document_review_count,
            case
                when file_counts.photo_count = 0
                    or file_counts.document_count = 0
                    then 'missing_required_materials'
                when coalesce(review_counts.review_task_count, 0) > 0
                    then 'needs_review'
                else 'complete'
            end as claim_packet_status
        from file_counts
        left join review_counts using (claim_id)
        order by file_counts.claim_id
        """
    ).to_arrow_table().to_pylist()
    summary_by_claim = {row["claim_id"]: row for row in rows}
    review_by_claim = {
        row["claim_id"]: row
        for row in conn.sql(
            """
            select
                claim_id,
                count(*)::bigint as review_task_count,
                sum(case
                    when task_type in (
                        'photo_quality_review',
                        'photo_decode_review',
                        'duplicate_photo_review',
                        'photo_damage_review',
                        'model_output_review'
                    ) then 1 else 0 end)::bigint as photo_review_count,
                sum(case when task_type = 'document_field_review' then 1 else 0 end)::bigint
                    as document_review_count
            from review_tasks
            group by claim_id
            """
        ).to_arrow_table().to_pylist()
    }
    for claim in claim_rows:
        claim_id = str(claim["claim_id"])
        if claim_id in summary_by_claim:
            continue
        review_counts = review_by_claim.get(claim_id, {})
        summary_by_claim[claim_id] = {
            "claim_id": claim_id,
            "file_count": 0,
            "photo_count": 0,
            "document_count": 0,
            "review_task_count": int(review_counts.get("review_task_count") or 0),
            "photo_review_count": int(review_counts.get("photo_review_count") or 0),
            "document_review_count": int(
                review_counts.get("document_review_count") or 0
            ),
            "claim_packet_status": "missing_required_materials",
        }
    return CLAIM_SUMMARY.arrow_table(
        [summary_by_claim[claim_id] for claim_id in sorted(summary_by_claim)]
    )


def build_evidence_nodes(
    claim_rows: list[dict[str, Any]],
    claim_file_table: pa.Table,
    photo_table: pa.Table,
    document_table: pa.Table,
    review_task_table: pa.Table,
    photo_damage_table: pa.Table | None = None,
) -> pa.Table:
    rows: list[dict[str, Any]] = []

    for claim in claim_rows:
        claim_id = claim["claim_id"]
        rows.append(
            {
                "claim_id": claim_id,
                "node_id": f"evidence:claim:{claim_id}",
                "node_type": "claim",
                "source_file_id": None,
                "source_path": None,
                "payload_json": stable_json(claim),
                "created_by": "manifest",
                "requires_human_confirmation": False,
            }
        )

    for file_row in claim_file_table.to_pylist():
        rows.append(
            {
                "claim_id": file_row["claim_id"],
                "node_id": f"evidence:file:{file_row['file_id']}",
                "node_type": "file",
                "source_file_id": file_row["file_id"],
                "source_path": file_row["absolute_path"],
                "payload_json": stable_json(
                    {
                        "role": file_row["role"],
                        "media_type": file_row["media_type"],
                        "source_dataset": file_row["source_dataset"],
                        "source_url": file_row["source_url"],
                        "sha256": file_row["sha256"],
                    }
                ),
                "created_by": "manifest",
                "requires_human_confirmation": not bool(file_row["file_exists"]),
            }
        )

    for photo in photo_table.to_pylist():
        rows.append(
            {
                "claim_id": photo["claim_id"],
                "node_id": photo["evidence_node_id"],
                "node_type": "photo_quality",
                "source_file_id": photo["file_id"],
                "source_path": photo["source_path"],
                "payload_json": stable_json(
                    {
                        "decode_ok": photo["decode_ok"],
                        "decode_error": photo["decode_error"],
                        "image_format": photo["image_format"],
                        "image_mode": photo["image_mode"],
                        "image_width": photo["image_width"],
                        "image_height": photo["image_height"],
                        "aspect_ratio": photo["aspect_ratio"],
                        "megapixels": photo["megapixels"],
                        "brightness_mean": photo["brightness_mean"],
                        "brightness_std": photo["brightness_std"],
                        "blur_score": photo["blur_score"],
                        "quality_score": photo["quality_score"],
                        "issue_flags": json.loads(photo["issue_flags_json"]),
                        "perceptual_hash": photo["perceptual_hash"],
                        "quality_rule_version": photo["quality_rule_version"],
                    }
                ),
                "created_by": f"rule:{photo['quality_rule_version']}",
                "requires_human_confirmation": bool(photo["needs_review"]),
            }
        )

    for doc in document_table.to_pylist():
        rows.append(
            {
                "claim_id": doc["claim_id"],
                "node_id": doc["evidence_node_id"],
                "node_type": "document_span",
                "source_file_id": doc["file_id"],
                "source_path": doc["source_image_path"],
                "payload_json": stable_json(
                    {
                        "document_id": doc["document_id"],
                        "field_name": doc["field_name"],
                        "field_value": doc["field_value"],
                        "field_type": doc["field_type"],
                        "bbox": json.loads(doc["bbox_json"]),
                        "confidence": doc["confidence"],
                        "annotation_path": doc["annotation_path"],
                    }
                ),
                "created_by": "gold_annotation:funsd",
                "requires_human_confirmation": bool(doc["needs_review"]),
            }
        )

    photo_damages = (
        photo_damage_table
        if photo_damage_table is not None
        else PHOTO_DAMAGE_EVIDENCE.arrow_table([])
    )
    for damage in photo_damages.to_pylist():
        rows.append(
            {
                "claim_id": damage["claim_id"],
                "node_id": damage["evidence_node_id"],
                "node_type": "photo_damage",
                "source_file_id": damage["file_id"],
                "source_path": damage["source_path"],
                "payload_json": stable_json(
                    {
                        "vehicle_visible": damage["vehicle_visible"],
                        "target_vehicle_clear": damage["target_vehicle_clear"],
                        "damage_visible": damage["damage_visible"],
                        "damaged_parts": json.loads(damage["damaged_parts_json"]),
                        "damage_types": json.loads(damage["damage_types_json"]),
                        "severity_hint": damage["severity_hint"],
                        "evidence_description": damage["evidence_description"],
                        "uncertainty_reasons": json.loads(
                            damage["uncertainty_reasons_json"]
                        ),
                        "confidence": damage["confidence"],
                        "model_run_id": damage["model_run_id"],
                        "raw_response_ref": damage["raw_response_ref"],
                    }
                ),
                "created_by": (
                    f"model:{damage['model_provider']}:{damage['model_name']}:"
                    f"{damage['prompt_version']}"
                ),
                "requires_human_confirmation": True,
            }
        )

    for task in review_task_table.to_pylist():
        rows.append(
            {
                "claim_id": task["claim_id"],
                "node_id": f"evidence:review_task:{task['task_id']}",
                "node_type": "review_task",
                "source_file_id": task["source_file_id"],
                "source_path": None,
                "payload_json": stable_json(task),
                "created_by": "rule:review_task_v1",
                "requires_human_confirmation": True,
            }
        )

    return EVIDENCE_NODES.arrow_table(rows)


def write_table_outputs(
    name: str,
    table: pa.Table,
    output_dir: Path,
    *,
    write_parquet: bool,
) -> None:
    atomic_write_jsonl(table, output_dir / f"{name}.jsonl")
    if write_parquet:
        atomic_write_parquet(
            table,
            output_dir / "parquet" / name / "part-00000.parquet",
        )


def build_metadata(
    *,
    config: RunConfig,
    input_validation: ValidationReport,
    output_validation: ValidationReport,
    tables: dict[str, pa.Table],
) -> dict[str, Any]:
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": config.mode,
        "profile": config.profile,
        "image_semantics_required": config.requires_image_semantics,
        "data_root": str(config.data_root),
        "workspace_root": str(config.workspace_root),
        "output_dir": str(config.output_dir),
        "photo_labels_path": str(config.photo_labels_path)
        if config.photo_labels_path
        else None,
        "execution_backend": config.execution_backend,
        "runner": config.runner,
        "image_model": {
            "provider": config.image_model_provider,
            "model": config.image_model if config.requires_image_semantics else None,
            "base_url": config.image_model_base_url
            if config.requires_image_semantics
            else None,
            "model_version": config.image_model_version
            if config.requires_image_semantics
            else None,
            "prompt_version": config.prompt_version
            if config.requires_image_semantics
            else None,
            "response_schema_version": config.response_schema_version
            if config.requires_image_semantics
            else None,
            "max_image_model_errors": config.max_image_model_errors,
            "strict_error_budget": config.enforces_image_model_error_budget,
        },
        "table_counts": {
            name: table.num_rows for name, table in sorted(tables.items())
        },
        "input_validation": input_validation.to_dict(),
        "output_validation": output_validation.to_dict(),
        "limitations": [
            "Proxy public data, not real insurance claim data.",
            "CarDD local sample contains images only; no local damage annotations.",
            "FUNSD annotations are form-understanding labels, not insurance fields.",
            "Baseline profile uses deterministic rules and gold/proxy annotations only.",
        ],
    }


def print_preview(title: str, rel: Any, sql: str) -> None:
    print(f"\n{title}")
    rel.query("r", sql).show(max_width=160)


def run_pipeline(config: RunConfig, *, print_summary: bool = True) -> PipelineResult:
    validate_run_config(config)
    run_image_semantics = config.requires_image_semantics
    if run_image_semantics:
        check_image_model_service(config)

    inputs = load_pipeline_inputs(config)

    if config.runner:
        vane.configure(runner=config.runner)

    conn = vane.connect()
    stages = run_evidence_stages(
        conn,
        inputs,
        config,
        run_image_semantics=run_image_semantics,
    )
    tables = assemble_output_tables(
        stages,
        inputs,
        config,
        run_image_semantics=run_image_semantics,
    )
    output_validation = validate_pipeline_outputs(
        tables,
        inputs,
        config,
        run_image_semantics=run_image_semantics,
    )

    metadata = build_metadata(
        config=config,
        input_validation=inputs.input_validation,
        output_validation=output_validation,
        tables=tables,
    )
    validation_report = {
        "input": inputs.input_validation.to_dict(),
        "output": output_validation.to_dict(),
    }
    write_pipeline_outputs(
        tables,
        metadata,
        validation_report,
        config,
    )

    if print_summary:
        print("\nClaims evidence graph POC complete")
        print(f"Output directory: {config.output_dir}")
        for name, table in tables.items():
            print(f"{name}: {table.num_rows}")

        print_preview(
            "Claim summary",
            conn.from_arrow(stages.claim_summary),
            """
            select
                claim_id,
                file_count,
                photo_count,
                document_count,
                review_task_count,
                photo_review_count,
                document_review_count,
                claim_packet_status
            from r
            order by claim_id
            """,
        )
        print_preview(
            "Review tasks",
            conn.from_arrow(stages.review_tasks),
            """
            select
                claim_id,
                task_type,
                priority,
                source_file_id,
                reason
            from r
            order by claim_id, task_type, source_file_id
            """,
        )
        print_preview(
            "Photo quality range",
            conn.from_arrow(stages.photo_evidence),
            """
            select
                min(quality_score) as min_quality_score,
                max(quality_score) as max_quality_score,
                sum(case when needs_review then 1 else 0 end)
                    as photos_needing_review
            from r
            """,
        )

    return PipelineResult(
        output_dir=config.output_dir,
        tables=tables,
        validation=output_validation,
        metadata=metadata,
    )
