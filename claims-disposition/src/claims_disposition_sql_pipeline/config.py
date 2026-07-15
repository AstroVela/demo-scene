"""Static runtime configuration for the claims disposition demo."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "runtime.yml"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


class ConfigError(ValueError):
    """Raised when the checked-in runtime configuration is incomplete."""


@dataclass(frozen=True)
class PostgresConfig:
    dsn: str
    raw_schema: str
    raw_table: str
    output_schema: str
    output_table: str

    @property
    def raw_relation(self) -> str:
        return f"{self.raw_schema}.{self.raw_table}"

    @property
    def output_relation(self) -> str:
        return f"{self.output_schema}.{self.output_table}"


@dataclass(frozen=True)
class MinioConfig:
    endpoint: str
    access_key: str
    secret_key: str
    secure: bool
    bucket: str


@dataclass(frozen=True)
class OcrConfig:
    engine: str
    device: str
    required_fields: tuple[str, ...]
    minimum_text_confidence: float


@dataclass(frozen=True)
class AiConfig:
    provider: str
    base_url: str
    health_url: str
    api_key: str
    model: str
    concurrency: int
    timeout_seconds: float
    temperature: float
    max_tokens: int


@dataclass(frozen=True)
class RuntimeConfig:
    version: int
    postgres: PostgresConfig
    minio: MinioConfig
    ocr: OcrConfig
    ai: AiConfig

    def redacted_summary(self) -> str:
        """Return useful diagnostics without credentials or a PostgreSQL DSN."""

        return (
            f"postgres={self.postgres.raw_relation}->{self.postgres.output_relation}; "
            f"minio={self.minio.endpoint}/{self.minio.bucket} secure={self.minio.secure}; "
            f"ocr={self.ocr.engine}/{self.ocr.device}; "
            f"ai={self.ai.provider}/{self.ai.model}"
        )


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _section(root: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    if key not in root:
        raise ConfigError(f"{key} is required")
    return _mapping(root[key], key)


def _string(section: Mapping[str, Any], section_name: str, key: str) -> str:
    path = f"{section_name}.{key}"
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{path} is required and must be a non-empty string")
    return value.strip()


def _identifier(section: Mapping[str, Any], section_name: str, key: str) -> str:
    value = _string(section, section_name, key)
    if not _IDENTIFIER.fullmatch(value):
        raise ConfigError(f"{section_name}.{key} must be a SQL identifier")
    return value


def _boolean(section: Mapping[str, Any], section_name: str, key: str) -> bool:
    path = f"{section_name}.{key}"
    value = section.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"{path} is required and must be boolean")
    return value


def _required_fields(section: Mapping[str, Any]) -> tuple[str, ...]:
    value = section.get("required_fields")
    if not isinstance(value, list) or not value:
        raise ConfigError("ocr.required_fields is required and must be a non-empty list")
    fields = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
    if len(fields) != len(value) or len(set(fields)) != len(fields):
        raise ConfigError("ocr.required_fields must contain unique non-empty strings")
    return fields


def _confidence(section: Mapping[str, Any]) -> float:
    value = section.get("minimum_text_confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError("ocr.minimum_text_confidence is required and must be numeric")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ConfigError("ocr.minimum_text_confidence must be between 0 and 1")
    return result


def _loopback_http_url(section: Mapping[str, Any], key: str) -> str:
    path = f"ai.{key}"
    raw_value = section.get(key)
    if isinstance(raw_value, str) and _CONTROL_CHARACTERS.search(raw_value):
        raise ConfigError(f"{path} must not contain control characters")
    value = _string(section, "ai", key)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{path} must be a loopback HTTP URL") from exc
    if parsed.scheme != "http" or hostname not in {"127.0.0.1", "localhost"}:
        raise ConfigError(f"{path} must be a loopback HTTP URL")
    return value


def _positive_integer(section: Mapping[str, Any], key: str) -> int:
    path = f"ai.{key}"
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{path} is required and must be a positive integer")
    return value


def _finite_number(
    section: Mapping[str, Any], key: str, *, allow_zero: bool
) -> float:
    path = f"ai.{key}"
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path} is required and must be numeric")
    qualifier = "non-negative" if allow_zero else "positive"
    try:
        result = float(value)
    except OverflowError as exc:
        raise ConfigError(
            f"{path} must be a finite {qualifier} number"
        ) from exc
    if (
        not math.isfinite(result)
        or result < 0
        or (result == 0 and not allow_zero)
    ):
        raise ConfigError(f"{path} must be a finite {qualifier} number")
    return result


def load_runtime_config(path: Path | str = DEFAULT_CONFIG_PATH) -> RuntimeConfig:
    """Load the sole, checked-in runtime configuration source."""

    config_path = Path(path)
    try:
        payload = yaml.safe_load(config_path.read_text())
    except OSError as exc:
        raise ConfigError(f"cannot read runtime configuration {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in runtime configuration {config_path}: {exc}") from exc

    root = _mapping(payload, "configuration")
    version = root.get("version")
    if version != 1 or isinstance(version, bool):
        raise ConfigError("version must be 1")

    postgres_data = _section(root, "postgres")
    postgres = PostgresConfig(
        dsn=_string(postgres_data, "postgres", "dsn"),
        raw_schema=_identifier(postgres_data, "postgres", "raw_schema"),
        raw_table=_identifier(postgres_data, "postgres", "raw_table"),
        output_schema=_identifier(postgres_data, "postgres", "output_schema"),
        output_table=_identifier(postgres_data, "postgres", "output_table"),
    )

    minio_data = _section(root, "minio")
    minio = MinioConfig(
        endpoint=_string(minio_data, "minio", "endpoint"),
        access_key=_string(minio_data, "minio", "access_key"),
        secret_key=_string(minio_data, "minio", "secret_key"),
        secure=_boolean(minio_data, "minio", "secure"),
        bucket=_string(minio_data, "minio", "bucket"),
    )

    ocr_data = _section(root, "ocr")
    ocr = OcrConfig(
        engine=_string(ocr_data, "ocr", "engine"),
        device=_string(ocr_data, "ocr", "device"),
        required_fields=_required_fields(ocr_data),
        minimum_text_confidence=_confidence(ocr_data),
    )

    ai_data = _section(root, "ai")
    provider = _string(ai_data, "ai", "provider")
    if provider != "openai":
        raise ConfigError("ai.provider must be openai")
    ai = AiConfig(
        provider=provider,
        base_url=_loopback_http_url(ai_data, "base_url"),
        health_url=_loopback_http_url(ai_data, "health_url"),
        api_key=_string(ai_data, "ai", "api_key"),
        model=_string(ai_data, "ai", "model"),
        concurrency=_positive_integer(ai_data, "concurrency"),
        timeout_seconds=_finite_number(
            ai_data,
            "timeout_seconds",
            allow_zero=False,
        ),
        temperature=_finite_number(
            ai_data,
            "temperature",
            allow_zero=True,
        ),
        max_tokens=_positive_integer(ai_data, "max_tokens"),
    )

    return RuntimeConfig(
        version=1,
        postgres=postgres,
        minio=minio,
        ocr=ocr,
        ai=ai,
    )
