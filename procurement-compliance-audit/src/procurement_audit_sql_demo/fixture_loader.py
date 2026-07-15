"""Load and validate the focused four-file procurement fixture."""

from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

import pyarrow as pa


EXPECTED_FIXTURE_FILES = frozenset(
    {
        "project.json",
        "expert_scores.csv",
        "expert_recommendation.png",
        "committee_minutes.png",
    }
)
_PROJECT_FIELDS = {
    "project_id",
    "title",
    "original_winner_supplier_id",
    "suppliers",
    "evidence_files",
    "thresholds",
}
_SUPPLIER_FIELDS = {"supplier_id", "name", "aliases"}
_EVIDENCE_FIELDS = {"file_id", "role", "local_path", "media_type"}
_THRESHOLD_FIELDS = {"score_bias_points", "ai_min_confidence"}
_SCORE_FIELDS = ("project_id", "expert_id", "expert_name", "supplier_id", "score")
_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")


class FixtureContractError(ValueError):
    """Raised before runtime or model work when fixture data is invalid."""


@dataclass(frozen=True)
class FixtureBundle:
    project: pa.Table
    suppliers: pa.Table
    scores: pa.Table
    evidence: pa.Table
    source_dir: Path


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FixtureContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_project(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant {item}")
            ),
        )
    except FixtureContractError:
        raise
    except (OSError, ValueError, UnicodeError) as exc:
        raise FixtureContractError(f"project.json must be valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise FixtureContractError("project.json must contain one object")
    if set(value) != _PROJECT_FIELDS:
        raise FixtureContractError("project.json has wrong fields")
    return value


def _non_empty_text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FixtureContractError(f"{path} must be non-empty text")
    return value.strip()


def _identifier(value: Any, path: str) -> str:
    result = _non_empty_text(value, path).upper()
    if not _IDENTIFIER.fullmatch(result):
        raise FixtureContractError(f"{path} must be a stable uppercase identifier")
    return result


def _number(value: Any, path: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FixtureContractError(f"{path} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise FixtureContractError(f"{path} must be between {minimum} and {maximum}")
    return result


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _supplier_label_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _load_suppliers(project: Mapping[str, Any], project_id: str) -> list[dict[str, Any]]:
    values = project.get("suppliers")
    if not isinstance(values, list) or len(values) != 3:
        raise FixtureContractError("suppliers must contain exactly three rows")
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    label_owners: dict[str, str] = {}
    for index, value in enumerate(values):
        path = f"suppliers[{index}]"
        if not isinstance(value, Mapping) or set(value) != _SUPPLIER_FIELDS:
            raise FixtureContractError(f"{path} has wrong fields")
        supplier_id = _identifier(value["supplier_id"], f"{path}.supplier_id")
        name = _non_empty_text(value["name"], f"{path}.name")
        aliases = value["aliases"]
        if (
            not isinstance(aliases, list)
            or not aliases
            or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
        ):
            raise FixtureContractError(f"{path}.aliases must be non-empty strings")
        normalized_aliases = [alias.strip() for alias in aliases]
        if supplier_id in seen_ids:
            raise FixtureContractError("supplier IDs must be unique")
        for label in [name, *normalized_aliases]:
            key = _supplier_label_key(label)
            existing_owner = label_owners.get(key)
            if existing_owner is not None:
                raise FixtureContractError(
                    f"supplier name or alias {label!r} is owned by both "
                    f"{existing_owner} and {supplier_id}"
                )
            label_owners[key] = supplier_id
        seen_ids.add(supplier_id)
        rows.append(
            {
                "project_id": project_id,
                "supplier_id": supplier_id,
                "supplier_name": name,
                "aliases_json": _stable_json(normalized_aliases),
            }
        )
    return rows


def _load_thresholds(project: Mapping[str, Any]) -> tuple[float, float]:
    value = project.get("thresholds")
    if not isinstance(value, Mapping) or set(value) != _THRESHOLD_FIELDS:
        raise FixtureContractError("thresholds has wrong fields")
    return (
        _number(value["score_bias_points"], "thresholds.score_bias_points", 0.0, 100.0),
        _number(value["ai_min_confidence"], "thresholds.ai_min_confidence", 0.0, 1.0),
    )


def _load_evidence(
    project: Mapping[str, Any],
    project_id: str,
    fixture_dir: Path,
) -> list[dict[str, Any]]:
    values = project.get("evidence_files")
    if not isinstance(values, list) or len(values) != 2:
        raise FixtureContractError("evidence_files must contain exactly two rows")
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_roles: set[str] = set()
    expected_roles = {"expert_recommendation", "committee_minutes"}
    for index, value in enumerate(values):
        path = f"evidence_files[{index}]"
        if not isinstance(value, Mapping) or set(value) != _EVIDENCE_FIELDS:
            raise FixtureContractError(f"{path} has wrong fields")
        file_id = _identifier(value["file_id"], f"{path}.file_id")
        role = _non_empty_text(value["role"], f"{path}.role")
        media_type = _non_empty_text(value["media_type"], f"{path}.media_type")
        local_value = _non_empty_text(value["local_path"], f"{path}.local_path")
        local_path = (fixture_dir / local_value).resolve()
        try:
            local_path.relative_to(fixture_dir)
        except ValueError as exc:
            raise FixtureContractError(f"{path}.local_path must stay inside the fixture") from exc
        if Path(local_value).is_absolute():
            raise FixtureContractError(f"{path}.local_path must stay inside the fixture")
        if not local_path.is_file():
            raise FixtureContractError(f"{path}.local_path does not exist: {local_value}")
        if media_type != "image/png" or local_path.suffix.lower() != ".png":
            raise FixtureContractError(f"{path} must point to an image/png")
        if file_id in seen_ids or role in seen_roles:
            raise FixtureContractError("evidence file IDs and roles must be unique")
        seen_ids.add(file_id)
        seen_roles.add(role)
        rows.append(
            {
                "project_id": project_id,
                "file_id": file_id,
                "role": role,
                "local_path": str(local_path),
                "media_type": media_type,
            }
        )
    if seen_roles != expected_roles:
        raise FixtureContractError(f"evidence roles must be {sorted(expected_roles)}")
    return rows


def _load_scores(
    path: Path,
    project_id: str,
    supplier_ids: set[str],
) -> list[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(_SCORE_FIELDS):
                raise FixtureContractError("expert_scores.csv has wrong columns")
            source_rows = list(reader)
    except OSError as exc:
        raise FixtureContractError(f"cannot read expert_scores.csv: {exc}") from exc

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    expert_names: dict[str, str] = {}
    expert_suppliers: dict[str, set[str]] = defaultdict(set)
    for index, value in enumerate(source_rows, start=2):
        row_project_id = _identifier(value["project_id"], f"score row {index}.project_id")
        if row_project_id != project_id:
            raise FixtureContractError(f"score row {index} belongs to another project")
        expert_id = _identifier(value["expert_id"], f"score row {index}.expert_id")
        expert_name = _non_empty_text(value["expert_name"], f"score row {index}.expert_name")
        supplier_id = _identifier(value["supplier_id"], f"score row {index}.supplier_id")
        if supplier_id not in supplier_ids:
            raise FixtureContractError(f"score row {index} references unknown supplier")
        try:
            score_value: Any = float(value["score"])
        except (TypeError, ValueError) as exc:
            raise FixtureContractError(f"score row {index}.score must be numeric") from exc
        score = _number(score_value, f"score row {index}.score", 0.0, 100.0)
        key = (expert_id, supplier_id)
        if key in seen:
            raise FixtureContractError(f"duplicate score key: {expert_id}/{supplier_id}")
        seen.add(key)
        if expert_id in expert_names and expert_names[expert_id] != expert_name:
            raise FixtureContractError(f"expert {expert_id} has inconsistent names")
        expert_names[expert_id] = expert_name
        expert_suppliers[expert_id].add(supplier_id)
        rows.append(
            {
                "project_id": project_id,
                "expert_id": expert_id,
                "expert_name": expert_name,
                "supplier_id": supplier_id,
                "score": score,
            }
        )
    if len(expert_names) != 4 or len(rows) != 12:
        raise FixtureContractError("score matrix must contain four experts and twelve rows")
    if any(values != supplier_ids for values in expert_suppliers.values()):
        raise FixtureContractError("every expert must score all three suppliers")
    return rows


def _winner(rows: list[dict[str, Any]]) -> str:
    values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        values[row["supplier_id"]].append(row["score"])
    return min(
        values,
        key=lambda supplier_id: (
            -sum(values[supplier_id]) / len(values[supplier_id]),
            supplier_id,
        ),
    )


def load_fixture(fixture_dir: Path | str) -> FixtureBundle:
    """Validate all source data and return the four runtime Arrow inputs."""

    root = Path(fixture_dir).resolve()
    if not root.is_dir():
        raise FixtureContractError(f"fixture directory does not exist: {root}")
    actual_files = {path.name for path in root.iterdir() if path.is_file()}
    if actual_files != EXPECTED_FIXTURE_FILES:
        raise FixtureContractError(
            "fixture must contain exactly four files; "
            f"missing={sorted(EXPECTED_FIXTURE_FILES - actual_files)}, "
            f"extra={sorted(actual_files - EXPECTED_FIXTURE_FILES)}"
        )

    project = _read_project(root / "project.json")
    project_id = _identifier(project.get("project_id"), "project_id")
    title = _non_empty_text(project.get("title"), "title")
    suppliers = _load_suppliers(project, project_id)
    supplier_ids = {row["supplier_id"] for row in suppliers}
    original_winner = _identifier(
        project.get("original_winner_supplier_id"),
        "original_winner_supplier_id",
    )
    if original_winner not in supplier_ids:
        raise FixtureContractError("original_winner_supplier_id is unknown")
    score_bias_threshold, ai_min_confidence = _load_thresholds(project)
    evidence = _load_evidence(project, project_id, root)
    scores = _load_scores(root / "expert_scores.csv", project_id, supplier_ids)
    if _winner(scores) != original_winner:
        raise FixtureContractError("declared original winner does not match score matrix")

    project_row = {
        "project_id": project_id,
        "title": title,
        "original_winner_supplier_id": original_winner,
        "score_bias_threshold": score_bias_threshold,
        "ai_min_confidence": ai_min_confidence,
    }
    return FixtureBundle(
        project=pa.Table.from_pylist([project_row]),
        suppliers=pa.Table.from_pylist(suppliers),
        scores=pa.Table.from_pylist(scores),
        evidence=pa.Table.from_pylist(evidence),
        source_dir=root,
    )
