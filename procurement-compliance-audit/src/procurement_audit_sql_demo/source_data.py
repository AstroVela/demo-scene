"""Validate PostgreSQL source rows and expose typed pipeline inputs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import json
import math
import re
from typing import Any, Mapping, Sequence

import pyarrow as pa


PROJECT_COLUMNS = (
    "project_id",
    "title",
    "original_winner_supplier_id",
    "score_bias_threshold",
    "ai_min_confidence",
)
SUPPLIER_COLUMNS = (
    "project_id",
    "supplier_id",
    "supplier_name",
    "aliases_json",
)
SCORE_COLUMNS = (
    "project_id",
    "expert_id",
    "expert_name",
    "supplier_id",
    "score",
)
EVIDENCE_COLUMNS = (
    "project_id",
    "file_id",
    "role",
    "bucket",
    "object_key",
    "media_type",
)
_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
_EXPECTED_ROLES = {"expert_recommendation", "committee_minutes"}
_ROLE_ORDER = {"expert_recommendation": 0, "committee_minutes": 1}


class SourceContractError(ValueError):
    """Raised when PostgreSQL source rows violate the focused demo contract."""


@dataclass(frozen=True)
class SourceBundle:
    project: pa.Table
    suppliers: pa.Table
    scores: pa.Table
    evidence: pa.Table


def _columns(row: Mapping[str, Any], expected: tuple[str, ...], context: str) -> None:
    if set(row) != set(expected):
        raise SourceContractError(f"{context} has wrong columns")


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceContractError(f"{context} must be non-empty text")
    return value.strip()


def _identifier(value: Any, context: str) -> str:
    result = _text(value, context).upper()
    if not _IDENTIFIER.fullmatch(result):
        raise SourceContractError(f"{context} must be a stable uppercase identifier")
    return result


def _number(value: Any, context: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SourceContractError(f"{context} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise SourceContractError(f"{context} must be between {minimum} and {maximum}")
    return result


def _json_string_list(value: Any, context: str) -> tuple[list[str], str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise SourceContractError(f"{context} must be a JSON string list") from exc
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise SourceContractError(f"{context} must be a non-empty string list")
    normalized = [item.strip() for item in value]
    if len(set(normalized)) != len(normalized):
        raise SourceContractError(f"{context} must not contain duplicates")
    return normalized, json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _winner(rows: Sequence[Mapping[str, Any]]) -> str:
    values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        values[row["supplier_id"]].append(float(row["score"]))
    return min(
        values,
        key=lambda supplier_id: (
            -sum(values[supplier_id]) / len(values[supplier_id]),
            supplier_id,
        ),
    )


def source_bundle_from_rows(
    project_rows: Sequence[Mapping[str, Any]],
    supplier_rows: Sequence[Mapping[str, Any]],
    score_rows: Sequence[Mapping[str, Any]],
    evidence_rows: Sequence[Mapping[str, Any]],
    *,
    expected_bucket: str,
) -> SourceBundle:
    """Validate one PostgreSQL snapshot before any OCR or model work."""

    # Validate the single project and its decision thresholds.
    if len(project_rows) != 1:
        raise SourceContractError(
            f"PostgreSQL source must contain exactly one project, got {len(project_rows)}"
        )
    project_source = dict(project_rows[0])
    _columns(project_source, PROJECT_COLUMNS, "project row 0")
    project_id = _identifier(project_source["project_id"], "project_id")
    project = {
        "project_id": project_id,
        "title": _text(project_source["title"], "title"),
        "original_winner_supplier_id": _identifier(
            project_source["original_winner_supplier_id"],
            "original_winner_supplier_id",
        ),
        "score_bias_threshold": _number(
            project_source["score_bias_threshold"],
            "score_bias_threshold",
            0.0,
            100.0,
        ),
        "ai_min_confidence": _number(
            project_source["ai_min_confidence"],
            "ai_min_confidence",
            0.0,
            1.0,
        ),
    }

    # Normalize suppliers and require unique names and aliases.
    if len(supplier_rows) != 3:
        raise SourceContractError("PostgreSQL source must contain exactly three suppliers")
    suppliers: list[dict[str, Any]] = []
    supplier_ids: set[str] = set()
    label_owners: dict[str, str] = {}
    for index, source in enumerate(supplier_rows):
        row = dict(source)
        _columns(row, SUPPLIER_COLUMNS, f"supplier row {index}")
        if _identifier(row["project_id"], f"supplier row {index}.project_id") != project_id:
            raise SourceContractError(f"supplier row {index} belongs to another project")
        supplier_id = _identifier(row["supplier_id"], f"supplier row {index}.supplier_id")
        if supplier_id in supplier_ids:
            raise SourceContractError(f"duplicate supplier {supplier_id}")
        supplier_name = _text(row["supplier_name"], f"supplier row {index}.supplier_name")
        aliases, aliases_json = _json_string_list(
            row["aliases_json"],
            f"supplier row {index}.aliases_json",
        )
        for label in [supplier_name, *aliases]:
            key = re.sub(r"\s+", "", label).casefold()
            if key in label_owners:
                raise SourceContractError(
                    f"supplier label {label!r} is shared by {label_owners[key]} and {supplier_id}"
                )
            label_owners[key] = supplier_id
        supplier_ids.add(supplier_id)
        suppliers.append(
            {
                "project_id": project_id,
                "supplier_id": supplier_id,
                "supplier_name": supplier_name,
                "aliases_json": aliases_json,
            }
        )
    if project["original_winner_supplier_id"] not in supplier_ids:
        raise SourceContractError("original_winner_supplier_id references an unknown supplier")

    # Require a complete expert-by-supplier matrix with a reproducible winner.
    if len(score_rows) != 12:
        raise SourceContractError("PostgreSQL source must contain exactly twelve scores")
    scores: list[dict[str, Any]] = []
    seen_scores: set[tuple[str, str]] = set()
    expert_names: dict[str, str] = {}
    expert_suppliers: dict[str, set[str]] = defaultdict(set)
    for index, source in enumerate(score_rows):
        row = dict(source)
        _columns(row, SCORE_COLUMNS, f"score row {index}")
        if _identifier(row["project_id"], f"score row {index}.project_id") != project_id:
            raise SourceContractError(f"score row {index} belongs to another project")
        expert_id = _identifier(row["expert_id"], f"score row {index}.expert_id")
        expert_name = _text(row["expert_name"], f"score row {index}.expert_name")
        supplier_id = _identifier(row["supplier_id"], f"score row {index}.supplier_id")
        if supplier_id not in supplier_ids:
            raise SourceContractError(f"score row {index} references an unknown supplier")
        key = (expert_id, supplier_id)
        if key in seen_scores:
            raise SourceContractError(f"duplicate score key: {expert_id}/{supplier_id}")
        if expert_id in expert_names and expert_names[expert_id] != expert_name:
            raise SourceContractError(f"expert {expert_id} has inconsistent names")
        seen_scores.add(key)
        expert_names[expert_id] = expert_name
        expert_suppliers[expert_id].add(supplier_id)
        scores.append(
            {
                "project_id": project_id,
                "expert_id": expert_id,
                "expert_name": expert_name,
                "supplier_id": supplier_id,
                "score": _number(row["score"], f"score row {index}.score", 0.0, 100.0),
            }
        )
    if len(expert_names) != 4 or any(values != supplier_ids for values in expert_suppliers.values()):
        raise SourceContractError("scores must form a complete four-expert matrix")
    if _winner(scores) != project["original_winner_supplier_id"]:
        raise SourceContractError("declared original winner does not match score matrix")

    # Validate the two trusted MinIO locators before OCR can read either object.
    if len(evidence_rows) != 2:
        raise SourceContractError("PostgreSQL source must contain exactly two evidence rows")
    evidence: list[dict[str, Any]] = []
    seen_files: set[str] = set()
    seen_roles: set[str] = set()
    for index, source in enumerate(evidence_rows):
        row = dict(source)
        _columns(row, EVIDENCE_COLUMNS, f"evidence row {index}")
        if _identifier(row["project_id"], f"evidence row {index}.project_id") != project_id:
            raise SourceContractError(f"evidence row {index} belongs to another project")
        file_id = _identifier(row["file_id"], f"evidence row {index}.file_id")
        role = _text(row["role"], f"evidence row {index}.role")
        bucket = _text(row["bucket"], f"evidence row {index}.bucket")
        object_key = _text(row["object_key"], f"evidence row {index}.object_key")
        media_type = _text(row["media_type"], f"evidence row {index}.media_type")
        if bucket != expected_bucket:
            raise SourceContractError(
                f"evidence row {index} bucket must match runtime MinIO bucket"
            )
        parts = object_key.split("/")
        if object_key.startswith("/") or any(part in {"", ".", ".."} for part in parts):
            raise SourceContractError(f"evidence row {index}.object_key is invalid")
        if media_type != "image/png" or not object_key.lower().endswith(".png"):
            raise SourceContractError(f"evidence row {index} must locate an image/png")
        if file_id in seen_files or role in seen_roles:
            raise SourceContractError("evidence file IDs and roles must be unique")
        seen_files.add(file_id)
        seen_roles.add(role)
        evidence.append(
            {
                "project_id": project_id,
                "file_id": file_id,
                "role": role,
                "bucket": bucket,
                "object_key": object_key,
                "media_type": media_type,
            }
        )
    if seen_roles != _EXPECTED_ROLES:
        raise SourceContractError(f"evidence roles must be {sorted(_EXPECTED_ROLES)}")
    evidence.sort(key=lambda row: (_ROLE_ORDER[row["role"]], row["file_id"]))

    return SourceBundle(
        project=pa.Table.from_pylist([project]),
        suppliers=pa.Table.from_pylist(suppliers),
        scores=pa.Table.from_pylist(scores),
        evidence=pa.Table.from_pylist(evidence),
    )
