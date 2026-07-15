"""Validate and atomically publish the two JSONL demo outputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any

from .vane_functions import stable_json


FINDING_COLUMNS = (
    "finding_id",
    "project_id",
    "rule_id",
    "severity",
    "subject_type",
    "subject_id",
    "supplier_id",
    "metric_name",
    "metric_value",
    "threshold_value",
    "finding_summary",
    "evidence_file_ids_json",
    "recommended_action",
    "confidence",
)
SUMMARY_COLUMNS = (
    "project_id",
    "title",
    "status",
    "finding_count",
    "high_severity_count",
    "original_winner_supplier_id",
    "winner_without_flagged_expert",
    "flagged_expert_id",
)
ALLOWED_RULES = {
    "EXP-001-conflict-not-recused",
    "EXP-002-score-bias",
    "EXP-003-award-impact",
}
ALLOWED_SEVERITIES = {"low", "medium", "high"}
ALLOWED_SUBJECT_TYPES = {"expert", "project"}
ALLOWED_STATUSES = {"passed", "review_required", "insufficient_evidence"}
OUTPUT_FILENAMES = frozenset({"audit_findings.jsonl", "audit_summary.jsonl"})


class OutputContractError(ValueError):
    """Raised before either output file is changed."""


@dataclass(frozen=True)
class PublishedOutputs:
    findings_path: Path
    summary_path: Path
    finding_count: int
    summary_count: int


def _rows(value: Any, name: str) -> Sequence[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise OutputContractError(f"{name} must be a sequence of rows")
    if any(not isinstance(row, Mapping) for row in value):
        raise OutputContractError(f"{name} rows must be objects")
    return value


def _exact_columns(row: Mapping[str, Any], columns: tuple[str, ...], context: str) -> None:
    actual = set(row)
    expected = set(columns)
    if actual != expected:
        raise OutputContractError(
            f"{context} has wrong columns; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def _text(value: Any, field: str, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OutputContractError(f"{context}: {field} must be non-empty text")
    return value.strip()


def _number(value: Any, field: str, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OutputContractError(f"{context}: {field} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise OutputContractError(f"{context}: {field} must be finite")
    return result


def _count(value: Any, field: str, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise OutputContractError(f"{context}: {field} must be a non-negative integer")
    return value


def validate_findings(
    findings: Sequence[Mapping[str, Any]],
    known_evidence_ids: set[str] | frozenset[str],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_rules: set[tuple[str, str]] = set()
    known = set(known_evidence_ids)
    for index, row in enumerate(_rows(findings, "findings")):
        context = f"finding row {index}"
        _exact_columns(row, FINDING_COLUMNS, context)
        finding_id = _text(row["finding_id"], "finding_id", context)
        project_id = _text(row["project_id"], "project_id", context)
        rule_id = _text(row["rule_id"], "rule_id", context)
        if rule_id not in ALLOWED_RULES:
            raise OutputContractError(f"{context}: unsupported rule_id {rule_id}")
        if finding_id != f"{project_id}:{rule_id}":
            raise OutputContractError(f"{context}: finding_id must bind project_id and rule_id")
        if finding_id in seen_ids or (project_id, rule_id) in seen_rules:
            raise OutputContractError(f"{context}: duplicate finding identity")
        seen_ids.add(finding_id)
        seen_rules.add((project_id, rule_id))
        severity = _text(row["severity"], "severity", context)
        if severity not in ALLOWED_SEVERITIES:
            raise OutputContractError(f"{context}: invalid severity")
        subject_type = _text(row["subject_type"], "subject_type", context)
        if subject_type not in ALLOWED_SUBJECT_TYPES:
            raise OutputContractError(f"{context}: invalid subject_type")
        metric_value = _number(row["metric_value"], "metric_value", context)
        threshold_value = _number(row["threshold_value"], "threshold_value", context)
        confidence = _number(row["confidence"], "confidence", context)
        if not 0.0 <= confidence <= 1.0:
            raise OutputContractError(f"{context}: confidence must be between 0 and 1")
        evidence_value = row["evidence_file_ids_json"]
        try:
            evidence_ids = json.loads(evidence_value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise OutputContractError(
                f"{context}: evidence_file_ids_json must be valid JSON"
            ) from exc
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or any(not isinstance(item, str) or not item.strip() for item in evidence_ids)
            or len(set(evidence_ids)) != len(evidence_ids)
        ):
            raise OutputContractError(
                f"{context}: evidence_file_ids_json must be a unique non-empty string list"
            )
        unknown = set(evidence_ids) - known
        if unknown:
            raise OutputContractError(
                f"{context}: unknown evidence references {sorted(unknown)}"
            )
        values = {
            "finding_id": finding_id,
            "project_id": project_id,
            "rule_id": rule_id,
            "severity": severity,
            "subject_type": subject_type,
            "subject_id": _text(row["subject_id"], "subject_id", context),
            "supplier_id": _text(row["supplier_id"], "supplier_id", context),
            "metric_name": _text(row["metric_name"], "metric_name", context),
            "metric_value": metric_value,
            "threshold_value": threshold_value,
            "finding_summary": _text(
                row["finding_summary"], "finding_summary", context
            ),
            "evidence_file_ids_json": stable_json(evidence_ids),
            "recommended_action": _text(
                row["recommended_action"], "recommended_action", context
            ),
            "confidence": confidence,
        }
        normalized.append({column: values[column] for column in FINDING_COLUMNS})
    normalized.sort(key=lambda row: (row["project_id"], row["rule_id"]))
    return normalized


def validate_summaries(
    summaries: Sequence[Mapping[str, Any]],
    findings: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    source = _rows(summaries, "summaries")
    if len(source) != 1:
        raise OutputContractError("summaries must contain exactly one project row")
    row = source[0]
    context = "summary row 0"
    _exact_columns(row, SUMMARY_COLUMNS, context)
    project_id = _text(row["project_id"], "project_id", context)
    status = _text(row["status"], "status", context)
    if status not in ALLOWED_STATUSES:
        raise OutputContractError(f"{context}: invalid status")
    finding_count = _count(row["finding_count"], "finding_count", context)
    high_count = _count(row["high_severity_count"], "high_severity_count", context)
    project_findings = [item for item in findings if item["project_id"] == project_id]
    actual_high_count = sum(item["severity"] == "high" for item in project_findings)
    if finding_count != len(project_findings):
        raise OutputContractError(f"{context}: finding_count does not match findings")
    if high_count != actual_high_count:
        raise OutputContractError(
            f"{context}: high_severity_count does not match findings"
        )
    if finding_count and status != "review_required":
        raise OutputContractError(f"{context}: findings require review_required status")
    if not finding_count and status == "review_required":
        raise OutputContractError(f"{context}: review_required status needs a finding")

    winner_without = row["winner_without_flagged_expert"]
    flagged_expert = row["flagged_expert_id"]
    if status == "insufficient_evidence":
        if winner_without is not None:
            winner_without = _text(
                winner_without,
                "winner_without_flagged_expert",
                context,
            )
        if flagged_expert is not None:
            flagged_expert = _text(flagged_expert, "flagged_expert_id", context)
    else:
        winner_without = _text(
            winner_without,
            "winner_without_flagged_expert",
            context,
        )
        flagged_expert = _text(flagged_expert, "flagged_expert_id", context)
    values = {
        "project_id": project_id,
        "title": _text(row["title"], "title", context),
        "status": status,
        "finding_count": finding_count,
        "high_severity_count": high_count,
        "original_winner_supplier_id": _text(
            row["original_winner_supplier_id"],
            "original_winner_supplier_id",
            context,
        ),
        "winner_without_flagged_expert": winner_without,
        "flagged_expert_id": flagged_expert,
    }
    return [{column: values[column] for column in SUMMARY_COLUMNS}]


def _atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            for row in rows:
                handle.write(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                    )
                )
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_outputs(
    findings: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]],
    output_dir: Path | str,
    known_evidence_ids: set[str] | frozenset[str],
) -> PublishedOutputs:
    """Validate both outputs completely, then atomically replace each JSONL file."""

    normalized_findings = validate_findings(findings, known_evidence_ids)
    normalized_summaries = validate_summaries(summaries, normalized_findings)
    root = Path(output_dir)
    findings_path = root / "audit_findings.jsonl"
    summary_path = root / "audit_summary.jsonl"
    _atomic_jsonl(findings_path, normalized_findings)
    _atomic_jsonl(summary_path, normalized_summaries)
    return PublishedOutputs(
        findings_path=findings_path,
        summary_path=summary_path,
        finding_count=len(normalized_findings),
        summary_count=len(normalized_summaries),
    )
