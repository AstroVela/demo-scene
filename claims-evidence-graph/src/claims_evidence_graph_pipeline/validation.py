"""Input and output validation for the claims evidence graph POC."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa

from claims_evidence_graph_pipeline.contracts import (
    CLAIM_FILES_REQUIRED_FIELDS,
    CLAIMS_REQUIRED_FIELDS,
    ContractError,
    OUTPUT_TABLES,
    PHOTO_HUMAN_LABELS_REQUIRED_FIELDS,
    SUPPORTED_MEDIA_TYPES,
)

PHOTO_LABEL_BOOLEAN_FIELDS = {
    "usable_for_review",
    "vehicle_visible",
    "target_vehicle_clear",
    "damage_visible",
    "needs_reshoot",
}


@dataclass
class ValidationReport:
    """Structured validation result persisted with each run."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def extend(self, other: "ValidationReport") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.facts.update(other.facts)

    def raise_if_failed(self, *, fail_on_warnings: bool = False) -> None:
        if self.errors:
            raise ContractError("Validation failed: " + "; ".join(self.errors))
        if fail_on_warnings and self.warnings:
            raise ContractError("Validation warnings: " + "; ".join(self.warnings))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "facts": self.facts,
        }


def _missing_fields(row: dict[str, Any], required: set[str]) -> list[str]:
    return sorted(name for name in required if name not in row)


def _duplicates(values: list[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def _is_relative_path(value: Any) -> bool:
    try:
        return not Path(str(value)).is_absolute()
    except TypeError:
        return False


def _non_empty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _validate_json_list_field(
    report: ValidationReport,
    *,
    row_name: str,
    row: dict[str, Any],
    field: str,
) -> None:
    try:
        value = _json_value(row.get(field))
    except (TypeError, json.JSONDecodeError) as exc:
        report.add_error(f"{row_name} {field} must be valid JSON: {exc}")
        return
    if not isinstance(value, list):
        report.add_error(f"{row_name} {field} must be a JSON list")
        return
    if not all(isinstance(item, str) for item in value):
        report.add_error(f"{row_name} {field} must contain only strings")


def validate_manifests(
    claim_rows: list[dict[str, Any]],
    file_rows: list[dict[str, Any]],
    workspace_root: Path,
) -> ValidationReport:
    report = ValidationReport()
    report.facts["claim_manifest_rows"] = len(claim_rows)
    report.facts["file_manifest_rows"] = len(file_rows)

    if not claim_rows:
        report.add_error("claims.jsonl has no rows")
    if not file_rows:
        report.add_error("claim_files.jsonl has no rows")

    for index, row in enumerate(claim_rows, start=1):
        missing = _missing_fields(row, CLAIMS_REQUIRED_FIELDS)
        if missing:
            report.add_error(f"claims row {index} missing fields: {missing}")

    for index, row in enumerate(file_rows, start=1):
        missing = _missing_fields(row, CLAIM_FILES_REQUIRED_FIELDS)
        if missing:
            report.add_error(f"claim_files row {index} missing fields: {missing}")

    claim_ids = [str(row.get("claim_id")) for row in claim_rows if row.get("claim_id")]
    file_claim_ids = [
        str(row.get("claim_id")) for row in file_rows if row.get("claim_id")
    ]
    file_ids = [str(row.get("file_id")) for row in file_rows if row.get("file_id")]

    duplicate_claims = _duplicates(claim_ids)
    if duplicate_claims:
        report.add_error(f"duplicate claim_id values: {duplicate_claims}")

    duplicate_files = _duplicates(file_ids)
    if duplicate_files:
        report.add_error(f"duplicate file_id values: {duplicate_files}")

    unknown_claims = sorted(set(file_claim_ids) - set(claim_ids))
    if unknown_claims:
        report.add_error(f"claim_files references unknown claims: {unknown_claims}")

    by_media = Counter(str(row.get("media_type")) for row in file_rows)
    report.facts["media_type_counts"] = dict(sorted(by_media.items()))
    unsupported = sorted(set(by_media) - SUPPORTED_MEDIA_TYPES)
    if unsupported:
        report.add_error(f"unsupported media_type values: {unsupported}")

    for index, row in enumerate(file_rows, start=1):
        raw_path = _non_empty_string(row.get("raw_path"))
        poc_path = _non_empty_string(row.get("poc_path"))
        if raw_path is None:
            report.add_error(f"claim_files row {index} raw_path must be non-empty")
        elif not _is_relative_path(raw_path):
            report.add_error(f"claim_files row {index} raw_path must be relative")
        else:
            path = workspace_root / raw_path
            if not path.exists():
                report.add_error(
                    f"claim_files row {index} missing raw_path file: {path}"
                )
        if poc_path is None:
            report.add_error(f"claim_files row {index} poc_path must be non-empty")
        elif not _is_relative_path(poc_path):
            report.add_error(f"claim_files row {index} poc_path must be relative")
        else:
            path = workspace_root / poc_path
            if not path.exists():
                report.add_error(
                    f"claim_files row {index} missing poc_path file: {path}"
                )

        if row.get("media_type") == "image/png":
            annotation = _non_empty_string(row.get("annotation_raw_path"))
            if not annotation:
                report.add_error(
                    f"document row {index} missing annotation_raw_path"
                )
            else:
                if not _is_relative_path(annotation):
                    report.add_error(
                        f"document row {index} annotation_raw_path must be relative"
                    )
                else:
                    annotation_path = workspace_root / annotation
                    if not annotation_path.exists():
                        report.add_error(
                            f"document row {index} missing annotation file: "
                            f"{annotation_path}"
                        )

    if by_media.get("image/jpeg", 0) == 0:
        report.add_warning("no photo files found")
    if by_media.get("image/png", 0) == 0:
        report.add_warning("no document image files found")

    return report


def validate_label_inputs(
    *,
    photo_label_rows: list[dict[str, Any]],
    file_rows: list[dict[str, Any]],
) -> ValidationReport:
    report = ValidationReport()
    report.facts["photo_label_rows"] = len(photo_label_rows)

    photo_file_keys = {
        (str(row.get("claim_id")), str(row.get("file_id")))
        for row in file_rows
        if row.get("media_type") == "image/jpeg"
    }

    photo_label_keys: list[str] = []
    photo_label_key_set: set[tuple[str, str]] = set()
    for index, row in enumerate(photo_label_rows, start=1):
        row_name = f"photo label row {index}"
        missing = _missing_fields(row, PHOTO_HUMAN_LABELS_REQUIRED_FIELDS)
        if missing:
            report.add_error(f"{row_name} missing fields: {missing}")
            continue

        key = (str(row["claim_id"]), str(row["file_id"]))
        photo_label_key_set.add(key)
        photo_label_keys.append(f"{key[0]}\0{key[1]}")
        if key not in photo_file_keys:
            report.add_error(f"{row_name} references unknown photo file: {key}")

        for field in sorted(PHOTO_LABEL_BOOLEAN_FIELDS):
            if not isinstance(row.get(field), bool):
                report.add_error(f"{row_name} {field} must be boolean")

        _validate_json_list_field(
            report,
            row_name=row_name,
            row=row,
            field="damaged_parts_json",
        )
        _validate_json_list_field(
            report,
            row_name=row_name,
            row=row,
            field="damage_types_json",
        )

        for field in ("severity_label", "labeler_id", "labeled_at", "adjudication_status"):
            if not str(row.get(field) or "").strip():
                report.add_error(f"{row_name} {field} must be non-empty")

    duplicate_photo_labels = _duplicates(photo_label_keys)
    if duplicate_photo_labels:
        decoded = [tuple(value.split("\0", 1)) for value in duplicate_photo_labels]
        report.add_error(f"duplicate photo label keys: {decoded}")

    return report


def validate_table_schema(name: str, table: pa.Table) -> ValidationReport:
    report = ValidationReport()
    contract = OUTPUT_TABLES[name]
    expected = contract.column_names
    actual = table.column_names
    if actual != expected:
        report.add_error(f"{name} columns mismatch: expected {expected}, got {actual}")
    for column, data_type in contract.fields.items():
        if column not in table.column_names:
            continue
        actual_type = table.schema.field(column).type
        if actual_type != data_type:
            report.add_error(
                f"{name}.{column} type mismatch: expected {data_type}, "
                f"got {actual_type}"
            )
    return report


def validate_outputs(
    tables: dict[str, pa.Table],
    *,
    expected_claim_count: int,
    expected_file_count: int,
    expected_photo_count: int,
    expected_document_count: int,
    semantic_required: bool = False,
    expected_semantic_photo_count: int = 0,
) -> ValidationReport:
    report = ValidationReport()
    report.facts["table_counts"] = {
        name: table.num_rows for name, table in sorted(tables.items())
    }
    report.facts["semantic_required"] = semantic_required
    report.facts["expected_semantic_photo_count"] = expected_semantic_photo_count

    for name, table in tables.items():
        report.extend(validate_table_schema(name, table))

    if tables["claim_files"].num_rows != expected_file_count:
        report.add_error(
            "claim_files row count mismatch: "
            f"expected {expected_file_count}, got {tables['claim_files'].num_rows}"
        )
    if tables["photo_evidence"].num_rows != expected_photo_count:
        report.add_error(
            "photo_evidence row count mismatch: "
            f"expected {expected_photo_count}, got {tables['photo_evidence'].num_rows}"
        )
    if tables["claim_summary"].num_rows != expected_claim_count:
        report.add_error(
            "claim_summary row count mismatch: "
            f"expected {expected_claim_count}, got {tables['claim_summary'].num_rows}"
        )
    if expected_document_count and tables["document_evidence"].num_rows == 0:
        report.add_error("document_evidence is empty despite document inputs")

    if semantic_required:
        damage_table = tables.get("photo_damage_evidence")
        model_run_table = tables.get("photo_model_runs")
        if damage_table is None:
            report.add_error("photo_damage_evidence is required by semantic profile")
        if model_run_table is None:
            report.add_error("photo_model_runs is required by semantic profile")
        if (
            damage_table is not None
            and damage_table.num_rows != expected_semantic_photo_count
        ):
            report.add_error(
                "photo_damage_evidence row count mismatch: expected "
                f"{expected_semantic_photo_count}, got {damage_table.num_rows}"
            )
        if (
            model_run_table is not None
            and model_run_table.num_rows != expected_semantic_photo_count
        ):
            report.add_error(
                "photo_model_runs row count mismatch: expected "
                f"{expected_semantic_photo_count}, got {model_run_table.num_rows}"
            )
        if damage_table is not None and model_run_table is not None:
            damage_file_ids = sorted(
                str(row["file_id"]) for row in damage_table.to_pylist()
            )
            model_file_ids = sorted(
                str(row["file_id"]) for row in model_run_table.to_pylist()
            )
            if damage_file_ids != model_file_ids:
                report.add_error(
                    "semantic evidence/model run file_id mismatch: "
                    f"damage={damage_file_ids}, model_runs={model_file_ids}"
                )

    for name, table in tables.items():
        if name == "review_tasks":
            continue
        if table.num_rows == 0:
            report.add_warning(f"{name} is empty")

    return report
