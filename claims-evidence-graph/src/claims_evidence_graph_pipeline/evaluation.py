"""Offline evaluation helpers for the claims evidence graph POC."""

from __future__ import annotations

import json
from typing import Any

import pyarrow as pa

from claims_evidence_graph_pipeline.contracts import (
    PHOTO_DAMAGE_EVAL_METRICS,
    PHOTO_EVAL_METRICS,
)


def _safe_divide(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def photo_label_requires_review(label: dict[str, Any]) -> bool:
    """Derive the human review target from photo-level labels."""

    return (not bool(label["usable_for_review"])) or bool(label["needs_reshoot"])


def _json_list_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        parsed = json.loads(value)
    else:
        parsed = value
    if not isinstance(parsed, list):
        return set()
    return {
        str(item).strip().lower()
        for item in parsed
        if str(item).strip()
    }


def _damage_prediction_set(value: Any) -> set[str]:
    return _json_list_set(value) - {"unknown", "none_visible"}


def _metric_row(
    *,
    metric_name: str,
    prediction_field: str,
    label_field: str,
    support: int,
    unmatched_label_count: int,
    true_positive: int,
    false_positive: int,
    true_negative: int,
    false_negative: int,
    notes: str,
) -> dict[str, Any]:
    precision = _safe_divide(true_positive, true_positive + false_positive)
    recall = _safe_divide(true_positive, true_positive + false_negative)
    return {
        "metric_name": metric_name,
        "prediction_field": prediction_field,
        "label_field": label_field,
        "support": support,
        "unmatched_label_count": unmatched_label_count,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
        "notes": notes,
    }


def _bool_metric_counts(
    *,
    labels: list[dict[str, Any]],
    damages_by_key: dict[tuple[str, str], dict[str, Any]],
    prediction_field: str,
    label_field: str,
) -> tuple[int, int, int, int, int]:
    true_positive = 0
    false_positive = 0
    true_negative = 0
    false_negative = 0
    unmatched_label_count = 0

    for label in labels:
        key = (str(label["claim_id"]), str(label["file_id"]))
        damage = damages_by_key.get(key)
        if damage is None:
            unmatched_label_count += 1
            continue

        predicted = bool(damage[prediction_field])
        expected = bool(label[label_field])
        if predicted and expected:
            true_positive += 1
        elif predicted and not expected:
            false_positive += 1
        elif not predicted and expected:
            false_negative += 1
        else:
            true_negative += 1

    return (
        true_positive,
        false_positive,
        true_negative,
        false_negative,
        unmatched_label_count,
    )


def _set_overlap_metric_counts(
    *,
    labels: list[dict[str, Any]],
    damages_by_key: dict[tuple[str, str], dict[str, Any]],
    prediction_field: str,
    label_field: str,
) -> tuple[int, int, int, int, int]:
    true_positive = 0
    false_positive = 0
    true_negative = 0
    false_negative = 0
    unmatched_label_count = 0

    for label in labels:
        key = (str(label["claim_id"]), str(label["file_id"]))
        damage = damages_by_key.get(key)
        if damage is None:
            unmatched_label_count += 1
            continue

        predicted = _damage_prediction_set(damage[prediction_field])
        expected = _json_list_set(label[label_field])
        if predicted and expected and predicted.intersection(expected):
            true_positive += 1
        elif predicted:
            false_positive += 1
        elif expected:
            false_negative += 1
        else:
            true_negative += 1

    return (
        true_positive,
        false_positive,
        true_negative,
        false_negative,
        unmatched_label_count,
    )


def _needs_review_metric_counts(
    *,
    labels: list[dict[str, Any]],
    damages_by_key: dict[tuple[str, str], dict[str, Any]],
) -> tuple[int, int, int, int, int]:
    true_positive = 0
    false_positive = 0
    true_negative = 0
    false_negative = 0
    unmatched_label_count = 0

    for label in labels:
        key = (str(label["claim_id"]), str(label["file_id"]))
        damage = damages_by_key.get(key)
        if damage is None:
            unmatched_label_count += 1
            continue

        predicted = bool(damage["needs_review"])
        expected = photo_label_requires_review(label)
        if predicted and expected:
            true_positive += 1
        elif predicted and not expected:
            false_positive += 1
        elif not predicted and expected:
            false_negative += 1
        else:
            true_negative += 1

    return (
        true_positive,
        false_positive,
        true_negative,
        false_negative,
        unmatched_label_count,
    )


def evaluate_photo_quality(
    photo_table: pa.Table,
    photo_label_table: pa.Table,
) -> pa.Table:
    """Evaluate rule-based photo review decisions against human labels."""

    photos_by_key = {
        (str(row["claim_id"]), str(row["file_id"])): row
        for row in photo_table.to_pylist()
    }

    true_positive = 0
    false_positive = 0
    true_negative = 0
    false_negative = 0
    unmatched_label_count = 0

    for label in photo_label_table.to_pylist():
        key = (str(label["claim_id"]), str(label["file_id"]))
        photo = photos_by_key.get(key)
        if photo is None:
            unmatched_label_count += 1
            continue

        expected_review = photo_label_requires_review(label)
        predicted_review = bool(photo["needs_review"])
        if predicted_review and expected_review:
            true_positive += 1
        elif predicted_review and not expected_review:
            false_positive += 1
        elif not predicted_review and expected_review:
            false_negative += 1
        else:
            true_negative += 1

    support = true_positive + false_positive + true_negative + false_negative
    precision = _safe_divide(true_positive, true_positive + false_positive)
    recall = _safe_divide(true_positive, true_positive + false_negative)
    notes = "All photo labels matched photo_evidence."
    if unmatched_label_count:
        notes = (
            f"{unmatched_label_count} photo label rows did not match "
            "photo_evidence and were excluded."
        )

    return PHOTO_EVAL_METRICS.arrow_table(
        [
            {
                "metric_name": "photo_quality_needs_review",
                "prediction_rule": "photo_evidence.needs_review",
                "label_rule": "not usable_for_review or needs_reshoot",
                "support": support,
                "unmatched_label_count": unmatched_label_count,
                "true_positive": true_positive,
                "false_positive": false_positive,
                "true_negative": true_negative,
                "false_negative": false_negative,
                "precision": precision,
                "recall": recall,
                "f1": _f1(precision, recall),
                "notes": notes,
            }
        ]
    )


def evaluate_photo_damage(
    photo_damage_table: pa.Table,
    photo_label_table: pa.Table,
) -> pa.Table:
    """Evaluate model-generated photo damage evidence against photo labels."""

    labels = photo_label_table.to_pylist()
    damages_by_key = {
        (str(row["claim_id"]), str(row["file_id"])): row
        for row in photo_damage_table.to_pylist()
    }
    support = sum(
        1
        for label in labels
        if (str(label["claim_id"]), str(label["file_id"])) in damages_by_key
    )
    unmatched_predictions = max(0, len(damages_by_key) - support)
    base_notes = "Matched labels are evaluated against photo_damage_evidence."
    if unmatched_predictions:
        base_notes += (
            f" {unmatched_predictions} photo_damage_evidence rows had no label "
            "and were ignored."
        )

    rows: list[dict[str, Any]] = []

    for field in (
        "vehicle_visible",
        "target_vehicle_clear",
        "damage_visible",
    ):
        tp, fp, tn, fn, unmatched = _bool_metric_counts(
            labels=labels,
            damages_by_key=damages_by_key,
            prediction_field=field,
            label_field=field,
        )
        rows.append(
            _metric_row(
                metric_name=f"photo_damage_{field}",
                prediction_field=f"photo_damage_evidence.{field}",
                label_field=f"photo_human_labels.{field}",
                support=support,
                unmatched_label_count=unmatched,
                true_positive=tp,
                false_positive=fp,
                true_negative=tn,
                false_negative=fn,
                notes=base_notes,
            )
        )

    for prediction_field, label_field in (
        ("damaged_parts_json", "damaged_parts_json"),
        ("damage_types_json", "damage_types_json"),
    ):
        tp, fp, tn, fn, unmatched = _set_overlap_metric_counts(
            labels=labels,
            damages_by_key=damages_by_key,
            prediction_field=prediction_field,
            label_field=label_field,
        )
        rows.append(
            _metric_row(
                metric_name=f"photo_damage_{prediction_field.removesuffix('_json')}_overlap",
                prediction_field=f"photo_damage_evidence.{prediction_field}",
                label_field=f"photo_human_labels.{label_field}",
                support=support,
                unmatched_label_count=unmatched,
                true_positive=tp,
                false_positive=fp,
                true_negative=tn,
                false_negative=fn,
                notes=(
                    base_notes
                    + " Set fields are scored as positive when prediction and "
                    "label have at least one overlapping value."
                ),
            )
        )

    tp, fp, tn, fn, unmatched = _needs_review_metric_counts(
        labels=labels,
        damages_by_key=damages_by_key,
    )
    rows.append(
        _metric_row(
            metric_name="photo_damage_needs_review",
            prediction_field="photo_damage_evidence.needs_review",
            label_field="not usable_for_review or needs_reshoot",
            support=support,
            unmatched_label_count=unmatched,
            true_positive=tp,
            false_positive=fp,
            true_negative=tn,
            false_negative=fn,
            notes=(
                base_notes
                + " This checks whether low confidence, uncertainty, or unclear "
                "vehicle evidence routes the photo to review."
            ),
        )
    )

    return PHOTO_DAMAGE_EVAL_METRICS.arrow_table(rows)
