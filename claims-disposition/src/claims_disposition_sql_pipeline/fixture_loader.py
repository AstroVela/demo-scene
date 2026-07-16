"""Create and load the deterministic four-claim demo fixture."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from importlib.resources import files
import io
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .config import DEFAULT_CONFIG_PATH, load_runtime_config
from .minio_store import MinioStore
from .pg import connect_postgres, initialize_schemas, reset_fixture_rows


FIXTURE_BUCKET = "claims-disposition-fixtures"
EXPECTED_DISPOSITIONS = {
    "CLM-APPROVE": "approve_for_payment",
    "CLM-DENY": "deny_claim",
    "CLM-MISSING": "request_more_materials",
    "CLM-REVIEW": "manual_review",
}


@dataclass(frozen=True)
class FixtureObject:
    bucket: str
    object_key: str
    value: bytes
    content_type: str


@dataclass(frozen=True)
class FixtureBundle:
    claims: tuple[dict, ...]
    objects: tuple[FixtureObject, ...]


_CLAIM_DETAILS = (
    (
        "CLM-APPROVE",
        "clear_low_risk_damage",
        "Complete low-risk vehicle damage packet with clear minor damage.",
        "Alex Approve",
        "2026-07-01",
        101,
    ),
    (
        "CLM-DENY",
        "no_meaningful_damage",
        "Complete packet whose photo does not show meaningful vehicle damage.",
        "Dana Deny",
        "2026-07-02",
        202,
    ),
    (
        "CLM-MISSING",
        "missing_required_material_fact",
        "The supporting form is present but its claimant name is blank.",
        "",
        "2026-07-03",
        303,
    ),
    (
        "CLM-REVIEW",
        "ambiguous_damage",
        "Complete packet with damage evidence that remains AI-ambiguous.",
        "Riley Review",
        "2026-07-04",
        404,
    ),
)
_PACKAGED_PHOTO_ASSETS = {
    "clear_low_risk_damage": "damaged_vehicle.jpg",
    "no_meaningful_damage": "clean_vehicle.jpg",
}


def _font(size: int):
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _photo_bytes(seed: int, scenario: str) -> bytes:
    width, height = 1280, 960
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    y = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    noise = rng.normal(0, 10, size=(height, width)).astype(np.float32)
    pixels = np.empty((height, width, 3), dtype=np.float32)
    pixels[:, :, 0] = 80 + 75 * x + 30 * y + noise
    pixels[:, :, 1] = 105 + 45 * x + 35 * y + noise
    pixels[:, :, 2] = 130 + 30 * x + 20 * y + noise
    image = Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8), "RGB")

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (190, 330, 1090, 730),
        radius=80,
        fill=(45, 83, 128),
        outline=(225, 230, 235),
        width=10,
    )
    draw.polygon(
        [(360, 330), (500, 205), (820, 205), (965, 330)],
        fill=(70, 105, 145),
        outline=(225, 230, 235),
    )
    draw.ellipse((285, 650, 485, 850), fill=(30, 30, 35), outline="white", width=8)
    draw.ellipse((800, 650, 1000, 850), fill=(30, 30, 35), outline="white", width=8)
    draw.rectangle((510, 235, 650, 325), fill=(150, 195, 220))
    draw.rectangle((670, 235, 810, 325), fill=(150, 195, 220))

    if scenario in {"clear_low_risk_damage", "missing_required_material_fact"}:
        draw.arc((770, 390, 1010, 640), 20, 300, fill=(255, 185, 70), width=18)
        draw.line((805, 445, 960, 575), fill=(255, 205, 90), width=12)
    elif scenario == "ambiguous_damage":
        draw.ellipse((735, 420, 980, 620), outline=(150, 150, 155), width=8)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92, optimize=True)
    return buffer.getvalue()


def _packaged_photo_bytes(name: str) -> bytes:
    return (
        files(__package__)
        .joinpath("assets")
        .joinpath(name)
        .read_bytes()
    )


def _document_bytes(fields: Mapping[str, str]) -> bytes:
    image = Image.new("RGB", (1600, 1000), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(64)
    field_font = _font(50)
    value_font = _font(52)

    draw.rectangle((35, 35, 1565, 965), outline=(35, 55, 85), width=6)
    draw.text((100, 80), "VEHICLE CLAIM FORM", font=title_font, fill=(25, 45, 75))
    draw.line((100, 175, 1500, 175), fill=(25, 45, 75), width=4)

    rows = (
        ("CLAIM NUMBER", fields["claim_number"]),
        ("CLAIMANT NAME", fields["claimant_name"]),
        ("LOSS DATE", fields["loss_date"]),
    )
    for index, (label, value) in enumerate(rows):
        y = 285 + index * 205
        draw.text((120, y), f"{label}:", font=field_font, fill="black")
        if value:
            draw.text((610, y), value, font=value_font, fill="black")
        draw.line((610, y + 72, 1440, y + 72), fill=(90, 90, 90), width=3)

    draw.text(
        (120, 885),
        "Submitted for deterministic claims-disposition testing",
        font=_font(28),
        fill=(75, 75, 75),
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=3)
    return buffer.getvalue()


def _materials(claim_id: str, bucket: str) -> tuple[list[dict], str, str]:
    suffix = claim_id.removeprefix("CLM-")
    photo_id = f"PHOTO-{suffix}-001"
    document_id = f"DOC-{suffix}-001"
    materials = [
        {
            "file_id": photo_id,
            "file_order": 1,
            "role": "damage_photo",
            "media_type": "image/jpeg",
            "bucket": bucket,
            "object_key": f"claims/{claim_id}/photos/{photo_id}.jpg",
        },
        {
            "file_id": document_id,
            "file_order": 2,
            "role": "supporting_document",
            "media_type": "image/png",
            "bucket": bucket,
            "object_key": f"claims/{claim_id}/documents/{document_id}.png",
        },
    ]
    return materials, photo_id, document_id


def build_fixture(bucket: str = FIXTURE_BUCKET) -> FixtureBundle:
    """Build local seed data without using it as a pipeline runtime input."""

    claims: list[dict] = []
    objects: list[FixtureObject] = []

    for index, details in enumerate(_CLAIM_DETAILS):
        claim_id, scenario, description, claimant_name, loss_date, seed = details
        materials, _photo_id, _document_id = _materials(claim_id, bucket)
        fields = {
            "claim_number": claim_id,
            "claimant_name": claimant_name,
            "loss_date": loss_date,
        }
        packaged_photo = _PACKAGED_PHOTO_ASSETS.get(scenario)
        photo_bytes = (
            _packaged_photo_bytes(packaged_photo)
            if packaged_photo is not None
            else _photo_bytes(seed, scenario)
        )
        claims.append(
            {
                "claim_id": claim_id,
                "scenario": scenario,
                "description": description,
                "submitted_at": f"2026-07-10T00:0{index}:00+00:00",
                "is_test_claim": True,
                "materials_json": json.dumps(
                    materials,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        objects.extend(
            (
                FixtureObject(
                    bucket=bucket,
                    object_key=materials[0]["object_key"],
                    value=photo_bytes,
                    content_type="image/jpeg",
                ),
                FixtureObject(
                    bucket=bucket,
                    object_key=materials[1]["object_key"],
                    value=_document_bytes(fields),
                    content_type="image/png",
                ),
            )
        )

    return FixtureBundle(claims=tuple(claims), objects=tuple(objects))


def load_fixture(config_path: Path = DEFAULT_CONFIG_PATH) -> tuple[int, int]:
    """Refresh the PostgreSQL snapshot and MinIO objects used at runtime."""

    config = load_runtime_config(config_path)
    fixture = build_fixture(config.minio.bucket)

    # PostgreSQL receives claim metadata and canonical MinIO locators.
    with connect_postgres(config.postgres) as connection:
        initialize_schemas(connection)
        reset_fixture_rows(connection, list(fixture.claims))

    # MinIO receives the binary photos and supporting documents.
    store = MinioStore(config.minio)
    store.probe()
    store.ensure_bucket(config.minio.bucket)
    for claim_id in EXPECTED_DISPOSITIONS:
        store.remove_prefix(config.minio.bucket, f"claims/{claim_id}/")
    for item in fixture.objects:
        store.put_bytes(item.bucket, item.object_key, item.value, item.content_type)

    return len(fixture.claims), len(fixture.objects)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load deterministic claims disposition fixtures."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Static runtime YAML path.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        claim_count, object_count = load_fixture(args.config)
    except Exception as exc:
        print(f"fixture load failed: {exc}", file=sys.stderr)
        return 1
    print(f"loaded {claim_count} claims and {object_count} objects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
