"""Trusted PostgreSQL snapshot and MinIO source-contract validation."""

from __future__ import annotations

import hashlib
import io
import wave
from dataclasses import dataclass
from typing import Any

from PIL import Image, UnidentifiedImageError

from .config import RuntimeConfig
from .contracts import CONDITION_IDS, TRUST_TIERS, validate_primary_keys
from .minio_store import MinioStore
from .pg import connect, read_table


ROLE_MEDIA_TYPES = {
    "internal_meeting": "audio/wav",
    "approved_research": "application/pdf",
    "company_clinical_announcement": "application/pdf",
    "audited_financial_update": "application/pdf",
    "company_regulatory_update": "application/pdf",
    "expert_interview": "application/pdf",
    "chat_screenshot": "image/png",
}


class SourceContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BusinessSnapshot:
    fixture_metadata: dict[str, Any]
    companies: list[dict[str, Any]]
    theses: list[dict[str, Any]]
    conditions: list[dict[str, Any]]
    domain_terms: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    signals: list[dict[str, Any]]
    signal_sources: list[dict[str, Any]]

    @property
    def logical_scenario(self) -> str:
        return str(self.fixture_metadata["logical_scenario"])

    @property
    def fixture_variant(self) -> str:
        return str(self.fixture_metadata["fixture_variant"])

    def signals_for_source(self, source_id: str) -> list[str]:
        return sorted(
            row["signal_id"]
            for row in self.signal_sources
            if row["source_id"] == source_id
        )


@dataclass(frozen=True)
class SourceObject:
    metadata: dict[str, Any]
    content: bytes


def load_business_snapshot(config: RuntimeConfig) -> BusinessSnapshot:
    schema = config.postgres.raw_schema
    with connect(config.postgres) as connection:
        metadata_rows = read_table(
            connection, schema, "fixture_metadata", order_by=("singleton_id",)
        )
        if len(metadata_rows) != 1:
            raise SourceContractError("FIXTURE_METADATA", "fixture_metadata must contain one row")
        snapshot = BusinessSnapshot(
            fixture_metadata=metadata_rows[0],
            companies=read_table(connection, schema, "companies", order_by=("company_id",)),
            theses=read_table(
                connection, schema, "investment_theses", order_by=("thesis_id",)
            ),
            conditions=read_table(
                connection, schema, "thesis_conditions", order_by=("condition_id",)
            ),
            domain_terms=read_table(
                connection,
                schema,
                "domain_terms",
                order_by=("term_id", "alias"),
            ),
            sources=read_table(
                connection, schema, "source_files", order_by=("source_id",)
            ),
            signals=read_table(
                connection, schema, "incoming_signals", order_by=("signal_id",)
            ),
            signal_sources=read_table(
                connection,
                schema,
                "signal_sources",
                order_by=("signal_id", "source_id"),
            ),
        )
    validate_snapshot(snapshot)
    return snapshot


def validate_snapshot(snapshot: BusinessSnapshot) -> None:
    validate_primary_keys(snapshot.companies, ("company_id",), "companies")
    validate_primary_keys(snapshot.theses, ("thesis_id",), "investment_theses")
    validate_primary_keys(snapshot.conditions, ("condition_id",), "thesis_conditions")
    validate_primary_keys(snapshot.sources, ("source_id",), "source_files")
    validate_primary_keys(snapshot.signals, ("signal_id",), "incoming_signals")
    validate_primary_keys(
        snapshot.signal_sources, ("signal_id", "source_id"), "signal_sources"
    )
    company_ids = {row["company_id"] for row in snapshot.companies}
    thesis_ids = {row["thesis_id"] for row in snapshot.theses}
    source_ids = {row["source_id"] for row in snapshot.sources}
    signal_ids = {row["signal_id"] for row in snapshot.signals}
    condition_ids = {row["condition_id"] for row in snapshot.conditions}
    if condition_ids != set(CONDITION_IDS):
        raise SourceContractError(
            "CONDITION_SET",
            f"fixture must contain exact condition IDs; got {sorted(condition_ids)}",
        )
    if len(snapshot.companies) != 1 or len(snapshot.sources) != 7 or len(snapshot.signals) != 4:
        raise SourceContractError(
            "FIXTURE_CARDINALITY",
            "fixture must contain 1 company, 7 sources, and 4 signals",
        )
    for source in snapshot.sources:
        if source["company_id"] not in company_ids:
            raise SourceContractError("SOURCE_COMPANY", "source has unknown company")
        if source["source_role"] not in ROLE_MEDIA_TYPES:
            raise SourceContractError(
                "SOURCE_ROLE", f"unsupported source role: {source['source_role']}"
            )
        expected_media = ROLE_MEDIA_TYPES[source["source_role"]]
        if source["media_type"] != expected_media:
            raise SourceContractError(
                "MEDIA_ROLE_MISMATCH",
                f"{source['source_id']} must use {expected_media}",
            )
        if source["trust_tier"] not in TRUST_TIERS:
            raise SourceContractError("TRUST_TIER", "source trust tier is invalid")
    for thesis in snapshot.theses:
        if thesis["company_id"] not in company_ids or thesis["status"] != "approved":
            raise SourceContractError("THESIS_IDENTITY", "thesis must be approved and company-bound")
    for condition in snapshot.conditions:
        if condition["thesis_id"] not in thesis_ids:
            raise SourceContractError("CONDITION_THESIS", "condition has unknown thesis")
        if condition["operator"] != "qualitative":
            if condition["threshold_numeric"] is None or not condition["unit"]:
                raise SourceContractError(
                    "CONDITION_THRESHOLD", "numeric condition needs threshold and unit"
                )
    for signal in snapshot.signals:
        if signal["company_id"] not in company_ids or signal["thesis_id"] not in thesis_ids:
            raise SourceContractError("SIGNAL_IDENTITY", "signal has unknown company or thesis")
    for link in snapshot.signal_sources:
        if link["signal_id"] not in signal_ids or link["source_id"] not in source_ids:
            raise SourceContractError("SIGNAL_SOURCE_FK", "signal source link is invalid")


def _validate_decodable(metadata: dict[str, Any], content: bytes) -> None:
    media_type = metadata["media_type"]
    if media_type == "audio/wav":
        try:
            with wave.open(io.BytesIO(content), "rb") as audio:
                if audio.getnchannels() != 1 or audio.getframerate() != 16_000:
                    raise SourceContractError(
                        "AUDIO_FORMAT", "audio must be mono 16 kHz WAV"
                    )
                if audio.getnframes() <= 0:
                    raise SourceContractError("AUDIO_EMPTY", "audio has no frames")
        except SourceContractError:
            raise
        except (EOFError, wave.Error) as exc:
            raise SourceContractError("AUDIO_DECODE", "audio cannot be decoded") from exc
    elif media_type == "application/pdf":
        if not content.startswith(b"%PDF-"):
            raise SourceContractError("PDF_DECODE", "PDF header is invalid")
        if b"%%EOF" not in content[-128:]:
            raise SourceContractError("PDF_DECODE", "PDF trailer is invalid")
    elif media_type == "image/png":
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
                if image.format != "PNG":
                    raise SourceContractError("IMAGE_FORMAT", "image must be PNG")
        except SourceContractError:
            raise
        except (OSError, SyntaxError, UnidentifiedImageError) as exc:
            raise SourceContractError("IMAGE_DECODE", "PNG cannot be decoded") from exc
    else:
        raise SourceContractError("MEDIA_TYPE", f"unsupported media type: {media_type}")


def fetch_and_validate_source(
    metadata: dict[str, Any],
    store: MinioStore,
) -> SourceObject:
    bucket = str(metadata["bucket"])
    object_key = str(metadata["object_key"])
    if not bucket or object_key.startswith("/") or ".." in object_key.split("/"):
        raise SourceContractError("LOCATOR", "MinIO locator is invalid")
    try:
        content = store.get_bytes(bucket, object_key)
    except Exception as exc:
        raise SourceContractError("OBJECT_READ", "MinIO object cannot be read") from exc
    actual_sha = hashlib.sha256(content).hexdigest()
    if actual_sha != metadata["sha256"]:
        raise SourceContractError(
            "SHA256_MISMATCH",
            f"{metadata['source_id']} SHA-256 does not match trusted metadata",
        )
    _validate_decodable(metadata, content)
    return SourceObject(metadata=metadata, content=content)
