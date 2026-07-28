"""Thin Ray-executed Vane adapters around ordinary Python business logic."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import wave
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
from PIL import Image, UnidentifiedImageError
import vane

from .contracts import stable_json, validate_ai_response
from .domain_logic import (
    apply_domain_glossary,
    transcript_knowledge_status,
)


ROLE_AI_SCOPE: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "approved_research": (
        frozenset({"ORR", "TRAE_G3_PLUS", "CASH_RUNWAY", "BLA_STATUS"}),
        frozenset(
            {"COND-EFFICACY", "COND-SAFETY", "COND-RUNWAY", "COND-REGULATORY"}
        ),
    ),
    "company_clinical_announcement": (
        frozenset({"ORR", "TRAE_G3_PLUS", "SUBGROUP_ORR", "DOR", "PFS"}),
        frozenset({"COND-EFFICACY", "COND-SAFETY"}),
    ),
    "audited_financial_update": (
        frozenset({"CASH_RUNWAY"}),
        frozenset({"COND-RUNWAY"}),
    ),
    "company_regulatory_update": (
        frozenset({"BLA_STATUS"}),
        frozenset({"COND-REGULATORY"}),
    ),
    "expert_interview": (
        frozenset({"BLA_STATUS"}),
        frozenset({"COND-REGULATORY"}),
    ),
    "chat_screenshot": (
        frozenset({"TRIAL_STATUS"}),
        frozenset({"COND-REGULATORY"}),
    ),
}


def validate_ai_response_for_role(raw_response: str, source_role: str) -> str:
    try:
        metrics, conditions = ROLE_AI_SCOPE[source_role]
    except KeyError as exc:
        raise ValueError(f"source role cannot enter AI extraction: {source_role}") from exc
    return stable_json(
        validate_ai_response(
            raw_response,
            allowed_metrics=metrics,
            allowed_conditions=conditions,
        )
    )


@vane.func(return_dtype="VARCHAR", name="validate_research_ai_json")
def validate_research_ai_json_udf(raw_response: str, source_role: str) -> str:
    """Stateless Vane Function that delegates to the strict Python contract."""

    return validate_ai_response_for_role(raw_response, source_role)


def _render_pdf_first_page(value: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="fund-research-pdf-") as root:
        source = Path(root) / "source.pdf"
        output_prefix = Path(root) / "page"
        source.write_bytes(value)
        subprocess.run(
            [
                "pdftoppm",
                "-f",
                "1",
                "-singlefile",
                "-png",
                "-r",
                "72",
                str(source),
                str(output_prefix),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        page = output_prefix.with_suffix(".png")
        if not page.exists():
            raise ValueError("pdftoppm did not produce a page image")
        return page.read_bytes()


def _page_image(value: bytes, media_type: str) -> bytes:
    if media_type == "application/pdf":
        return _render_pdf_first_page(value)
    if media_type == "image/png":
        return value
    raise ValueError(f"unsupported OCR media type: {media_type}")


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
        return result if isinstance(result, list) else []
    if isinstance(observations, (list, tuple)):
        return list(observations)
    return []


def _bbox_origin(value: Any, fallback_index: int) -> tuple[float, float]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        points = [
            point
            for point in value
            if isinstance(point, (list, tuple))
            and len(point) >= 2
            and isinstance(point[0], (int, float))
            and isinstance(point[1], (int, float))
        ]
        if points:
            return (
                min(float(point[0]) for point in points),
                min(float(point[1]) for point in points),
            )
        if len(value) == 4 and all(isinstance(item, (int, float)) for item in value):
            return float(value[0]), float(value[1])
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
    score = float(confidence) if isinstance(confidence, (int, float)) else 0.0
    x, y = _bbox_origin(box, index)
    return y, x, " ".join(text.split()), max(0.0, min(1.0, score))


class DocumentOcrBatch:
    """Stateful RapidOCR batch callable; one instance is reused by a Ray actor."""

    def __init__(self, minimum_confidence: float):
        self.minimum_confidence = minimum_confidence
        self._engine = None

    def _load_engine(self):
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def _one(self, value: bytes, media_type: str) -> dict[str, Any]:
        try:
            page = _page_image(value, media_type)
            with Image.open(io.BytesIO(page)) as image:
                image.verify()
        except (
            OSError,
            subprocess.CalledProcessError,
            SyntaxError,
            UnidentifiedImageError,
            ValueError,
        ) as exc:
            return {
                "ocr_status": "quarantined",
                "ocr_text": "",
                "ocr_confidence": 0.0,
                "page_image_bytes": b"",
                "error_code": f"OCR_DECODE:{type(exc).__name__}",
            }
        try:
            observations = self._load_engine()(page)
            rows = [
                normalized
                for index, item in enumerate(_ocr_rows(observations))
                if (normalized := _normalize_ocr_row(item, index)) is not None
            ]
            rows.sort(key=lambda row: (row[0], row[1]))
        except Exception as exc:
            return {
                "ocr_status": "failed",
                "ocr_text": "",
                "ocr_confidence": 0.0,
                "page_image_bytes": page,
                "error_code": f"OCR_ENGINE:{type(exc).__name__}",
            }
        if not rows:
            return {
                "ocr_status": "quarantined",
                "ocr_text": "",
                "ocr_confidence": 0.0,
                "page_image_bytes": page,
                "error_code": "OCR_NO_TEXT",
            }
        confidence = sum(row[3] for row in rows) / len(rows)
        return {
            "ocr_status": (
                "succeeded" if confidence >= self.minimum_confidence else "quarantined"
            ),
            "ocr_text": "\n".join(row[2] for row in rows),
            "ocr_confidence": round(confidence, 4),
            "page_image_bytes": page,
            "error_code": (
                None if confidence >= self.minimum_confidence else "OCR_LOW_CONFIDENCE"
            ),
        }

    def __call__(self, batch: pa.Table) -> pa.Table:
        results = [
            self._one(bytes(value or b""), str(media_type or ""))
            for value, media_type in zip(
                batch["object_bytes"].to_pylist(),
                batch["media_type"].to_pylist(),
                strict=True,
            )
        ]
        source_ids = batch["source_id"].to_pylist()
        locators = [
            f"minio://{bucket}/{key}#page=1"
            for bucket, key in zip(
                batch["bucket"].to_pylist(),
                batch["object_key"].to_pylist(),
                strict=True,
            )
        ]
        return pa.table(
            {
                "source_id": pa.array(source_ids, type=pa.string()),
                "ocr_status": pa.array(
                    [row["ocr_status"] for row in results], type=pa.string()
                ),
                "ocr_text": pa.array(
                    [row["ocr_text"] for row in results], type=pa.string()
                ),
                "ocr_confidence": pa.array(
                    [row["ocr_confidence"] for row in results], type=pa.float64()
                ),
                "source_locator": pa.array(locators, type=pa.string()),
                "page_image_bytes": pa.array(
                    [row["page_image_bytes"] for row in results], type=pa.binary()
                ),
                "error_code": pa.array(
                    [row["error_code"] for row in results], type=pa.string()
                ),
            }
        )


class AsrServiceBatch:
    """Stateful Whisper HTTP client reused by one Ray actor."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        language: str,
        timeout_seconds: float,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.language = language
        self.timeout_seconds = timeout_seconds
        self._client = None

    def _load_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout_seconds,
                trust_env=False,
            )
        return self._client

    @staticmethod
    def _duration(value: bytes) -> float:
        with wave.open(io.BytesIO(value), "rb") as audio:
            return audio.getnframes() / float(audio.getframerate())

    def _transcribe(self, value: bytes) -> str:
        response = self._load_client().post(
            f"{self.base_url}/audio/transcriptions",
            files={"file": ("meeting.wav", value, "audio/wav")},
            data={
                "model": self.model,
                "language": self.language,
                "task": "transcribe",
                "temperature": "0",
                "max_tokens": "256",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if set(payload) != {"text"} or not isinstance(payload["text"], str):
            raise ValueError("ASR service response must contain only text")
        text = " ".join(payload["text"].split())
        if not text:
            raise ValueError("ASR service returned empty text")
        return text

    def __call__(self, batch: pa.Table) -> pa.Table:
        source_ids = batch["source_id"].to_pylist()
        audio_values = batch["object_bytes"].to_pylist()
        buckets = batch["bucket"].to_pylist()
        keys = batch["object_key"].to_pylist()
        raw_texts: list[str] = []
        durations: list[float] = []
        locators: list[str] = []
        for value, bucket, key in zip(audio_values, buckets, keys, strict=True):
            audio = bytes(value or b"")
            duration = self._duration(audio)
            raw_texts.append(self._transcribe(audio))
            durations.append(round(duration, 3))
            locators.append(f"minio://{bucket}/{key}#t=0.000,{duration:.3f}")
        return pa.table(
            {
                "source_id": pa.array(source_ids, type=pa.string()),
                "segment_id": pa.array(
                    [f"{source_id}-SEG-001" for source_id in source_ids],
                    type=pa.string(),
                ),
                "start_seconds": pa.array([0.0] * len(source_ids), type=pa.float64()),
                "end_seconds": pa.array(durations, type=pa.float64()),
                "raw_text": pa.array(raw_texts, type=pa.string()),
                "language": pa.array([self.language] * len(source_ids), type=pa.string()),
                "asr_confidence": pa.array([None] * len(source_ids), type=pa.float64()),
                "confidence_method": pa.array(
                    ["service_not_exposed"] * len(source_ids), type=pa.string()
                ),
                "source_locator": pa.array(locators, type=pa.string()),
            }
        )


def configured_asr_actor(
    *,
    base_url: str,
    model: str,
    language: str,
    timeout_seconds: float,
):
    """Return a no-argument callable class accepted by Vane actor backends."""

    class ConfiguredAsrActor(AsrServiceBatch):
        def __init__(self):
            super().__init__(
                base_url=base_url,
                model=model,
                language=language,
                timeout_seconds=timeout_seconds,
            )

    return ConfiguredAsrActor


def configured_ocr_actor(*, minimum_confidence: float):
    """Return a no-argument RapidOCR actor class with captured configuration."""

    class ConfiguredOcrActor(DocumentOcrBatch):
        def __init__(self):
            super().__init__(minimum_confidence)

    return ConfiguredOcrActor


class GlossaryCorrectionBatch:
    """Thin stateless adapter over :func:`apply_domain_glossary`."""

    def __init__(self, terms: Sequence[Mapping[str, Any]]):
        self.terms = [dict(row) for row in terms]

    def __call__(self, batch: pa.Table) -> pa.Table:
        rows = batch.to_pylist()
        corrected_texts = []
        correction_json = []
        statuses = []
        for row in rows:
            corrected, events = apply_domain_glossary(
                str(row["raw_text"]),
                self.terms,
                source_id=str(row["source_id"]),
                segment_id=str(row["segment_id"]),
            )
            corrected_texts.append(corrected)
            correction_json.append(json.dumps(events, ensure_ascii=False, sort_keys=True))
            statuses.append(transcript_knowledge_status(corrected))
        return batch.append_column(
            "corrected_text", pa.array(corrected_texts, type=pa.string())
        ).append_column(
            "corrections_json", pa.array(correction_json, type=pa.string())
        ).append_column(
            "knowledge_status", pa.array(statuses, type=pa.string())
        )


def configured_glossary_function(terms: Sequence[Mapping[str, Any]]):
    """Return the plain function shape required by Vane Ray task backends."""

    adapter = GlossaryCorrectionBatch(terms)

    def apply_glossary_batch(batch: pa.Table) -> pa.Table:
        return adapter(batch)

    return apply_glossary_batch
