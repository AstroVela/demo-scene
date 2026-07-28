"""Strict public contracts shared by ingestion, AI, SQL, and publication."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any


SIGNAL_STATES = frozenset(
    {
        "thesis_review_required",
        "thesis_supported",
        "manual_review",
        "insufficient_evidence",
    }
)
KNOWLEDGE_KINDS = frozenset({"source_fact", "uncertainty"})
REPORT_KNOWLEDGE_KINDS = frozenset(
    {"source_fact", "approved_thesis", "model_hypothesis", "uncertainty"}
)
EVIDENCE_STATUSES = frozenset(
    {"supported", "contradicted", "unresolved", "not_applicable"}
)
TRUST_TIERS = frozenset({1, 2, 3})
STAGE_STATUSES = frozenset({"pending", "succeeded", "quarantined", "failed"})
METRIC_CODES = frozenset(
    {
        "ORR",
        "TRAE_G3_PLUS",
        "SUBGROUP_ORR",
        "CASH_RUNWAY",
        "BLA_STATUS",
        "TRIAL_STATUS",
        "DOR",
        "PFS",
    }
)
CONDITION_IDS = frozenset(
    {"COND-EFFICACY", "COND-SAFETY", "COND-RUNWAY", "COND-REGULATORY"}
)
AI_RESPONSE_FIELDS = frozenset({"observations", "impact_hypotheses"})
OBSERVATION_FIELDS = frozenset(
    {
        "fact_type",
        "metric_code",
        "value_numeric",
        "value_text",
        "unit",
        "source_quote",
        "page",
        "confidence",
        "knowledge_kind",
        "review_required",
    }
)
IMPACT_FIELDS = frozenset(
    {
        "metric_code",
        "condition_id",
        "evidence_status",
        "rationale",
        "confidence",
    }
)
_JSON_FENCE = re.compile(
    r"\A```(?:json)?[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n```[ \t]*\Z",
    re.IGNORECASE,
)


class ContractError(ValueError):
    """Raised when a boundary payload does not satisfy its exact contract."""


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ContractError(f"invalid JSON constant: {value}")


def strict_json_object(raw: str) -> Mapping[str, Any]:
    if not isinstance(raw, str):
        raise ContractError("AI response must be text")
    candidate = raw.strip()
    fenced = _JSON_FENCE.fullmatch(candidate)
    if fenced is not None:
        candidate = fenced.group("body").strip()
    try:
        result = json.loads(
            candidate,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except ContractError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError, UnicodeError) as exc:
        raise ContractError("AI response must be one valid JSON object") from exc
    if not isinstance(result, Mapping):
        raise ContractError("AI response must be one valid JSON object")
    return result


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], path: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ContractError(
            f"{path} has wrong fields; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _required_text(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{path} must be non-empty text")
    result = re.sub(r"\s+", " ", value).strip()
    if not result:
        raise ContractError(f"{path} must be non-empty text")
    return result


def _optional_text(value: Any, path: str) -> str | None:
    return None if value is None else _required_text(value, path)


def _confidence(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{path} must be numeric")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ContractError(f"{path} must be between 0 and 1")
    return round(result, 4)


def _numeric(value: Any, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(f"{path} must be numeric or null")
    result = float(value)
    if not math.isfinite(result):
        raise ContractError(f"{path} must be finite")
    return result


def validate_ai_response(
    raw: str,
    *,
    allowed_metrics: set[str] | frozenset[str],
    allowed_conditions: set[str] | frozenset[str],
) -> dict[str, Any]:
    """Validate and canonicalize one VLM response without trusting source identity."""

    payload = strict_json_object(raw)
    _exact_fields(payload, AI_RESPONSE_FIELDS, "response")
    observations_value = payload["observations"]
    impacts_value = payload["impact_hypotheses"]
    if not isinstance(observations_value, Sequence) or isinstance(
        observations_value, (str, bytes)
    ):
        raise ContractError("response.observations must be an array")
    if not isinstance(impacts_value, Sequence) or isinstance(
        impacts_value, (str, bytes)
    ):
        raise ContractError("response.impact_hypotheses must be an array")
    if not observations_value:
        raise ContractError("response.observations must not be empty")

    observations: list[dict[str, Any]] = []
    seen_metrics: set[str] = set()
    for index, raw_observation in enumerate(observations_value):
        path = f"response.observations[{index}]"
        if not isinstance(raw_observation, Mapping):
            raise ContractError(f"{path} must be an object")
        _exact_fields(raw_observation, OBSERVATION_FIELDS, path)
        metric = _required_text(raw_observation["metric_code"], f"{path}.metric_code").upper()
        if metric not in METRIC_CODES or metric not in allowed_metrics:
            raise ContractError(f"{path}.metric_code is not allowed: {metric}")
        fact_type = _required_text(raw_observation["fact_type"], f"{path}.fact_type")
        if fact_type not in {"metric", "status", "statement"}:
            raise ContractError(f"{path}.fact_type is invalid")
        knowledge_kind = _required_text(
            raw_observation["knowledge_kind"], f"{path}.knowledge_kind"
        )
        if knowledge_kind not in KNOWLEDGE_KINDS:
            raise ContractError(f"{path}.knowledge_kind is invalid")
        page = raw_observation["page"]
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ContractError(f"{path}.page must be a positive integer")
        if not isinstance(raw_observation["review_required"], bool):
            raise ContractError(f"{path}.review_required must be boolean")
        value_numeric = _numeric(raw_observation["value_numeric"], f"{path}.value_numeric")
        value_text = _optional_text(raw_observation["value_text"], f"{path}.value_text")
        unit = _optional_text(raw_observation["unit"], f"{path}.unit")
        if value_numeric is None and value_text is None:
            raise ContractError(f"{path} must contain value_numeric or value_text")
        if value_numeric is not None and unit is None:
            raise ContractError(f"{path}.unit is required for numeric facts")
        observations.append(
            {
                "fact_type": fact_type,
                "metric_code": metric,
                "value_numeric": value_numeric,
                "value_text": value_text,
                "unit": unit,
                "source_quote": _required_text(
                    raw_observation["source_quote"], f"{path}.source_quote"
                ),
                "page": page,
                "confidence": _confidence(raw_observation["confidence"], f"{path}.confidence"),
                "knowledge_kind": knowledge_kind,
                "review_required": raw_observation["review_required"],
            }
        )
        seen_metrics.add(metric)

    impacts: list[dict[str, Any]] = []
    for index, raw_impact in enumerate(impacts_value):
        path = f"response.impact_hypotheses[{index}]"
        if not isinstance(raw_impact, Mapping):
            raise ContractError(f"{path} must be an object")
        _exact_fields(raw_impact, IMPACT_FIELDS, path)
        metric = _required_text(raw_impact["metric_code"], f"{path}.metric_code").upper()
        if metric not in seen_metrics:
            raise ContractError(f"{path}.metric_code must reference an observation")
        condition = _required_text(raw_impact["condition_id"], f"{path}.condition_id").upper()
        if condition not in CONDITION_IDS or condition not in allowed_conditions:
            raise ContractError(f"{path}.condition_id is not allowed: {condition}")
        evidence_status = _required_text(
            raw_impact["evidence_status"], f"{path}.evidence_status"
        )
        if evidence_status not in EVIDENCE_STATUSES:
            raise ContractError(f"{path}.evidence_status is invalid")
        impacts.append(
            {
                "metric_code": metric,
                "condition_id": condition,
                "evidence_status": evidence_status,
                "rationale": _required_text(raw_impact["rationale"], f"{path}.rationale"),
                "confidence": _confidence(raw_impact["confidence"], f"{path}.confidence"),
            }
        )
    return {"observations": observations, "impact_hypotheses": impacts}


def validate_primary_keys(rows: Sequence[Mapping[str, Any]], keys: tuple[str, ...], name: str) -> None:
    seen: set[tuple[Any, ...]] = set()
    for index, row in enumerate(rows):
        key = tuple(row.get(column) for column in keys)
        if any(value is None or value == "" for value in key):
            raise ContractError(f"{name}[{index}] has an empty primary key: {keys}")
        if key in seen:
            raise ContractError(f"{name} has duplicate primary key: {key}")
        seen.add(key)
