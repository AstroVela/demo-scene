"""Strict static runtime configuration for the procurement audit demo."""

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
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ROOT_FIELDS = {
    "version",
    "runner",
    "output_dir",
    "postgres",
    "minio",
    "ocr",
    "ai",
}
_POSTGRES_FIELDS = {
    "dsn",
    "raw_schema",
    "project_table",
    "supplier_table",
    "score_table",
    "evidence_table",
}
_MINIO_FIELDS = {"endpoint", "access_key", "secret_key", "secure", "bucket"}
_OCR_FIELDS = {"engine", "device", "minimum_confidence"}
_AI_FIELDS = {
    "provider",
    "base_url",
    "health_url",
    "api_key",
    "model",
    "concurrency",
    "timeout_seconds",
    "temperature",
    "max_tokens",
}


class ConfigError(ValueError):
    """Raised when the checked-in runtime configuration is invalid."""


@dataclass(frozen=True)
class PostgresConfig:
    dsn: str
    raw_schema: str
    project_table: str
    supplier_table: str
    score_table: str
    evidence_table: str

    @property
    def raw_relation_names(self) -> tuple[str, str, str, str]:
        return tuple(
            f"{self.raw_schema}.{table}"
            for table in (
                self.project_table,
                self.supplier_table,
                self.score_table,
                self.evidence_table,
            )
        )


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
    minimum_confidence: float


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
    runner: str
    output_dir: Path
    postgres: PostgresConfig
    minio: MinioConfig
    ocr: OcrConfig
    ai: AiConfig

    def redacted_summary(self) -> str:
        return (
            f"runner={self.runner}; "
            f"postgres={','.join(self.postgres.raw_relation_names)}; "
            f"minio={self.minio.endpoint}/{self.minio.bucket} "
            f"secure={self.minio.secure}; "
            f"ocr={self.ocr.engine}/{self.ocr.device}; "
            f"ai={self.ai.provider}/{self.ai.model}"
        )


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _exact_fields(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ConfigError(f"{path} has wrong fields; missing={missing}, extra={extra}")


def _string(value: Mapping[str, Any], key: str, path: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ConfigError(f"{path}.{key} must be a non-empty string")
    result = item.strip()
    if _CONTROL_CHARACTERS.search(result):
        raise ConfigError(f"{path}.{key} must not contain control characters")
    return result


def _identifier(value: Mapping[str, Any], key: str, path: str) -> str:
    result = _string(value, key, path)
    if not _IDENTIFIER.fullmatch(result):
        raise ConfigError(f"{path}.{key} must be a SQL identifier")
    return result


def _boolean(value: Mapping[str, Any], key: str, path: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise ConfigError(f"{path}.{key} must be boolean")
    return item


def _project_path(config_path: Path, value: Mapping[str, Any], key: str) -> Path:
    text = _string(value, key, "configuration")
    candidate = Path(text)
    resolved = candidate.resolve() if candidate.is_absolute() else (config_path.parent / candidate).resolve()
    try:
        resolved.relative_to(config_path.parent.resolve())
    except ValueError as exc:
        raise ConfigError(f"configuration.{key} must stay inside the project") from exc
    return resolved


def _loopback_http_url(value: Mapping[str, Any], key: str) -> str:
    text = _string(value, key, "ai")
    try:
        parsed = urlsplit(text)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"ai.{key} must be a loopback HTTP URL") from exc
    if (
        parsed.scheme != "http"
        or hostname not in {"127.0.0.1", "localhost"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ConfigError(f"ai.{key} must be a loopback HTTP URL")
    return text


def _finite_number(
    value: Mapping[str, Any],
    key: str,
    path: str,
    *,
    minimum: float,
    maximum: float | None = None,
    inclusive_minimum: bool = True,
) -> float:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, (int, float)):
        raise ConfigError(f"{path}.{key} must be numeric")
    result = float(item)
    below = result < minimum if inclusive_minimum else result <= minimum
    if not math.isfinite(result) or below or (maximum is not None and result > maximum):
        upper = f" and {maximum}" if maximum is not None else ""
        raise ConfigError(f"{path}.{key} must be between {minimum}{upper}")
    return result


def _positive_integer(value: Mapping[str, Any], key: str, path: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
        raise ConfigError(f"{path}.{key} must be a positive integer")
    return item


def load_runtime_config(path: Path | str = DEFAULT_CONFIG_PATH) -> RuntimeConfig:
    """Load and validate the demo's sole runtime configuration source."""

    config_path = Path(path).resolve()
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read runtime configuration {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in runtime configuration {config_path}: {exc}") from exc

    root = _mapping(payload, "configuration")
    _exact_fields(root, _ROOT_FIELDS, "configuration")
    version = root.get("version")
    if isinstance(version, bool) or version != 1:
        raise ConfigError("configuration.version must be 1")
    runner = _string(root, "runner", "configuration")
    if runner not in {"local", "ray"}:
        raise ConfigError("configuration.runner must be local or ray")

    postgres_value = _mapping(root.get("postgres"), "postgres")
    _exact_fields(postgres_value, _POSTGRES_FIELDS, "postgres")
    postgres = PostgresConfig(
        dsn=_string(postgres_value, "dsn", "postgres"),
        raw_schema=_identifier(postgres_value, "raw_schema", "postgres"),
        project_table=_identifier(postgres_value, "project_table", "postgres"),
        supplier_table=_identifier(postgres_value, "supplier_table", "postgres"),
        score_table=_identifier(postgres_value, "score_table", "postgres"),
        evidence_table=_identifier(postgres_value, "evidence_table", "postgres"),
    )

    minio_value = _mapping(root.get("minio"), "minio")
    _exact_fields(minio_value, _MINIO_FIELDS, "minio")
    minio = MinioConfig(
        endpoint=_string(minio_value, "endpoint", "minio"),
        access_key=_string(minio_value, "access_key", "minio"),
        secret_key=_string(minio_value, "secret_key", "minio"),
        secure=_boolean(minio_value, "secure", "minio"),
        bucket=_string(minio_value, "bucket", "minio"),
    )

    ocr_value = _mapping(root.get("ocr"), "ocr")
    _exact_fields(ocr_value, _OCR_FIELDS, "ocr")
    engine = _string(ocr_value, "engine", "ocr")
    if engine != "rapidocr":
        raise ConfigError("ocr.engine must be rapidocr")
    device = _string(ocr_value, "device", "ocr")
    if device != "cpu":
        raise ConfigError("ocr.device must be cpu")
    ocr = OcrConfig(
        engine=engine,
        device=device,
        minimum_confidence=_finite_number(
            ocr_value,
            "minimum_confidence",
            "ocr",
            minimum=0.0,
            maximum=1.0,
        ),
    )

    ai_value = _mapping(root.get("ai"), "ai")
    _exact_fields(ai_value, _AI_FIELDS, "ai")
    provider = _string(ai_value, "provider", "ai")
    if provider != "openai":
        raise ConfigError("ai.provider must be openai")
    ai = AiConfig(
        provider=provider,
        base_url=_loopback_http_url(ai_value, "base_url"),
        health_url=_loopback_http_url(ai_value, "health_url"),
        api_key=_string(ai_value, "api_key", "ai"),
        model=_string(ai_value, "model", "ai"),
        concurrency=_positive_integer(ai_value, "concurrency", "ai"),
        timeout_seconds=_finite_number(
            ai_value,
            "timeout_seconds",
            "ai",
            minimum=0.0,
            inclusive_minimum=False,
        ),
        temperature=_finite_number(
            ai_value,
            "temperature",
            "ai",
            minimum=0.0,
        ),
        max_tokens=_positive_integer(ai_value, "max_tokens", "ai"),
    )
    return RuntimeConfig(
        version=1,
        runner=runner,
        output_dir=_project_path(config_path, root, "output_dir"),
        postgres=postgres,
        minio=minio,
        ocr=ocr,
        ai=ai,
    )
