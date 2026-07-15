#!/usr/bin/env python3
"""Parse and verify public media assets used by enterprise evidence governance."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pyarrow as pa

from _common import tokenize


REPO_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_MODALITIES = ("audio", "document", "image", "text")
MEDIA_METRICS_TYPE = pa.struct(
    [
        pa.field("width", pa.int64()),
        pa.field("height", pa.int64()),
        pa.field("duration_seconds", pa.float64()),
        pa.field("sample_rate", pa.int64()),
    ]
)
SVG_DIMENSION_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)(?:px)?\s*$")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def resolve_repo_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    resolved = path.resolve()
    repo_root = REPO_ROOT.resolve()
    if resolved != repo_root and repo_root not in resolved.parents:
        raise ValueError(f"snapshot path must stay under {REPO_ROOT}: {value}")
    return resolved


def verify_public_asset_snapshot(
    asset_catalog: Path,
    snapshot_path: Path,
) -> dict[str, Any]:
    if not snapshot_path.is_file():
        raise FileNotFoundError(f"public asset snapshot does not exist: {snapshot_path}")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(snapshot, dict):
        raise ValueError("public asset snapshot must be a JSON object")

    assets = snapshot.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ValueError("public asset snapshot must contain a non-empty assets list")
    if any(not isinstance(asset, dict) for asset in assets):
        raise ValueError("public asset snapshot contains a non-object asset entry")

    declared_catalog = resolve_repo_path(str(snapshot.get("asset_catalog") or ""))
    source_manifest = resolve_repo_path(str(snapshot.get("source_manifest") or ""))
    failures: list[str] = []
    if declared_catalog != asset_catalog.resolve():
        failures.append(
            "asset catalog path mismatch: "
            f"expected {declared_catalog}, got {asset_catalog.resolve()}"
        )
    if snapshot.get("records") != len(assets):
        failures.append(
            "asset count mismatch: "
            f"expected {snapshot.get('records')!r}, got {len(assets)}"
        )
    actual_modalities = sorted(
        {str(asset.get("modality") or "") for asset in assets}
    )
    if snapshot.get("modalities") != actual_modalities:
        failures.append(
            "asset modalities mismatch: "
            f"expected {snapshot.get('modalities')!r}, got {actual_modalities!r}"
        )

    checks = [
        (
            "asset catalog",
            asset_catalog.resolve(),
            str(snapshot.get("asset_catalog_sha256") or ""),
            None,
        ),
        (
            "source manifest",
            source_manifest,
            str(snapshot.get("source_manifest_sha256") or ""),
            None,
        ),
    ]
    asset_ids: set[str] = set()
    for asset in assets:
        asset_id = str(asset.get("record_id") or "")
        if not asset_id or asset_id in asset_ids:
            failures.append(f"invalid or duplicate snapshot asset ID: {asset_id!r}")
        asset_ids.add(asset_id)
        checks.append(
            (
                f"asset {asset_id or '<unknown>'}",
                resolve_repo_path(str(asset.get("snapshot_path") or "")),
                str(asset.get("sha256") or ""),
                asset.get("byte_size"),
            )
        )

    for label, path, expected_hash, expected_bytes in checks:
        if not path.is_file():
            failures.append(f"{label} is missing: {path}")
            continue
        actual_hash = file_sha256(path)
        if not expected_hash or actual_hash != expected_hash:
            failures.append(
                f"{label} SHA-256 mismatch: expected {expected_hash}, got {actual_hash}"
            )
        if expected_bytes is not None and path.stat().st_size != int(expected_bytes):
            failures.append(
                f"{label} byte-size mismatch: expected {expected_bytes}, "
                f"got {path.stat().st_size}"
            )
    if failures:
        raise ValueError("invalid public asset snapshot: " + "; ".join(failures))

    return {
        "status": "verified",
        "metadata": display_path(snapshot_path),
        "metadata_sha256": file_sha256(snapshot_path),
        "asset_catalog": display_path(asset_catalog),
        "asset_catalog_sha256": file_sha256(asset_catalog),
        "source_manifest": display_path(source_manifest),
        "source_manifest_sha256": file_sha256(source_manifest),
        "asset_rows": len(assets),
    }


def decode_payload(row: dict[str, Any]) -> bytes:
    content_path = str(row.get("content_path") or "").strip()
    if content_path:
        path = Path(content_path)
        if not path.is_absolute():
            path = REPO_ROOT / path
        payload = path.read_bytes()
    else:
        encoded = str(row.get("content_base64") or "").strip()
        if encoded:
            payload = base64.b64decode(encoded, validate=True)
        else:
            payload = str(row.get("text") or "").encode("utf-8")

    expected_sha256 = str(row.get("expected_sha256") or "").strip().lower()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise ValueError(
            f"payload SHA-256 mismatch for {row.get('record_id')}: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
    return payload


def parse_svg_dimension(value: str | None) -> int | None:
    if not value:
        return None
    match = SVG_DIMENSION_RE.fullmatch(value)
    if not match:
        return None
    return int(float(match.group(1)))


def svg_dimensions(payload: bytes) -> tuple[int | None, int | None]:
    root = ET.fromstring(payload)
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise ET.ParseError("root element is not svg")
    width = parse_svg_dimension(root.attrib.get("width"))
    height = parse_svg_dimension(root.attrib.get("height"))
    if width is not None and height is not None:
        return width, height

    view_box = root.attrib.get("viewBox", "").replace(",", " ").split()
    if len(view_box) == 4:
        try:
            return width or int(float(view_box[2])), height or int(float(view_box[3]))
        except ValueError:
            pass
    return width, height


def empty_metrics() -> dict[str, int | float | None]:
    return {
        "width": None,
        "height": None,
        "duration_seconds": None,
        "sample_rate": None,
    }


def decode_utf8(payload: bytes, flags: list[str]) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        flags.append("invalid_utf8")
        return payload.decode("utf-8", errors="replace")


def build_asset_feature(
    row: dict[str, Any],
    *,
    payload: bytes,
    content_text: str,
    flags: list[str],
    quality_score: float,
    metrics: dict[str, int | float | None],
) -> dict[str, Any]:
    if not str(row.get("license_id") or "").strip():
        flags.append("missing_license")
    quality_score = round(max(0.0, quality_score - 0.25 * ("missing_license" in flags)), 3)
    decision = "accepted" if not flags and quality_score >= 0.8 else "rejected"
    return {
        "content_text": content_text,
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "byte_size": len(payload),
        "token_count": len(tokenize(content_text)),
        "decision": decision,
        "risk_flags": flags,
        "media_metrics": metrics,
    }


def process_document(row: dict[str, Any]) -> dict[str, Any]:
    payload = decode_payload(row)
    flags: list[str] = []
    text = decode_utf8(payload, flags).strip()
    if not text:
        flags.append("empty_document")
    return build_asset_feature(
        row,
        payload=payload,
        content_text=text,
        flags=flags,
        quality_score=1.0 if text else 0.0,
        metrics=empty_metrics(),
    )


def process_image(row: dict[str, Any]) -> dict[str, Any]:
    payload = decode_payload(row)
    flags: list[str] = []
    metrics = empty_metrics()
    if row["mime_type"] == "image/svg+xml":
        try:
            width, height = svg_dimensions(payload)
            metrics["width"] = width
            metrics["height"] = height
            if not width or not height:
                flags.append("missing_dimensions")
            elif width < 512 or height < 512:
                flags.append("low_resolution")
        except ET.ParseError:
            flags.append("invalid_image")
    else:
        flags.append("invalid_image")

    return build_asset_feature(
        row,
        payload=payload,
        content_text=str(row.get("text") or ""),
        flags=flags,
        quality_score=1.0 - 0.5 * len(flags),
        metrics=metrics,
    )


def process_audio(row: dict[str, Any]) -> dict[str, Any]:
    payload = decode_payload(row)
    flags: list[str] = []
    metrics = empty_metrics()
    try:
        with wave.open(io.BytesIO(payload), "rb") as audio_file:
            sample_rate = audio_file.getframerate()
            if sample_rate <= 0:
                raise wave.Error("sample rate must be greater than zero")
            duration = audio_file.getnframes() / float(sample_rate)
            metrics["duration_seconds"] = round(duration, 4)
            metrics["sample_rate"] = sample_rate
            if duration < 0.005:
                flags.append("audio_too_short")
    except (EOFError, wave.Error):
        flags.append("invalid_audio")

    return build_asset_feature(
        row,
        payload=payload,
        content_text=str(row.get("text") or ""),
        flags=flags,
        quality_score=1.0 - 0.5 * len(flags),
        metrics=metrics,
    )


def process_text(row: dict[str, Any]) -> dict[str, Any]:
    payload = decode_payload(row)
    flags: list[str] = []
    text = " ".join(decode_utf8(payload, flags).split())
    if len(tokenize(text)) < 4:
        flags.append("text_too_short")
    return build_asset_feature(
        row,
        payload=payload,
        content_text=text,
        flags=flags,
        quality_score=1.0 - 0.4 * len(flags),
        metrics=empty_metrics(),
    )


def validate_modalities(raw_assets: Any) -> list[str]:
    modalities = [
        row[0]
        for row in raw_assets.project("modality")
        .distinct()
        .order("modality")
        .fetchall()
    ]
    unsupported = sorted(set(modalities) - set(SUPPORTED_MODALITIES))
    if unsupported:
        raise ValueError("unsupported asset modalities: " + ", ".join(unsupported))
    return modalities


def project_raw_assets(input_relation: Any) -> Any:
    columns = set(input_relation.columns)
    content_path = (
        "coalesce(content_path, '') as content_path"
        if "content_path" in columns
        else "'' as content_path"
    )
    expected_sha256 = (
        "coalesce(expected_sha256, '') as expected_sha256"
        if "expected_sha256" in columns
        else "'' as expected_sha256"
    )
    return input_relation.project(
        f"""
        record_id,
        modality,
        source_uri,
        coalesce(license_id, '') as license_id,
        split,
        mime_type,
        coalesce(text, '') as text,
        {content_path},
        {expected_sha256},
        coalesce(content_base64, '') as content_base64,
        coalesce(metadata_json, '{{}}') as metadata_json
        """
    )
