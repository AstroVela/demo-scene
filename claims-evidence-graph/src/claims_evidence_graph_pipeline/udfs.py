"""Batch UDFs and rule helpers for the claims evidence graph POC."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
from PIL import Image, ImageOps

from claims_evidence_graph_pipeline.contracts import (
    DOCUMENT_EVIDENCE,
    PHOTO_EVIDENCE,
    TableContract,
    stable_json,
)

QUALITY_RULE_VERSION = "photo_quality_v2"


@dataclass(frozen=True)
class BatchUDFSpec:
    """Stable contract for one relation.map_batches processor."""

    name: str
    processor: Any
    output_contract: TableContract

    def duckdb_schema(self) -> dict[str, Any]:
        return self.output_contract.duckdb_schema()


def vane_execution_backend(execution_backend: str) -> str:
    return "subprocess_task" if execution_backend == "local" else execution_backend


def map_batches_with_backend(
    relation: Any,
    udf: Any | BatchUDFSpec,
    *,
    schema: dict[str, Any] | None = None,
    batch_size: int | None = None,
    execution_backend: str = "local",
) -> Any:
    """Run a callable batch processor through DuckDB/Vane relation execution."""
    processor = udf.processor if isinstance(udf, BatchUDFSpec) else udf
    output_schema = schema
    if output_schema is None:
        if not isinstance(udf, BatchUDFSpec):
            raise ValueError("schema is required when udf is not a BatchUDFSpec")
        output_schema = udf.duckdb_schema()
    return relation.map_batches(
        processor,
        schema=output_schema,
        batch_size=batch_size,
        execution_backend=vane_execution_backend(execution_backend),
    )


def run_batch_udf(
    conn: Any,
    input_table: pa.Table,
    spec: BatchUDFSpec,
    *,
    batch_size: int,
    execution_backend: str,
) -> pa.Table:
    """Run a standardized batch UDF spec and return its Arrow output table."""
    return map_batches_with_backend(
        conn.from_arrow(input_table),
        spec,
        batch_size=batch_size,
        execution_backend=execution_backend,
    ).to_arrow_table()


def laplacian_variance(gray: Image.Image) -> float:
    array = np.asarray(gray, dtype=np.float64)
    if array.shape[0] < 3 or array.shape[1] < 3:
        return 0.0
    laplacian = (
        -4.0 * array[1:-1, 1:-1]
        + array[:-2, 1:-1]
        + array[2:, 1:-1]
        + array[1:-1, :-2]
        + array[1:-1, 2:]
    )
    return float(np.var(laplacian))


def quality_score(
    width: int,
    height: int,
    brightness_mean: float,
    brightness_std: float,
    blur_score: float,
) -> tuple[float, list[str]]:
    flags: list[str] = []
    if brightness_mean < 45.0:
        flags.append("too_dark")
    if brightness_mean > 230.0:
        flags.append("too_bright")
    if brightness_std < 28.0:
        flags.append("low_contrast")
    if blur_score < 60.0:
        flags.append("blurry")
    if min(width, height) < 400:
        flags.append("low_resolution")

    score = 1.0
    if "too_dark" in flags or "too_bright" in flags:
        score -= 0.25
    if "low_contrast" in flags:
        score -= 0.25
    if "blurry" in flags:
        score -= 0.35
    if "low_resolution" in flags:
        score -= 0.15
    return max(0.0, min(1.0, score)), flags


def quality_needs_review(score: float, flags: list[str]) -> bool:
    return score < 0.6 or bool(flags)


def average_hash(image: Image.Image, *, size: int = 8) -> str:
    gray = image.convert("L").resize((size, size), Image.Resampling.LANCZOS)
    array = np.asarray(gray, dtype=np.float64)
    threshold = float(np.mean(array))
    bits = ["1" if value >= threshold else "0" for value in array.flatten()]
    return f"{int(''.join(bits), 2):0{size * size // 4}x}"


def photo_decode_error_row(
    *,
    claim_id: str,
    file_id: str,
    file_size: int,
    source_path: str,
    error: Exception,
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "file_id": file_id,
        "decode_ok": False,
        "decode_error": f"{type(error).__name__}: {error}",
        "image_format": None,
        "image_mode": None,
        "image_width": 0,
        "image_height": 0,
        "file_size_bytes": int(file_size),
        "aspect_ratio": None,
        "megapixels": None,
        "brightness_mean": None,
        "brightness_std": None,
        "blur_score": None,
        "quality_score": 0.0,
        "issue_flags_json": stable_json(["decode_error"]),
        "perceptual_hash": None,
        "quality_rule_version": QUALITY_RULE_VERSION,
        "needs_review": True,
        "evidence_node_id": f"evidence:photo_quality:{file_id}",
        "source_path": source_path,
    }


class PhotoQualityBatch:
    """Decode vehicle photos and compute simple auditable quality metrics."""

    def __call__(self, batch: pa.Table) -> pa.Table:
        return self.extract(batch)

    def extract(self, batch: pa.Table) -> pa.Table:
        rows: list[dict[str, Any]] = []
        for claim_id, file_id, file_size, source_path, file_bytes in zip(
            batch["claim_id"].to_pylist(),
            batch["file_id"].to_pylist(),
            batch["file_size_bytes"].to_pylist(),
            batch["absolute_path"].to_pylist(),
            batch["file_bytes"].to_pylist(),
            strict=True,
        ):
            try:
                with Image.open(io.BytesIO(bytes(file_bytes))) as opened_image:
                    image_format = opened_image.format
                    image_mode = opened_image.mode
                    image = ImageOps.exif_transpose(opened_image).convert("RGB")
                    image.load()
            except Exception as exc:
                rows.append(
                    photo_decode_error_row(
                        claim_id=claim_id,
                        file_id=file_id,
                        file_size=int(file_size),
                        source_path=source_path,
                        error=exc,
                    )
                )
                continue
            gray = image.convert("L")
            gray_array = np.asarray(gray, dtype=np.float64)
            brightness_mean = float(np.mean(gray_array))
            brightness_std = float(np.std(gray_array))
            blur_score = laplacian_variance(gray)
            score, flags = quality_score(
                image.width,
                image.height,
                brightness_mean,
                brightness_std,
                blur_score,
            )
            rows.append(
                {
                    "claim_id": claim_id,
                    "file_id": file_id,
                    "decode_ok": True,
                    "decode_error": None,
                    "image_format": image_format,
                    "image_mode": image_mode,
                    "image_width": int(image.width),
                    "image_height": int(image.height),
                    "file_size_bytes": int(file_size),
                    "aspect_ratio": float(image.width / image.height)
                    if image.height
                    else None,
                    "megapixels": float((image.width * image.height) / 1_000_000.0),
                    "brightness_mean": brightness_mean,
                    "brightness_std": brightness_std,
                    "blur_score": blur_score,
                    "quality_score": score,
                    "issue_flags_json": stable_json(flags),
                    "perceptual_hash": average_hash(image),
                    "quality_rule_version": QUALITY_RULE_VERSION,
                    "needs_review": quality_needs_review(score, flags),
                    "evidence_node_id": f"evidence:photo_quality:{file_id}",
                    "source_path": source_path,
                }
            )
        return PHOTO_EVIDENCE.arrow_table(rows)


def valid_bbox(box: Any) -> bool:
    if not isinstance(box, list) or len(box) != 4:
        return False
    try:
        x0, y0, x1, y1 = [float(value) for value in box]
    except (TypeError, ValueError):
        return False
    return x1 > x0 and y1 > y0


def form_text(form: dict[str, Any]) -> str:
    text = str(form.get("text") or "").strip()
    if text:
        return text
    words = form.get("words") or []
    return " ".join(str(word.get("text") or "").strip() for word in words).strip()


class FUNSDDocumentExtractBatch:
    """Expand FUNSD semantic blocks into document evidence rows."""

    def __call__(self, batch: pa.Table) -> pa.Table:
        return self.extract(batch)

    def extract(self, batch: pa.Table) -> pa.Table:
        rows: list[dict[str, Any]] = []
        for claim_id, file_id, source_path, annotation_path in zip(
            batch["claim_id"].to_pylist(),
            batch["file_id"].to_pylist(),
            batch["absolute_path"].to_pylist(),
            batch["annotation_absolute_path"].to_pylist(),
            strict=True,
        ):
            if not annotation_path:
                continue
            annotation_file = Path(annotation_path)
            document_id = Path(source_path).stem
            annotation = json.loads(annotation_file.read_text(encoding="utf-8"))
            for index, form in enumerate(annotation.get("form", [])):
                label = str(form.get("label") or "unknown")
                form_id = form.get("id", index)
                text = form_text(form)
                box = form.get("box")
                rows.append(
                    {
                        "claim_id": claim_id,
                        "file_id": file_id,
                        "document_id": document_id,
                        "field_name": f"{label}_{form_id}",
                        "field_value": text,
                        "field_type": label,
                        "bbox_json": stable_json(box),
                        "source_image_path": source_path,
                        "annotation_path": str(annotation_file),
                        "confidence": 1.0,
                        "needs_review": not text or not valid_bbox(box),
                        "evidence_node_id": (
                            f"evidence:document_span:{file_id}:{form_id}"
                        ),
                    }
                )
        return DOCUMENT_EVIDENCE.arrow_table(rows)


PHOTO_QUALITY_UDF = BatchUDFSpec(
    name="photo_quality",
    processor=PhotoQualityBatch().__call__,
    output_contract=PHOTO_EVIDENCE,
)

FUNSD_DOCUMENT_EXTRACT_UDF = BatchUDFSpec(
    name="funsd_document_extract",
    processor=FUNSDDocumentExtractBatch().__call__,
    output_contract=DOCUMENT_EVIDENCE,
)
