"""Vane-callable functions and deterministic helpers for audit evidence."""

from __future__ import annotations

import json
import math
import io
import re
from typing import Any, Mapping

from PIL import Image, UnidentifiedImageError
import pyarrow as pa
import vane

from .config import MinioConfig
from .minio_store import MinioStore


AI_FACT_FIELDS = frozenset(
    {
        "document_type",
        "expert_id",
        "supplier_name",
        "recommended",
        "participated",
        "recused",
        "evidence_quote",
        "confidence",
    }
)
_DOCUMENT_TYPES = {"recommendation_record", "committee_minutes"}
_EXPERT_ID = re.compile(r"^EXP-[0-9]{3}$")
_EVIDENCE_PLACEHOLDERS = {"图片原文", "证据原文", "原文", "n/a", "unknown"}
_JSON_FENCE = re.compile(
    r"\A```json[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n```[ \t]*\Z",
    re.IGNORECASE,
)


class AuditFactContractError(ValueError):
    """Raised when a multimodal response violates the deterministic contract."""


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuditFactContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise AuditFactContractError(f"invalid JSON constant: {value}")


def _strict_json_object(raw_response: str) -> Mapping[str, Any]:
    if not isinstance(raw_response, str):
        raise AuditFactContractError("AI fact response must be a valid JSON object")
    candidate = raw_response.strip()
    fenced = _JSON_FENCE.fullmatch(candidate)
    if fenced is not None:
        candidate = fenced.group("body")
    try:
        payload = json.loads(
            candidate,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_invalid_constant,
        )
    except AuditFactContractError:
        raise
    except (json.JSONDecodeError, TypeError, UnicodeError, ValueError) as exc:
        raise AuditFactContractError(
            "AI fact response must be a valid JSON object"
        ) from exc
    if not isinstance(payload, Mapping):
        raise AuditFactContractError("AI fact response must be a valid JSON object")
    return payload


def _normalized_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise AuditFactContractError(f"{field} must be non-empty text")
    result = re.sub(r"\s+", " ", value).strip()
    if not result:
        raise AuditFactContractError(f"{field} must be non-empty text")
    return result


def _nullable_boolean(value: Any, field: str) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise AuditFactContractError(f"{field} must be boolean or null")
    return value


def validate_audit_fact_json(raw_response: str) -> str:
    """Validate one model response and return stable canonical JSON."""

    payload = _strict_json_object(raw_response)
    if set(payload) != AI_FACT_FIELDS:
        missing = sorted(AI_FACT_FIELDS - set(payload))
        extra = sorted(set(payload) - AI_FACT_FIELDS)
        raise AuditFactContractError(
            f"AI fact response has wrong fields; missing={missing}, extra={extra}"
        )

    document_type = payload["document_type"]
    if document_type not in _DOCUMENT_TYPES:
        raise AuditFactContractError("document_type is invalid")
    expert_id = _normalized_text(payload["expert_id"], "expert_id").upper()
    if not _EXPERT_ID.fullmatch(expert_id):
        raise AuditFactContractError("expert_id must match EXP-NNN")
    evidence_quote = _normalized_text(payload["evidence_quote"], "evidence_quote")
    if evidence_quote.lower() in _EVIDENCE_PLACEHOLDERS:
        raise AuditFactContractError("evidence_quote must not be a placeholder")
    confidence_value = payload["confidence"]
    if isinstance(confidence_value, bool) or not isinstance(confidence_value, (int, float)):
        raise AuditFactContractError("confidence must be numeric")
    confidence = float(confidence_value)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise AuditFactContractError("confidence must be between 0 and 1")

    supplier_value = payload["supplier_name"]
    supplier_name = (
        None
        if supplier_value is None
        else _normalized_text(supplier_value, "supplier_name")
    )
    recommended = _nullable_boolean(payload["recommended"], "recommended")
    participated = _nullable_boolean(payload["participated"], "participated")
    recused = _nullable_boolean(payload["recused"], "recused")

    if document_type == "recommendation_record":
        if supplier_name is None:
            raise AuditFactContractError(
                "recommendation_record supplier_name must be non-null"
            )
        if recommended is None:
            raise AuditFactContractError(
                "recommendation_record recommended must be boolean"
            )
        if participated is not None or recused is not None:
            raise AuditFactContractError(
                "recommendation_record participation fields must be null"
            )
    else:
        if supplier_name is not None:
            raise AuditFactContractError("committee_minutes supplier_name must be null")
        if recommended is not None:
            raise AuditFactContractError("committee_minutes recommended must be null")
        if participated is None:
            raise AuditFactContractError(
                "committee_minutes participated must be boolean"
            )
        if recused is None:
            raise AuditFactContractError("committee_minutes recused must be boolean")

    return stable_json(
        {
            "document_type": document_type,
            "expert_id": expert_id,
            "supplier_name": supplier_name,
            "recommended": recommended,
            "participated": participated,
            "recused": recused,
            "evidence_quote": evidence_quote,
            "confidence": round(confidence, 4),
        }
    )


@vane.func(return_dtype="VARCHAR", name="validate_audit_fact_json")
def validate_audit_fact_json_udf(raw_response: str) -> str:
    return validate_audit_fact_json(raw_response)


def build_rapidocr():
    """Construct RapidOCR lazily inside the Vane actor process."""

    from rapidocr import RapidOCR

    return RapidOCR()


def _ocr_rows(observations: Any) -> list[Any]:
    if observations is None:
        return []
    texts = getattr(observations, "txts", None)
    if texts is None:
        texts = getattr(observations, "texts", None)
    scores = getattr(observations, "scores", None)
    boxes = getattr(observations, "boxes", None)
    if texts is not None and scores is not None:
        box_values = boxes if boxes is not None else [None] * len(texts)
        return list(zip(box_values, texts, scores))
    if isinstance(observations, Mapping):
        for key in ("text_lines", "results", "data"):
            value = observations.get(key)
            if isinstance(value, list):
                return value
        return []
    if isinstance(observations, tuple) and len(observations) == 2:
        result, _metadata = observations
        if isinstance(result, list):
            return result
    if isinstance(observations, (list, tuple)):
        return list(observations)
    return []


def _bbox_origin(value: Any, fallback_index: int) -> tuple[float, float]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        if len(value) == 4 and all(isinstance(item, (int, float)) for item in value):
            return float(value[0]), float(value[1])
        points = [
            point
            for point in value
            if isinstance(point, (list, tuple))
            and len(point) >= 2
            and isinstance(point[0], (int, float))
            and isinstance(point[1], (int, float))
        ]
        if points:
            return min(float(point[0]) for point in points), min(
                float(point[1]) for point in points
            )
    return 0.0, float(fallback_index)


def _normalize_ocr_row(item: Any, index: int) -> tuple[float, float, str, float] | None:
    text: Any = None
    confidence: Any = None
    box: Any = None
    if isinstance(item, Mapping):
        text = item.get("text", item.get("txt"))
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
    x, y = _bbox_origin(box, index)
    return (
        y,
        x,
        re.sub(r"\s+", " ", text).strip(),
        max(0.0, min(1.0, float(confidence))),
    )


def _unreadable_ocr(error: str) -> str:
    return stable_json(
        {
            "status": "unreadable",
            "full_text": "",
            "mean_confidence": 0.0,
            "text_line_count": 0,
            "error": error,
        }
    )


def normalize_ocr_observations(observations: Any) -> str:
    rows = [
        normalized
        for index, item in enumerate(_ocr_rows(observations))
        if (normalized := _normalize_ocr_row(item, index)) is not None
    ]
    rows.sort(key=lambda item: (item[0], item[1]))
    if not rows:
        return _unreadable_ocr("no_text_detected")
    return stable_json(
        {
            "status": "success",
            "full_text": "\n".join(item[2] for item in rows),
            "mean_confidence": round(
                sum(item[3] for item in rows) / len(rows),
                4,
            ),
            "text_line_count": len(rows),
            "error": None,
        }
    )


@vane.cls(
    actor_number=1,
    return_dtype="VARCHAR",
    name="evidence_ocr_json",
    gpus=0,
)
class EvidenceOcrActor:
    """Stateful MinIO image OCR function that initializes one reusable engine."""

    def __init__(
        self,
        minio_config: MinioConfig,
        engine_factory=None,
        store_factory=MinioStore,
    ) -> None:
        self.store = store_factory(minio_config)
        self.engine = (engine_factory or build_rapidocr)()

    def __call__(self, bucket: str, object_key: str) -> str:
        try:
            value = self.store.get_bytes(bucket, object_key)
        except Exception as exc:
            return _unreadable_ocr(f"object_read_failed:{type(exc).__name__}")
        try:
            with Image.open(io.BytesIO(value)) as image:
                image.verify()
        except (OSError, UnidentifiedImageError, SyntaxError, ValueError):
            return _unreadable_ocr("image_decode_failed")
        try:
            return normalize_ocr_observations(self.engine(value))
        except Exception as exc:
            return _unreadable_ocr(f"ocr_engine_failed:{type(exc).__name__}")


EVIDENCE_OCR_BATCH_SCHEMA = {
    "project_id": "VARCHAR",
    "file_id": "VARCHAR",
    "role": "VARCHAR",
    "bucket": "VARCHAR",
    "object_key": "VARCHAR",
    "media_type": "VARCHAR",
    "ocr_json": "VARCHAR",
    "ocr_status": "VARCHAR",
    "ocr_text": "VARCHAR",
    "ocr_confidence": "DOUBLE",
    "ocr_text_line_count": "INTEGER",
}
_EVIDENCE_OCR_ARROW_SCHEMA = pa.schema(
    [
        ("project_id", pa.string()),
        ("file_id", pa.string()),
        ("role", pa.string()),
        ("bucket", pa.string()),
        ("object_key", pa.string()),
        ("media_type", pa.string()),
        ("ocr_json", pa.string()),
        ("ocr_status", pa.string()),
        ("ocr_text", pa.string()),
        ("ocr_confidence", pa.float64()),
        ("ocr_text_line_count", pa.int32()),
    ]
)


def build_evidence_ocr_batch_actor(minio_config: MinioConfig) -> type:
    """Build a zero-argument batch actor for Vane's active Runner backend."""

    row_actor_class = EvidenceOcrActor.user_class

    class EvidenceOcrBatchActor:
        def __init__(self) -> None:
            self.actor = row_actor_class(minio_config)

        def __call__(self, batch: Any) -> pa.RecordBatch:
            rows = []
            columns = {
                name: batch.column(name).to_pylist()
                for name in (
                    "project_id",
                    "file_id",
                    "role",
                    "bucket",
                    "object_key",
                    "media_type",
                )
            }
            for index in range(batch.num_rows):
                ocr_json = self.actor(
                    columns["bucket"][index],
                    columns["object_key"][index],
                )
                payload = json.loads(ocr_json)
                rows.append(
                    {
                        **{name: values[index] for name, values in columns.items()},
                        "ocr_json": ocr_json,
                        "ocr_status": payload.get("status"),
                        "ocr_text": payload.get("full_text"),
                        "ocr_confidence": payload.get("mean_confidence"),
                        "ocr_text_line_count": payload.get("text_line_count"),
                    }
                )
            return pa.RecordBatch.from_pylist(
                rows,
                schema=_EVIDENCE_OCR_ARROW_SCHEMA,
            )

    return EvidenceOcrBatchActor
