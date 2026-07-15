#!/usr/bin/env python3
"""Prepare typed multimodal assets for a governed training-data release."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa
import vane

SOURCE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SOURCE_DIR.parent
for import_path in (REPO_ROOT, SOURCE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))
existing_pythonpath = os.environ.get("PYTHONPATH")
pythonpath_entries = [str(REPO_ROOT), str(SOURCE_DIR)]
if existing_pythonpath:
    paths = existing_pythonpath.split(os.pathsep)
    missing_paths = [path for path in pythonpath_entries if path not in paths]
    if missing_paths:
        os.environ["PYTHONPATH"] = os.pathsep.join(missing_paths + paths)
else:
    os.environ["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

from _common import (
    PUBLIC_BACKEND_CHOICES,
    backend_metadata_entry,
    batch_udf_options,
    merge_backend_metadata,
    positive_int,
    require_local_relation_runner,
    table_from_rows,
    tokenize,
    write_json,
)


DATA_DIR = REPO_ROOT / "data" / "multimodal_training_data"
DEFAULT_INPUT = DATA_DIR / "training_assets.csv"
SYNTHETIC_INPUT = DATA_DIR / "synthetic_training_assets.csv"
PUBLIC_SOURCE_MANIFEST = DATA_DIR / "public_sources.csv"
PUBLIC_SNAPSHOT_METADATA = DATA_DIR / "public_snapshot.json"
DEFAULT_OUTPUT_DIR = Path("output/multimodal_training_data")
MODULE_NAME = "src.multimodal_training_data"
FEATURE_SCHEMA_VERSION = 2
SUPPORTED_MODALITIES = ("audio", "document", "image", "text")
MEDIA_METRICS_TYPE = pa.struct(
    [
        pa.field("width", pa.int64()),
        pa.field("height", pa.int64()),
        pa.field("duration_seconds", pa.float64()),
        pa.field("sample_rate", pa.int64()),
    ]
)
TRAINING_FEATURE_SCHEMA = {
    "record_id": "VARCHAR",
    "modality": "VARCHAR",
    "source_uri": "VARCHAR",
    "license_id": "VARCHAR",
    "split": "VARCHAR",
    "mime_type": "VARCHAR",
    "content_text": "VARCHAR",
    "content_sha256": "VARCHAR",
    "byte_size": "BIGINT",
    "token_count": "BIGINT",
    "quality_score": "DOUBLE",
    "decision": "VARCHAR",
    "risk_flags": "VARCHAR[]",
    "media_metrics": (
        "STRUCT(width BIGINT, height BIGINT, duration_seconds DOUBLE, "
        "sample_rate BIGINT)"
    ),
    "feature_json": "VARCHAR",
}
TRAINING_FEATURE_ARROW_SCHEMA = {
    "record_id": pa.string(),
    "modality": pa.string(),
    "source_uri": pa.string(),
    "license_id": pa.string(),
    "split": pa.string(),
    "mime_type": pa.string(),
    "content_text": pa.string(),
    "content_sha256": pa.string(),
    "byte_size": pa.int64(),
    "token_count": pa.int64(),
    "quality_score": pa.float64(),
    "decision": pa.string(),
    "risk_flags": pa.list_(pa.string()),
    "media_metrics": MEDIA_METRICS_TYPE,
    "feature_json": pa.string(),
}
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

    declared_catalog = resolve_repo_path(str(snapshot.get("training_manifest") or ""))
    source_manifest = resolve_repo_path(str(snapshot.get("source_manifest") or ""))
    failures: list[str] = []
    if declared_catalog != asset_catalog.resolve():
        failures.append(
            "training manifest path mismatch: "
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
            "training manifest",
            asset_catalog.resolve(),
            str(snapshot.get("training_manifest_sha256") or ""),
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
        "training_manifest": display_path(asset_catalog),
        "training_manifest_sha256": file_sha256(asset_catalog),
        "source_manifest": display_path(source_manifest),
        "source_manifest_sha256": file_sha256(source_manifest),
        "asset_rows": len(assets),
    }


def validate_input_path(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"training input file does not exist: {path}")
    return path


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


def build_feature_row(
    row: dict[str, Any],
    *,
    payload: bytes,
    content_text: str,
    flags: list[str],
    quality_score: float,
    metrics: dict[str, int | float | None],
    features: dict[str, Any],
) -> dict[str, Any]:
    if not str(row.get("license_id") or "").strip():
        flags.append("missing_license")
    quality_score = round(max(0.0, quality_score - 0.25 * ("missing_license" in flags)), 3)
    decision = "accepted" if not flags and quality_score >= 0.8 else "rejected"
    return {
        "record_id": row["record_id"],
        "modality": row["modality"],
        "source_uri": row["source_uri"],
        "license_id": row.get("license_id") or "",
        "split": row["split"],
        "mime_type": row["mime_type"],
        "content_text": content_text,
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "byte_size": len(payload),
        "token_count": len(tokenize(content_text)),
        "quality_score": quality_score,
        "decision": decision,
        "risk_flags": flags,
        "media_metrics": metrics,
        "feature_json": json.dumps(features, sort_keys=True),
    }


def process_document(row: dict[str, Any]) -> dict[str, Any]:
    payload = decode_payload(row)
    flags: list[str] = []
    text = decode_utf8(payload, flags).strip()
    if not text:
        flags.append("empty_document")
    features = {
        "metadata": json.loads(row["metadata_json"] or "{}"),
        "line_count": len(text.splitlines()),
    }
    return build_feature_row(
        row,
        payload=payload,
        content_text=text,
        flags=flags,
        quality_score=1.0 if text else 0.0,
        metrics=empty_metrics(),
        features=features,
    )


def process_image(row: dict[str, Any]) -> dict[str, Any]:
    payload = decode_payload(row)
    flags: list[str] = []
    metrics = empty_metrics()
    image_format = "unknown"
    if row["mime_type"] == "image/svg+xml":
        try:
            width, height = svg_dimensions(payload)
            image_format = "svg"
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

    return build_feature_row(
        row,
        payload=payload,
        content_text=str(row.get("text") or ""),
        flags=flags,
        quality_score=1.0 - 0.5 * len(flags),
        metrics=metrics,
        features={
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "format": image_format,
        },
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

    return build_feature_row(
        row,
        payload=payload,
        content_text=str(row.get("text") or ""),
        flags=flags,
        quality_score=1.0 - 0.5 * len(flags),
        metrics=metrics,
        features={"metadata": json.loads(row["metadata_json"] or "{}")},
    )


def process_text(row: dict[str, Any]) -> dict[str, Any]:
    payload = decode_payload(row)
    flags: list[str] = []
    text = " ".join(decode_utf8(payload, flags).split())
    if len(tokenize(text)) < 4:
        flags.append("text_too_short")
    return build_feature_row(
        row,
        payload=payload,
        content_text=text,
        flags=flags,
        quality_score=1.0 - 0.4 * len(flags),
        metrics=empty_metrics(),
        features={
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "language": json.loads(row["metadata_json"] or "{}").get("language", "unknown"),
        },
    )


def process_batch(
    batch: pa.Table,
    processor: Callable[[dict[str, Any]], dict[str, Any]],
) -> pa.Table:
    return table_from_rows(
        [processor(row) for row in batch.to_pylist()],
        TRAINING_FEATURE_ARROW_SCHEMA,
    )


def process_document_batch(batch: pa.Table) -> pa.Table:
    return process_batch(batch, process_document)


def process_image_batch(batch: pa.Table) -> pa.Table:
    return process_batch(batch, process_image)


def process_audio_batch(batch: pa.Table) -> pa.Table:
    return process_batch(batch, process_audio)


def process_text_batch(batch: pa.Table) -> pa.Table:
    return process_batch(batch, process_text)


def importable_batch_function(name: str) -> Any:
    if __name__ != "__main__":
        return globals()[name]

    import importlib

    module = importlib.import_module(MODULE_NAME)
    return getattr(module, name)


def relation_row_count(rel: Any) -> int:
    return int(rel.aggregate("count(*) as row_count").fetchone()[0])


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
        raise ValueError("unsupported training modalities: " + ", ".join(unsupported))
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


def source_mode(input_path: Path) -> str:
    resolved = input_path.resolve()
    if resolved == DEFAULT_INPUT.resolve():
        return "public_snapshot"
    if resolved == SYNTHETIC_INPUT.resolve():
        return "synthetic_fixture"
    return "custom_manifest"


def build_feature_relations(
    conn: Any,
    raw_assets: Any,
    args: argparse.Namespace,
) -> tuple[Any, dict[str, dict[str, Any]]]:
    udf_options = batch_udf_options(args.execution_backend)
    stage_functions = {
        "document": "process_document_batch",
        "image": "process_image_batch",
        "audio": "process_audio_batch",
        "text": "process_text_batch",
    }
    relations: list[Any] = []
    backend_metadata: dict[str, dict[str, Any]] = {}
    for modality in SUPPORTED_MODALITIES:
        source = raw_assets.filter(f"modality = '{modality}'").order("record_id")
        relations.append(
            source.map_batches(
                importable_batch_function(stage_functions[modality]),
                schema=TRAINING_FEATURE_SCHEMA,
                batch_size=args.batch_size,
                **udf_options,
            )
        )
        backend_metadata[f"process_{modality}"] = backend_metadata_entry(args.execution_backend)

    features = relations[0]
    for relation in relations[1:]:
        features = features.union(relation)
    return features, backend_metadata


def write_artifacts(
    *,
    output_dir: Path,
    raw_assets: Any,
    feature_records: Any,
    training_release: Any,
    rejected_records: Any,
    modality_summary: Any,
    modalities: list[str],
    backend_metadata: dict[str, dict[str, Any]],
    snapshot_verification: dict[str, Any],
    runner: str,
    args: argparse.Namespace,
) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    counts = {
        "raw_record_rows": relation_row_count(raw_assets),
        "feature_record_rows": relation_row_count(feature_records),
        "release_rows": relation_row_count(training_release),
        "rejected_rows": relation_row_count(rejected_records),
        "modality_summary_rows": relation_row_count(modality_summary),
    }
    feature_records.write_parquet(str(output_dir / "feature_records.parquet"))
    training_release.write_parquet(str(output_dir / "training_release.parquet"))
    rejected_records.write_csv(str(output_dir / "rejected_records.csv"))
    modality_summary.write_csv(str(output_dir / "modality_summary.csv"))
    write_json(
        output_dir / "manifest.json",
        {
            "example": "multimodal_training_data",
            "vane_version": vane.__version__,
            "runner": runner,
            "batch_size": args.batch_size,
            "input": display_path(Path(args.input)),
            "source_mode": source_mode(Path(args.input)),
            "public_source_manifest": (
                display_path(PUBLIC_SOURCE_MANIFEST)
                if source_mode(Path(args.input)) == "public_snapshot"
                else ""
            ),
            "public_snapshot_metadata": (
                display_path(PUBLIC_SNAPSHOT_METADATA)
                if source_mode(Path(args.input)) == "public_snapshot"
                else ""
            ),
            "public_snapshot_verified": snapshot_verification.get("status")
            == "verified",
            "snapshot_verification": snapshot_verification,
            "modalities": modalities,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "content_hash": "sha256",
            "release_policy": {
                "accepted_decision": "accepted",
                "minimum_quality_score": 0.8,
                "license_required": True,
            },
            **counts,
            "output_files": [
                "feature_records.parquet",
                "training_release.parquet",
                "rejected_records.csv",
                "modality_summary.csv",
            ],
            **merge_backend_metadata(backend_metadata),
        },
    )
    return counts


def run(args: argparse.Namespace) -> None:
    runner = require_local_relation_runner(vane.current_config().runner)
    input_path = validate_input_path(Path(args.input))
    if source_mode(input_path) == "public_snapshot":
        snapshot_verification = verify_public_asset_snapshot(
            input_path,
            PUBLIC_SNAPSHOT_METADATA,
        )
    else:
        snapshot_verification = {"status": "not_applicable"}
    conn = vane.connect()
    input_relation = conn.read_csv(str(input_path), header=True)
    raw_assets_rel = project_raw_assets(input_relation)
    conn.sql("drop table if exists raw_assets")
    raw_assets_rel.order("record_id").to_table("raw_assets")
    raw_assets = conn.sql("select * from raw_assets")
    modalities = validate_modalities(raw_assets)

    features_rel, backend_metadata = build_feature_relations(conn, raw_assets, args)
    conn.sql("drop table if exists feature_records")
    features_rel.order("modality, record_id").to_table("feature_records")
    feature_records = conn.sql("select * from feature_records")

    training_release_rel = feature_records.filter("decision = 'accepted'").order(
        "split, modality, record_id"
    )
    conn.sql("drop table if exists training_release")
    training_release_rel.to_table("training_release")
    training_release = conn.sql("select * from training_release")

    rejected_records_rel = feature_records.filter("decision = 'rejected'").order(
        "quality_score, modality, record_id"
    )
    conn.sql("drop table if exists rejected_records")
    rejected_records_rel.to_table("rejected_records")
    rejected_records = conn.sql("select * from rejected_records")

    modality_summary_rel = feature_records.aggregate(
        """
        modality,
        count(*) as records,
        sum(byte_size) as total_bytes,
        round(avg(quality_score), 3) as avg_quality_score,
        sum(case when decision = 'accepted' then 1 else 0 end) as accepted,
        sum(case when decision = 'rejected' then 1 else 0 end) as rejected
        """
    ).order("modality")
    conn.sql("drop table if exists modality_summary")
    modality_summary_rel.to_table("modality_summary")
    modality_summary = conn.sql("select * from modality_summary")

    counts = write_artifacts(
        output_dir=Path(args.output_dir),
        raw_assets=raw_assets,
        feature_records=feature_records,
        training_release=training_release,
        rejected_records=rejected_records,
        modality_summary=modality_summary,
        modalities=modalities,
        backend_metadata=backend_metadata,
        snapshot_verification=snapshot_verification,
        runner=runner,
        args=args,
    )
    print(f"Raw assets: {counts['raw_record_rows']}")
    print(f"Released records: {counts['release_rows']}")
    print(f"Rejected records: {counts['rejected_rows']}")
    print(f"Output directory: {args.output_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare typed multimodal assets for a training-data release.",
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--batch-size", type=positive_int, default=2)
    parser.add_argument(
        "--execution-backend",
        choices=PUBLIC_BACKEND_CHOICES,
        default="auto",
        help="Let Vane infer the backend, or pin a task backend explicitly.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()
    try:
        require_local_relation_runner(vane.current_config().runner)
        validate_input_path(Path(args.input))
    except (RuntimeError, FileNotFoundError) as exc:
        parser.error(str(exc))
    return args


if __name__ == "__main__":
    run(parse_args())
