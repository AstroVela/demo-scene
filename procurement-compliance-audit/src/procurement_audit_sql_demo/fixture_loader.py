"""Validate the synthetic fixture and load it into PostgreSQL and MinIO."""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

from .config import DEFAULT_CONFIG_PATH, PROJECT_ROOT, load_runtime_config
from .minio_store import MinioStore
from .pg import connect_postgres, initialize_schema, reset_fixture_rows
from .source_data import SourceBundle, source_bundle_from_rows


EXPECTED_FIXTURE_FILES = frozenset(
    {
        "project.json",
        "expert_scores.csv",
        "expert_recommendation.png",
        "committee_minutes.png",
    }
)
DEFAULT_FIXTURE_DIR = PROJECT_ROOT / "fixtures/expert-score-anomaly"
_PROJECT_FIELDS = {
    "project_id",
    "title",
    "original_winner_supplier_id",
    "suppliers",
    "evidence_files",
    "thresholds",
}
_SUPPLIER_FIELDS = {"supplier_id", "name", "aliases"}
_EVIDENCE_FIELDS = {"file_id", "role", "object_key", "media_type"}
_THRESHOLD_FIELDS = {"score_bias_points", "ai_min_confidence"}
_SCORE_FIELDS = ("project_id", "expert_id", "expert_name", "supplier_id", "score")
_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")


class FixtureContractError(ValueError):
    """Raised before runtime or model work when fixture data is invalid."""


@dataclass(frozen=True)
class FixtureObject:
    bucket: str
    object_key: str
    value: bytes
    content_type: str


@dataclass(frozen=True)
class FixtureBundle:
    source: SourceBundle
    objects: tuple[FixtureObject, ...]

    @property
    def project(self):
        return self.source.project

    @property
    def suppliers(self):
        return self.source.suppliers

    @property
    def scores(self):
        return self.source.scores

    @property
    def evidence(self):
        return self.source.evidence


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
    bucket: str,
) -> tuple[list[dict[str, Any]], list[FixtureObject]]:
    values = project.get("evidence_files")
    if not isinstance(values, list) or len(values) != 2:
        raise FixtureContractError("evidence_files must contain exactly two rows")
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_roles: set[str] = set()
    expected_roles = {"expert_recommendation", "committee_minutes"}
    objects: list[FixtureObject] = []
    for index, value in enumerate(values):
        path = f"evidence_files[{index}]"
        if not isinstance(value, Mapping) or set(value) != _EVIDENCE_FIELDS:
            raise FixtureContractError(f"{path} has wrong fields")
        file_id = _identifier(value["file_id"], f"{path}.file_id")
        role = _non_empty_text(value["role"], f"{path}.role")
        media_type = _non_empty_text(value["media_type"], f"{path}.media_type")
        object_key = _non_empty_text(value["object_key"], f"{path}.object_key")
        object_parts = object_key.split("/")
        if object_key.startswith("/") or any(
            part in {"", ".", ".."} for part in object_parts
        ):
            raise FixtureContractError(f"{path}.object_key is invalid")
        seed_path = (fixture_dir / object_parts[-1]).resolve()
        try:
            seed_path.relative_to(fixture_dir)
        except ValueError as exc:
            raise FixtureContractError(f"{path}.object_key is invalid") from exc
        if not seed_path.is_file():
            raise FixtureContractError(
                f"{path}.object_key has no matching seed asset: {object_parts[-1]}"
            )
        if media_type != "image/png" or not object_key.lower().endswith(".png"):
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
                "bucket": bucket,
                "object_key": object_key,
                "media_type": media_type,
            }
        )
        objects.append(
            FixtureObject(
                bucket=bucket,
                object_key=object_key,
                value=seed_path.read_bytes(),
                content_type=media_type,
            )
        )
    if seen_roles != expected_roles:
        raise FixtureContractError(f"evidence roles must be {sorted(expected_roles)}")
    return rows, objects


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


def build_fixture(
    fixture_dir: Path | str = DEFAULT_FIXTURE_DIR,
    bucket: str = "procurement-compliance-audit-fixtures",
) -> FixtureBundle:
    """Validate local seed assets without treating them as runtime inputs."""

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
    evidence, objects = _load_evidence(project, project_id, root, bucket)
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
    source = source_bundle_from_rows(
        [project_row],
        suppliers,
        scores,
        evidence,
        expected_bucket=bucket,
    )
    return FixtureBundle(source=source, objects=tuple(objects))


def load_fixture(
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    fixture_dir: Path | str = DEFAULT_FIXTURE_DIR,
) -> tuple[int, int, int]:
    """Refresh the PostgreSQL snapshot and MinIO objects used by the runtime."""

    config = load_runtime_config(config_path)
    fixture = build_fixture(fixture_dir, config.minio.bucket)
    source = fixture.source

    with connect_postgres(config.postgres) as connection:
        initialize_schema(connection, config.postgres)
        reset_fixture_rows(
            connection,
            config.postgres,
            projects=source.project.to_pylist(),
            suppliers=source.suppliers.to_pylist(),
            scores=source.scores.to_pylist(),
            evidence=source.evidence.to_pylist(),
        )

    store = MinioStore(config.minio)
    store.probe()
    store.ensure_bucket(config.minio.bucket)
    for project in source.project.to_pylist():
        store.remove_prefix(
            config.minio.bucket,
            f"procurement/{project['project_id']}/",
        )
    for item in fixture.objects:
        store.put_bytes(
            item.bucket,
            item.object_key,
            item.value,
            item.content_type,
        )

    return source.project.num_rows, source.scores.num_rows, len(fixture.objects)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load procurement audit fixtures into PostgreSQL and MinIO."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Runtime YAML path.",
    )
    parser.add_argument(
        "--fixture-dir",
        type=Path,
        default=DEFAULT_FIXTURE_DIR,
        help="Local synthetic seed asset directory.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        project_count, score_count, object_count = load_fixture(
            args.config,
            args.fixture_dir,
        )
    except Exception as exc:
        print(f"fixture load failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"loaded {project_count} project, {score_count} scores and "
        f"{object_count} MinIO objects"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
