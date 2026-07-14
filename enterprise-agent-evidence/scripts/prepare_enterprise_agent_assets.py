#!/usr/bin/env python3
"""Build the pinned public-asset snapshot used by enterprise evidence governance."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "enterprise_multimodal_agent"
DEFAULT_SOURCE_MANIFEST = DATA_DIR / "asset_sources.csv"
DEFAULT_ASSET_CATALOG = DATA_DIR / "asset_catalog.csv"
DEFAULT_SNAPSHOT_METADATA = DATA_DIR / "asset_snapshot.json"
MAX_ASSET_BYTES = 5 * 1024 * 1024
USER_AGENT = "VaneEnterpriseEvidenceSnapshot/1.0"
OUTPUT_COLUMNS = (
    "record_id",
    "modality",
    "source_uri",
    "license_id",
    "split",
    "mime_type",
    "text",
    "content_path",
    "expected_sha256",
    "content_base64",
    "metadata_json",
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def read_sources(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as source_file:
        rows = list(csv.DictReader(source_file))
    if not rows:
        raise ValueError(f"public source manifest is empty: {path}")
    return rows


def snapshot_path(value: str) -> Path:
    path = (REPO_ROOT / value).resolve()
    data_root = DATA_DIR.resolve()
    if path != data_root and data_root not in path.parents:
        raise ValueError(f"snapshot path must stay under {DATA_DIR}: {value}")
    return path


def download_asset(row: dict[str, str], *, refresh: bool) -> tuple[Path, int]:
    output_path = snapshot_path(row["snapshot_path"])
    expected_sha256 = row["expected_sha256"].strip().lower()
    if output_path.is_file() and not refresh and file_sha256(output_path) == expected_sha256:
        return output_path, output_path.stat().st_size

    output_path.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        row["source_uri"],
        headers={"User-Agent": USER_AGENT},
    )
    temp_path: Path | None = None
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_ASSET_BYTES:
                raise ValueError(
                    f"asset exceeds {MAX_ASSET_BYTES} bytes: {row['record_id']}"
                )
            with tempfile.NamedTemporaryFile(
                dir=output_path.parent,
                prefix=f".{output_path.name}.",
                delete=False,
            ) as output_file:
                temp_path = Path(output_file.name)
                digest = hashlib.sha256()
                byte_size = 0
                while chunk := response.read(64 * 1024):
                    byte_size += len(chunk)
                    if byte_size > MAX_ASSET_BYTES:
                        raise ValueError(
                            f"asset exceeds {MAX_ASSET_BYTES} bytes: {row['record_id']}"
                        )
                    output_file.write(chunk)
                    digest.update(chunk)
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"SHA-256 mismatch for {row['record_id']}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        temp_path.replace(output_path)
        temp_path = None
        return output_path, byte_size
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def enriched_metadata(row: dict[str, str]) -> str:
    metadata = json.loads(row["metadata_json"] or "{}")
    metadata.update(
        {
            "license_uri": row["license_uri"],
            "source_page_uri": row["source_page_uri"],
            "source_version": row["source_version"],
        }
    )
    return json.dumps(metadata, sort_keys=True)


def write_asset_catalog(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=OUTPUT_COLUMNS,
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "record_id": row["record_id"],
                    "modality": row["modality"],
                    "source_uri": row["source_uri"],
                    "license_id": row["license_id"],
                    "split": row["split"],
                    "mime_type": row["mime_type"],
                    "text": row["text"],
                    "content_path": row["snapshot_path"],
                    "expected_sha256": row["expected_sha256"],
                    "content_base64": "",
                    "metadata_json": enriched_metadata(row),
                }
            )


def write_snapshot_metadata(
    path: Path,
    *,
    source_manifest: Path,
    asset_catalog: Path,
    assets: list[dict[str, Any]],
) -> None:
    payload = {
        "dataset": "enterprise_agent_evidence_asset_snapshot",
        "source_manifest": display_path(source_manifest),
        "source_manifest_sha256": file_sha256(source_manifest),
        "asset_catalog": display_path(asset_catalog),
        "asset_catalog_sha256": file_sha256(asset_catalog),
        "records": len(assets),
        "modalities": sorted({asset["modality"] for asset in assets}),
        "assets": assets,
    }
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def prepare(args: argparse.Namespace) -> None:
    source_manifest = Path(args.source_manifest)
    asset_catalog = Path(args.asset_catalog)
    snapshot_metadata = Path(args.snapshot_metadata)
    rows = read_sources(source_manifest)
    assets: list[dict[str, Any]] = []
    for row in rows:
        asset_path, byte_size = download_asset(row, refresh=args.refresh)
        assets.append(
            {
                "byte_size": byte_size,
                "license_id": row["license_id"],
                "license_uri": row["license_uri"],
                "modality": row["modality"],
                "record_id": row["record_id"],
                "sha256": file_sha256(asset_path),
                "snapshot_path": display_path(asset_path),
                "source_page_uri": row["source_page_uri"],
                "source_uri": row["source_uri"],
                "source_version": row["source_version"],
            }
        )

    write_asset_catalog(asset_catalog, rows)
    write_snapshot_metadata(
        snapshot_metadata,
        source_manifest=source_manifest,
        asset_catalog=asset_catalog,
        assets=assets,
    )
    print(f"Public assets: {len(assets)}")
    print(f"Asset catalog: {display_path(asset_catalog)}")
    print(f"Snapshot metadata: {display_path(snapshot_metadata)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the reproducible public asset snapshot for the enterprise example.",
    )
    parser.add_argument("--source-manifest", default=str(DEFAULT_SOURCE_MANIFEST))
    parser.add_argument("--asset-catalog", default=str(DEFAULT_ASSET_CATALOG))
    parser.add_argument("--snapshot-metadata", default=str(DEFAULT_SNAPSHOT_METADATA))
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Download every source even when the checked-in asset hash matches.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    prepare(parse_args())
