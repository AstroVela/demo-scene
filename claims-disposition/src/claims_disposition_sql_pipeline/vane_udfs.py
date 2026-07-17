"""Stateless Vane expression UDFs and their deterministic pure helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import io
import json
import re
from typing import Any, Iterable, Mapping

import numpy as np
from PIL import Image, UnidentifiedImageError
import vane

from .config import MinioConfig
from .minio_store import MinioStore


_DOCUMENT_LABEL = re.compile(
    r"^\s*(CLAIM\s*(?:NUMBER|NO\.?|#)|CLAIMANT\s*NAME|LOSS\s*DATE)\s*[:#\-]?\s*(.*)$",
    re.IGNORECASE,
)
_LABEL_KEYS = {
    "claimnumber": "claim_number",
    "claimno": "claim_number",
    "claim": "claim_number",
    "claimantname": "claimant_name",
    "lossdate": "loss_date",
}
_SEVERITIES = {"none", "minor", "moderate", "severe", "total_loss", "unknown"}
EVIDENCE_LIMITATION_CODES = (
    "blur",
    "occlusion",
    "low_resolution",
    "target_vehicle_unclear",
    "insufficient_view",
    "image_integrity_or_authenticity_concern",
    "conflicting_visual_cues",
)
_EVIDENCE_LIMITATION_CODE_SET = frozenset(EVIDENCE_LIMITATION_CODES)


@dataclass(frozen=True)
class SqlUdfSpec:
    function: object
    alias: str
    parameters: tuple[str, ...]


@dataclass(frozen=True)
class MinioUdfs:
    object_exists: object
    object_sha256: object
    photo_quality: object


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def analyze_photo_bytes(value: bytes) -> dict[str, Any]:
    try:
        with Image.open(io.BytesIO(value)) as source:
            source.load()
            image = source.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        return {
            "status": "decode_error",
            "photo_usable": False,
            "quality_score": 0.0,
            "quality_reasons": ["image_decode_failed"],
            "error_type": type(exc).__name__,
        }

    grayscale = np.asarray(image.convert("L"), dtype=np.float32)
    brightness = float(grayscale.mean() / 255.0)
    contrast = float(grayscale.std())
    horizontal = float(np.abs(np.diff(grayscale, axis=1)).mean())
    vertical = float(np.abs(np.diff(grayscale, axis=0)).mean())
    sharpness = (horizontal + vertical) / 2.0
    resolution_score = min(1.0, image.width / 1280.0, image.height / 960.0)
    brightness_score = max(0.0, 1.0 - abs(brightness - 0.5) / 0.5)
    contrast_score = min(1.0, contrast / 40.0)
    sharpness_score = min(1.0, sharpness / 8.0)
    quality_score = (
        resolution_score + brightness_score + contrast_score + sharpness_score
    ) / 4.0

    quality_reasons: list[str] = []
    if image.width < 640 or image.height < 480:
        quality_reasons.append("resolution_too_low")
    if brightness < 0.10:
        quality_reasons.append("too_dark")
    elif brightness > 0.95:
        quality_reasons.append("too_bright")
    if contrast < 10.0:
        quality_reasons.append("low_contrast")
    if sharpness < 1.5:
        quality_reasons.append("low_detail")
    if quality_score < 0.40:
        quality_reasons.append("quality_score_below_threshold")

    return {
        "status": "success",
        "width": image.width,
        "height": image.height,
        "brightness": round(brightness, 4),
        "contrast": round(contrast, 4),
        "sharpness": round(sharpness, 4),
        "quality_score": round(quality_score, 4),
        "photo_usable": not quality_reasons,
        "quality_reasons": quality_reasons,
    }


def build_minio_udfs(config: MinioConfig) -> MinioUdfs:
    @vane.func(return_dtype="BOOLEAN", name="minio_object_exists")
    def object_exists(bucket: str, object_key: str) -> bool:
        return MinioStore(config).exists(bucket, object_key)

    @vane.func(return_dtype="VARCHAR", name="minio_object_sha256")
    def object_sha256(bucket: str, object_key: str) -> str | None:
        return MinioStore(config).sha256(bucket, object_key)

    @vane.func(return_dtype="VARCHAR", name="photo_quality_json")
    def photo_quality(bucket: str, object_key: str) -> str:
        value = MinioStore(config).get_bytes(bucket, object_key)
        return stable_json(analyze_photo_bytes(value))

    return MinioUdfs(object_exists, object_sha256, photo_quality)


def _normalized_label(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.lower())


def normalize_loss_date(value: str) -> str:
    candidate = value.strip()
    for date_format in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(candidate, date_format).date().isoformat()
        except ValueError:
            continue
    return ""


def extract_document_fields(ocr: Mapping[str, Any]) -> dict[str, str]:
    fields = {
        "claim_number": "",
        "claimant_name": "",
        "loss_date": "",
    }
    raw_lines = ocr.get("text_lines", [])
    entries = []
    for item in raw_lines:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        bbox_value = item.get("bbox")
        bbox = None
        if (
            isinstance(bbox_value, (list, tuple))
            and len(bbox_value) == 4
            and all(isinstance(value, (int, float)) for value in bbox_value)
        ):
            bbox = tuple(float(value) for value in bbox_value)
        entries.append({"text": text, "bbox": bbox})

    labels = []
    for index, entry in enumerate(entries):
        match = _DOCUMENT_LABEL.match(entry["text"])
        if not match:
            continue
        label = _normalized_label(match.group(1))
        key = _LABEL_KEYS.get(label)
        value = match.group(2).strip(" :-\t")
        if key:
            fields[key] = value
            labels.append((index, key, entry["bbox"], bool(value)))

    used_values: set[int] = set()
    for _label_index, key, label_bbox, has_inline_value in labels:
        if has_inline_value or label_bbox is None:
            continue
        label_center_y = (label_bbox[1] + label_bbox[3]) / 2.0
        label_height = max(1.0, label_bbox[3] - label_bbox[1])
        candidates = []
        for candidate_index, candidate in enumerate(entries):
            candidate_bbox = candidate["bbox"]
            if candidate_index in used_values or candidate_bbox is None:
                continue
            if _DOCUMENT_LABEL.match(candidate["text"]):
                continue
            candidate_center_y = (candidate_bbox[1] + candidate_bbox[3]) / 2.0
            candidate_height = max(1.0, candidate_bbox[3] - candidate_bbox[1])
            vertical_distance = abs(candidate_center_y - label_center_y)
            same_row_tolerance = max(label_height, candidate_height) * 0.65
            is_to_the_right = candidate_bbox[0] >= label_bbox[2] - 20.0
            if vertical_distance <= same_row_tolerance and is_to_the_right:
                candidates.append(
                    (vertical_distance, candidate_bbox[0], candidate_index, candidate["text"])
                )
        if candidates:
            _, _, candidate_index, value = min(candidates)
            fields[key] = value.strip()
            used_values.add(candidate_index)
    fields["loss_date"] = normalize_loss_date(fields["loss_date"])
    return fields


@vane.func(return_dtype="VARCHAR", name="document_fields_json")
def document_fields_json(ocr_json: str) -> str:
    try:
        payload = json.loads(ocr_json)
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    return stable_json(extract_document_fields(payload))


def assess_document_quality(
    ocr: Mapping[str, Any],
    fields: Mapping[str, Any],
    expected_claim_id: str,
    required_fields: Iterable[str],
    minimum_confidence: float,
) -> dict[str, Any]:
    required = [str(value) for value in required_fields]
    missing = [name for name in required if not str(fields.get(name, "")).strip()]
    confidence_value = ocr.get("mean_confidence", 0.0)
    confidence = (
        float(confidence_value)
        if isinstance(confidence_value, (int, float))
        and not isinstance(confidence_value, bool)
        else 0.0
    )
    claim_number = str(fields.get("claim_number", "")).strip()
    claim_number_matches = claim_number == expected_claim_id
    ocr_success = ocr.get("status") == "success"

    failure_reasons = []
    if not ocr_success:
        failure_reasons.append("ocr_unreadable")
    if confidence < minimum_confidence:
        failure_reasons.append("ocr_confidence_below_threshold")
    if missing:
        failure_reasons.append("required_fields_missing")
    if claim_number and not claim_number_matches:
        failure_reasons.append("claim_number_mismatch")

    return {
        "document_usable": not failure_reasons,
        "ocr_status": str(ocr.get("status", "unreadable")),
        "ocr_confidence": round(confidence, 4),
        "minimum_text_confidence": float(minimum_confidence),
        "missing_fields": missing,
        "claim_number_matches": claim_number_matches,
        "failure_reasons": failure_reasons,
    }


@vane.func(return_dtype="VARCHAR", name="document_quality_json")
def document_quality_json(
    ocr_json: str,
    fields_json: str,
    expected_claim_id: str,
    required_fields_json: str,
    minimum_confidence: float,
) -> str:
    try:
        ocr = json.loads(ocr_json)
        fields = json.loads(fields_json)
        required_fields = json.loads(required_fields_json)
    except (TypeError, json.JSONDecodeError):
        ocr, fields, required_fields = {}, {}, []
    if not isinstance(ocr, Mapping):
        ocr = {}
    if not isinstance(fields, Mapping):
        fields = {}
    if not isinstance(required_fields, list):
        required_fields = []
    return stable_json(
        assess_document_quality(
            ocr,
            fields,
            expected_claim_id,
            required_fields,
            minimum_confidence,
        )
    )


def _damage_error(
    reason: str,
    claim_id: str,
    file_id: str,
    sha256: str,
) -> str:
    return stable_json(
        {
            "status": "invalid_response",
            "claim_id": claim_id,
            "file_id": file_id,
            "sha256": sha256,
            "vehicle_visible": False,
            "target_vehicle_clear": False,
            "damage_visible": False,
            "damaged_parts": [],
            "damage_types": [],
            "evidence_summary": "",
            "finding_determinate": None,
            "evidence_limitations": [],
            "severity_hint": "unknown",
            "uncertainty_reasons": [reason],
            "confidence": 0.0,
        }
    )


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return [item.strip() for item in value if item.strip()]


def _evidence_limitations(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        return None
    limitations = [item.strip() for item in value]
    if (
        any(
            not item or item not in _EVIDENCE_LIMITATION_CODE_SET
            for item in limitations
        )
        or len(set(limitations)) != len(limitations)
    ):
        return None
    return limitations


def parse_photo_damage_result(
    raw_response: str,
    claim_id: str,
    file_id: str,
    sha256: str,
) -> str:
    value = raw_response.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return _damage_error("invalid_json", claim_id, file_id, sha256)
    if not isinstance(payload, Mapping):
        return _damage_error("response_not_object", claim_id, file_id, sha256)

    boolean_fields = ("vehicle_visible", "target_vehicle_clear", "damage_visible")
    if any(not isinstance(payload.get(name), bool) for name in boolean_fields):
        return _damage_error("invalid_boolean_fields", claim_id, file_id, sha256)
    damaged_parts = _string_list(payload.get("damaged_parts"))
    damage_types = _string_list(payload.get("damage_types"))
    if damaged_parts is None or damage_types is None:
        return _damage_error("invalid_list_fields", claim_id, file_id, sha256)
    evidence_summary = payload.get("evidence_summary")
    if not isinstance(evidence_summary, str) or not evidence_summary.strip():
        return _damage_error("invalid_evidence_summary", claim_id, file_id, sha256)
    evidence_summary = evidence_summary.strip()
    finding_determinate = payload.get("finding_determinate")
    if not isinstance(finding_determinate, bool):
        return _damage_error("invalid_finding_determinate", claim_id, file_id, sha256)
    evidence_limitations = _evidence_limitations(payload.get("evidence_limitations"))
    if evidence_limitations is None:
        return _damage_error("invalid_evidence_limitations", claim_id, file_id, sha256)
    if not finding_determinate and not evidence_limitations:
        return _damage_error(
            "indeterminate_without_evidence_limitation",
            claim_id,
            file_id,
            sha256,
        )
    severity = str(payload.get("severity_hint", "")).strip().lower()
    if severity not in _SEVERITIES:
        return _damage_error("invalid_severity", claim_id, file_id, sha256)
    confidence_value = payload.get("confidence")
    if (
        isinstance(confidence_value, bool)
        or not isinstance(confidence_value, (int, float))
        or not 0.0 <= float(confidence_value) <= 1.0
    ):
        return _damage_error("invalid_confidence", claim_id, file_id, sha256)

    return stable_json(
        {
            "status": "success",
            "claim_id": claim_id,
            "file_id": file_id,
            "sha256": sha256,
            "vehicle_visible": payload["vehicle_visible"],
            "target_vehicle_clear": payload["target_vehicle_clear"],
            "damage_visible": payload["damage_visible"],
            "damaged_parts": damaged_parts,
            "damage_types": damage_types,
            "evidence_summary": evidence_summary,
            "finding_determinate": finding_determinate,
            "evidence_limitations": evidence_limitations,
            "severity_hint": severity,
            "uncertainty_reasons": evidence_limitations,
            "confidence": round(float(confidence_value), 4),
        }
    )


@vane.func(return_dtype="VARCHAR", name="photo_damage_result_json")
def photo_damage_result_json(
    raw_response: str,
    claim_id: str,
    file_id: str,
    sha256: str,
) -> str:
    return parse_photo_damage_result(raw_response, claim_id, file_id, sha256)


def stateless_udf_specs(minio_udfs: MinioUdfs) -> tuple[SqlUdfSpec, ...]:
    return (
        SqlUdfSpec(
            minio_udfs.object_exists,
            "minio_object_exists",
            ("VARCHAR", "VARCHAR"),
        ),
        SqlUdfSpec(
            minio_udfs.object_sha256,
            "minio_object_sha256",
            ("VARCHAR", "VARCHAR"),
        ),
        SqlUdfSpec(
            minio_udfs.photo_quality,
            "photo_quality_json",
            ("VARCHAR", "VARCHAR"),
        ),
        SqlUdfSpec(document_fields_json, "document_fields_json", ("VARCHAR",)),
        SqlUdfSpec(
            document_quality_json,
            "document_quality_json",
            ("VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR", "DOUBLE"),
        ),
        SqlUdfSpec(
            photo_damage_result_json,
            "photo_damage_result_json",
            ("VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR"),
        ),
    )


def _build_rapidocr():
    from rapidocr import RapidOCR

    return RapidOCR()


def _bbox(value: Any, fallback_index: int) -> list[float]:
    if isinstance(value, np.ndarray):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        if len(value) == 4 and all(isinstance(item, (int, float)) for item in value):
            return [round(float(item), 2) for item in value]
        points = [
            point
            for point in value
            if isinstance(point, (list, tuple))
            and len(point) >= 2
            and isinstance(point[0], (int, float))
            and isinstance(point[1], (int, float))
        ]
        if points:
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]
    y = float(fallback_index * 10)
    return [0.0, y, 0.0, y]


def _observation_rows(observations: Any) -> list[Any]:
    if observations is None:
        return []
    texts = getattr(observations, "txts", None) or getattr(observations, "texts", None)
    scores = getattr(observations, "scores", None)
    boxes = getattr(observations, "boxes", None)
    if texts is not None and scores is not None:
        box_values = boxes if boxes is not None else [None] * len(texts)
        return list(zip(texts, scores, box_values))
    if isinstance(observations, Mapping):
        for key in ("text_lines", "results", "data"):
            value = observations.get(key)
            if isinstance(value, list):
                return value
        return []
    if isinstance(observations, tuple) and len(observations) == 2:
        first, second = observations
        if isinstance(first, list) and not isinstance(second, str):
            observations = first
    if isinstance(observations, (list, tuple)):
        return list(observations)
    return []


def _normalize_observation(item: Any, index: int) -> dict[str, Any] | None:
    text: Any = None
    confidence: Any = None
    box: Any = None
    if isinstance(item, Mapping):
        text = item.get("text") or item.get("txt")
        confidence = item.get("confidence", item.get("score"))
        box = item.get("bbox", item.get("box"))
    elif isinstance(item, (list, tuple)) and len(item) >= 3:
        if isinstance(item[0], str):
            text, confidence, box = item[0], item[1], item[2]
        elif isinstance(item[1], str):
            box, text, confidence = item[0], item[1], item[2]
    else:
        text = getattr(item, "text", None)
        confidence = getattr(item, "confidence", getattr(item, "score", None))
        box = getattr(item, "bbox", getattr(item, "box", None))
    if not isinstance(text, str) or not text.strip():
        return None
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = 0.0
    return {
        "text": text.strip(),
        "confidence": round(max(0.0, min(1.0, float(confidence))), 4),
        "bbox": _bbox(box, index),
    }


def normalize_ocr_observations(observations: Any) -> str:
    text_lines = [
        normalized
        for index, item in enumerate(_observation_rows(observations))
        if (normalized := _normalize_observation(item, index)) is not None
    ]
    text_lines.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    if not text_lines:
        return stable_json(
            {
                "status": "unreadable",
                "mean_confidence": 0.0,
                "text_lines": [],
                "failure_reason": "no_text_detected",
            }
        )
    mean_confidence = sum(item["confidence"] for item in text_lines) / len(text_lines)
    return stable_json(
        {
            "status": "success",
            "mean_confidence": round(mean_confidence, 4),
            "text_lines": text_lines,
        }
    )


@vane.cls(
    actor_number=1,
    return_dtype="VARCHAR",
    name="document_ocr_json",
    gpus=0,
)
class DocumentOcrActor:
    def __init__(self, minio_config: MinioConfig, engine_factory=None) -> None:
        self.store = MinioStore(minio_config)
        self._engine_factory = engine_factory or _build_rapidocr
        self.engine = None

    def __call__(self, bucket: str, object_key: str) -> str:
        value = self.store.get_bytes(bucket, object_key)
        try:
            with Image.open(io.BytesIO(value)) as image:
                image.verify()
            with Image.open(io.BytesIO(value)) as image:
                image.load()
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
            return stable_json(
                {
                    "status": "unreadable",
                    "mean_confidence": 0.0,
                    "text_lines": [],
                    "failure_reason": "document_decode_failed",
                    "error_type": type(exc).__name__,
                }
            )
        if self.engine is None:
            self.engine = self._engine_factory()
        observations = self.engine(value)
        return normalize_ocr_observations(observations)
