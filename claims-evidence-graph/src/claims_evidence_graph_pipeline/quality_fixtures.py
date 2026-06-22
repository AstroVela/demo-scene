"""Deterministic quality fixture data for the claims evidence graph POC."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

CLAIM_ID = "CLM-QUALITY-001"


@dataclass(frozen=True)
class QualityFixturePaths:
    workspace_root: Path
    data_root: Path
    output_dir: Path
    photo_labels_path: Path


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _textured_image(*, width: int = 640, height: int = 480) -> Image.Image:
    y_index, x_index = np.indices((height, width))
    red = (x_index * 7 + y_index * 3) % 256
    green = (x_index * 5 + y_index * 11) % 256
    blue = (x_index * 13 + y_index * 17) % 256
    array = np.stack([red, green, blue], axis=2).astype("uint8")
    return Image.fromarray(array, "RGB")


def _dark_image() -> Image.Image:
    array = (np.asarray(_textured_image(), dtype=np.float64) * 0.08).astype(
        "uint8"
    )
    return Image.fromarray(array, "RGB")


def _fixture_label(
    *,
    file_id: str,
    usable_for_review: bool,
    needs_reshoot: bool,
) -> dict[str, Any]:
    return {
        "claim_id": CLAIM_ID,
        "file_id": file_id,
        "usable_for_review": usable_for_review,
        "vehicle_visible": True,
        "target_vehicle_clear": True,
        "damage_visible": True,
        "damaged_parts_json": ["door"],
        "damage_types_json": ["scratch"],
        "severity_label": "minor",
        "needs_reshoot": needs_reshoot,
        "labeler_id": "fixture-generator",
        "labeled_at": "2026-06-16T00:00:00Z",
        "adjudication_status": "fixture",
    }


def _claim_file(
    *,
    file_id: str,
    role: str,
    media_type: str,
    path: Path,
    workspace_root: Path,
    annotation_path: Path | None = None,
) -> dict[str, Any]:
    row = {
        "claim_id": CLAIM_ID,
        "file_id": file_id,
        "role": role,
        "media_type": media_type,
        "source_dataset": "claims_quality_fixture",
        "source_url": f"fixture://claims-quality/{file_id}",
        "raw_path": str(path.relative_to(workspace_root)),
        "poc_path": str(path.relative_to(workspace_root)),
        "notes": "deterministic quality fixture",
    }
    if annotation_path is not None:
        row["annotation_raw_path"] = str(annotation_path.relative_to(workspace_root))
        row["annotation_poc_path"] = str(annotation_path.relative_to(workspace_root))
    return row


def build_quality_fixture_workspace(
    workspace_root: Path,
    *,
    data_root: Path | None = None,
    output_dir: Path | None = None,
) -> QualityFixturePaths:
    workspace_root = workspace_root.expanduser().resolve()
    data_root = (
        data_root.expanduser().resolve()
        if data_root is not None
        else workspace_root / "claims-poc-quality-fixtures"
    )
    output_dir = (
        output_dir.expanduser().resolve()
        if output_dir is not None
        else data_root / "outputs"
    )

    image_dir = workspace_root / "raw" / "claims_quality_fixtures" / "images"
    document_dir = workspace_root / "raw" / "claims_quality_fixtures" / "documents"
    annotation_dir = (
        workspace_root / "raw" / "claims_quality_fixtures" / "annotations"
    )
    image_dir.mkdir(parents=True, exist_ok=True)
    document_dir.mkdir(parents=True, exist_ok=True)
    annotation_dir.mkdir(parents=True, exist_ok=True)

    good_path = image_dir / "good.jpg"
    dark_path = image_dir / "dark.jpg"
    blurry_path = image_dir / "blurry.jpg"
    low_res_path = image_dir / "low_res.jpg"
    duplicate_a_path = image_dir / "duplicate_a.jpg"
    duplicate_b_path = image_dir / "duplicate_b.jpg"
    corrupt_path = image_dir / "corrupt.jpg"

    good_image = _textured_image()
    duplicate_image = _textured_image().transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    good_image.save(good_path, quality=95)
    _dark_image().save(dark_path, quality=95)
    good_image.filter(ImageFilter.GaussianBlur(radius=8)).save(
        blurry_path,
        quality=95,
    )
    _textured_image(width=240, height=180).save(low_res_path, quality=95)
    duplicate_image.save(duplicate_a_path, quality=95)
    duplicate_image.save(duplicate_b_path, quality=95)
    corrupt_path.write_bytes(b"not a valid jpeg")

    document_path = document_dir / "document.png"
    annotation_path = annotation_dir / "document.json"
    Image.new("RGB", (640, 480), (245, 245, 245)).save(document_path)
    annotation_path.write_text(
        json.dumps(
            {
                "form": [
                    {
                        "id": 0,
                        "label": "answer",
                        "text": "Estimate ready",
                        "box": [10, 10, 180, 40],
                        "words": [{"text": "Estimate"}, {"text": "ready"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    _write_jsonl(
        data_root / "manifests" / "claims.jsonl",
        [
            {
                "claim_id": CLAIM_ID,
                "scenario": "quality_fixture",
                "description": "Deterministic photo quality edge cases.",
                "is_real_claim": False,
                "source_note": "synthetic local fixture",
            }
        ],
    )
    _write_jsonl(
        data_root / "manifests" / "claim_files.jsonl",
        [
            _claim_file(
                file_id="PHOTO-GOOD",
                role="vehicle_damage_photo",
                media_type="image/jpeg",
                path=good_path,
                workspace_root=workspace_root,
            ),
            _claim_file(
                file_id="PHOTO-DARK",
                role="vehicle_damage_photo",
                media_type="image/jpeg",
                path=dark_path,
                workspace_root=workspace_root,
            ),
            _claim_file(
                file_id="PHOTO-BLURRY",
                role="vehicle_damage_photo",
                media_type="image/jpeg",
                path=blurry_path,
                workspace_root=workspace_root,
            ),
            _claim_file(
                file_id="PHOTO-LOW-RES",
                role="vehicle_damage_photo",
                media_type="image/jpeg",
                path=low_res_path,
                workspace_root=workspace_root,
            ),
            _claim_file(
                file_id="PHOTO-DUPLICATE-A",
                role="vehicle_damage_photo",
                media_type="image/jpeg",
                path=duplicate_a_path,
                workspace_root=workspace_root,
            ),
            _claim_file(
                file_id="PHOTO-DUPLICATE-B",
                role="vehicle_damage_photo",
                media_type="image/jpeg",
                path=duplicate_b_path,
                workspace_root=workspace_root,
            ),
            _claim_file(
                file_id="PHOTO-CORRUPT",
                role="vehicle_damage_photo",
                media_type="image/jpeg",
                path=corrupt_path,
                workspace_root=workspace_root,
            ),
            _claim_file(
                file_id="DOC-QUALITY-001",
                role="scanned_claim_or_estimate_form_proxy",
                media_type="image/png",
                path=document_path,
                workspace_root=workspace_root,
                annotation_path=annotation_path,
            ),
        ],
    )

    photo_labels_path = data_root / "labels" / "photo_labels.jsonl"
    _write_jsonl(
        photo_labels_path,
        [
            _fixture_label(
                file_id="PHOTO-GOOD",
                usable_for_review=True,
                needs_reshoot=False,
            ),
            _fixture_label(
                file_id="PHOTO-DARK",
                usable_for_review=False,
                needs_reshoot=True,
            ),
            _fixture_label(
                file_id="PHOTO-BLURRY",
                usable_for_review=False,
                needs_reshoot=True,
            ),
            _fixture_label(
                file_id="PHOTO-LOW-RES",
                usable_for_review=False,
                needs_reshoot=True,
            ),
            _fixture_label(
                file_id="PHOTO-DUPLICATE-A",
                usable_for_review=True,
                needs_reshoot=False,
            ),
            _fixture_label(
                file_id="PHOTO-DUPLICATE-B",
                usable_for_review=True,
                needs_reshoot=False,
            ),
            _fixture_label(
                file_id="PHOTO-CORRUPT",
                usable_for_review=False,
                needs_reshoot=True,
            ),
        ],
    )

    return QualityFixturePaths(
        workspace_root=workspace_root,
        data_root=data_root,
        output_dir=output_dir,
        photo_labels_path=photo_labels_path,
    )
