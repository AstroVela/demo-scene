"""Validate the audit mart and atomically publish per-call JSON to MinIO."""

from __future__ import annotations

from datetime import datetime
import json
import math
from typing import Any, Mapping, Sequence

from .config import RuntimeConfig
from .minio_store import MinioStore
from .vane_udfs import stable_json


ANALYSIS_CONTENT_TYPE = "application/json"
BATCH_SUMMARY_OBJECT = "batch_summary.json"

# Columns that every mart row must carry (from call_audit_report).
REQUIRED_COLUMNS = frozenset(
    {
        "call_id",
        "bucket",
        "object_key",
        "object_sha256",
        "probe_status",
        "audio_usable",
        "duration_seconds",
        "channels",
        "sample_rate",
        "quality_reasons_json",
        "asr_status",
        "transcript_text",
        "transcript_usable",
        "text_length",
        "language_confidence",
        "transcript_failure_reasons_json",
        "analysis_status",
        "problem_category",
        "customer_sentiment",
        "sentiment_score",
        "urgency",
        "key_issues_json",
        "customer_request",
        "resolution_status",
        "requires_followup",
        "agent_attitude",
        "summary",
        "uncertainty_reasons_json",
        "confidence",
        "review_disposition",
        "run_started_at",
        "asr_engine",
        "asr_model",
        "ai_provider",
        "ai_model",
    }
)
ALLOWED_REVIEW_DISPOSITIONS = {
    "audited",
    "review_unusable_audio",
    "review_low_quality_transcript",
    "review_invalid_analysis",
}


class OutputContractError(ValueError):
    """Raised before publication when a mart row violates the output contract."""


def _text(row: Mapping[str, Any], field: str, call_id: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise OutputContractError(f"{call_id}: {field} must be non-empty text")
    return value.strip()


def _optional_text(row: Mapping[str, Any], field: str) -> str:
    value = row.get(field)
    if value is None:
        return ""
    return str(value)


def _score(value: Any, lower: float, upper: float, call_id: str, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OutputContractError(f"{call_id}: {field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not lower <= result <= upper:
        raise OutputContractError(
            f"{call_id}: {field} must be between {lower} and {upper}"
        )
    return round(result, 4)


def _json_list(value: Any, call_id: str, field: str) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise OutputContractError(
                f"{call_id}: {field} must be valid JSON"
            ) from exc
    if not isinstance(value, list):
        raise OutputContractError(f"{call_id}: {field} must be a JSON array")
    return value


def _parse_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one mart row into the publication-ready analysis dict."""

    call_id = _text(row, "call_id", "unknown")
    review_disposition = _text(row, "review_disposition", call_id)
    if review_disposition not in ALLOWED_REVIEW_DISPOSITIONS:
        raise OutputContractError(
            f"{call_id}: invalid review_disposition {review_disposition}"
        )
    return {
        "call_id": call_id,
        "object_key": _text(row, "object_key", call_id),
        "object_sha256": _optional_text(row, "object_sha256"),
        "audio": {
            "probe_status": _optional_text(row, "probe_status"),
            "audio_usable": bool(row.get("audio_usable", False)),
            "duration_seconds": row.get("duration_seconds") or 0.0,
            "channels": row.get("channels") or 0,
            "sample_rate": row.get("sample_rate") or 0,
            "quality_reasons": _json_list(row.get("quality_reasons_json", "[]"), call_id, "quality_reasons_json"),
        },
        "transcript": {
            "asr_status": _optional_text(row, "asr_status"),
            "transcript_usable": bool(row.get("transcript_usable", False)),
            "text_length": row.get("text_length") or 0,
            "language_confidence": row.get("language_confidence") or 0.0,
            "failure_reasons": _json_list(
                row.get("transcript_failure_reasons_json", "[]"), call_id, "transcript_failure_reasons_json"
            ),
        },
        "analysis": {
            "analysis_status": _optional_text(row, "analysis_status"),
            "problem_category": _optional_text(row, "problem_category"),
            "customer_sentiment": _optional_text(row, "customer_sentiment"),
            "sentiment_score": _score(
                row.get("sentiment_score", 0.0), -1.0, 1.0, call_id, "sentiment_score"
            ),
            "urgency": _optional_text(row, "urgency"),
            "key_issues": _json_list(row.get("key_issues_json", "[]"), call_id, "key_issues_json"),
            "customer_request": _optional_text(row, "customer_request"),
            "resolution_status": _optional_text(row, "resolution_status"),
            "requires_followup": bool(row.get("requires_followup", True)),
            "agent_attitude": _optional_text(row, "agent_attitude"),
            "summary": _optional_text(row, "summary"),
            "uncertainty_reasons": _json_list(
                row.get("uncertainty_reasons_json", "[]"), call_id, "uncertainty_reasons_json"
            ),
            "confidence": _score(row.get("confidence", 0.0), 0.0, 1.0, call_id, "confidence"),
        },
        "review_disposition": review_disposition,
        "run_metadata": {
            "run_started_at": _optional_text(row, "run_started_at"),
            "asr_engine": _optional_text(row, "asr_engine"),
            "asr_model": _optional_text(row, "asr_model"),
            "ai_provider": _optional_text(row, "ai_provider"),
            "ai_model": _optional_text(row, "ai_model"),
        },
    }


def validate_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate and normalize the complete audit mart before publication."""

    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise OutputContractError("output payload must be a list of rows")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise OutputContractError(f"row {index} must be a mapping")
        actual = set(row.keys())
        missing = REQUIRED_COLUMNS - actual
        if missing:
            raise OutputContractError(
                f"row {index}: missing columns {sorted(missing)}"
            )
        parsed = _parse_row(row)
        call_id = parsed["call_id"]
        if call_id in seen:
            raise OutputContractError(f"duplicate call_id: {call_id}")
        seen.add(call_id)
        normalized.append(parsed)
    return normalized


def publish_analysis_json(
    rows: Sequence[Mapping[str, Any]],
    config: RuntimeConfig,
    run_started_at: datetime,
) -> int:
    """Validate the mart, then publish one JSON per call plus a batch summary."""

    normalized = validate_rows(rows)
    store = MinioStore(config.minio)
    store.probe()
    store.ensure_bucket(config.minio.bucket)

    # Clean the analysis prefix so stale outputs from prior runs never survive.
    store.remove_prefix(config.minio.bucket, config.minio.analysis_prefix)

    for parsed in normalized:
        object_key = f"{config.minio.analysis_prefix}{parsed['call_id']}.json"
        store.put_bytes(
            config.minio.bucket,
            object_key,
            stable_json(parsed).encode("utf-8"),
            ANALYSIS_CONTENT_TYPE,
        )

    # The batch summary provides a single-file audit dashboard.
    summary = {
        "batch_run_started_at": run_started_at.isoformat(),
        "total_calls": len(normalized),
        "audited_calls": sum(
            1 for item in normalized if item["review_disposition"] == "audited"
        ),
        "review_calls": sum(
            1 for item in normalized if item["review_disposition"] != "audited"
        ),
        "problem_categories": _count_field(normalized, "problem_category"),
        "customer_sentiments": _count_field(normalized, "customer_sentiment"),
        "calls": [
            {
                "call_id": item["call_id"],
                "review_disposition": item["review_disposition"],
                "problem_category": item["analysis"]["problem_category"],
                "customer_sentiment": item["analysis"]["customer_sentiment"],
                "requires_followup": item["analysis"]["requires_followup"],
            }
            for item in sorted(normalized, key=lambda x: x["call_id"])
        ],
    }
    summary_key = f"{config.minio.analysis_prefix}{BATCH_SUMMARY_OBJECT}"
    store.put_bytes(
        config.minio.bucket,
        summary_key,
        stable_json(summary).encode("utf-8"),
        ANALYSIS_CONTENT_TYPE,
    )
    return len(normalized)


def _count_field(
    items: list[dict[str, Any]],
    field: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = item["analysis"].get(field, "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))
