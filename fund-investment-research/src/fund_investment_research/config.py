"""Strict runtime configuration for the Ray-only Demo."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "runtime.yml"


class ConfigError(ValueError):
    """Raised when runtime.yml violates the public configuration contract."""


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _exact_keys(value: Mapping[str, Any], path: str, expected: set[str]) -> None:
    actual = set(value)
    if actual != expected:
        raise ConfigError(
            f"{path} has wrong keys; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _text(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{path} must be text")
    result = value.strip()
    if not allow_empty and not result:
        raise ConfigError(f"{path} must not be empty")
    return result


def _positive_float(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path} must be numeric")
    result = float(value)
    if result <= 0:
        raise ConfigError(f"{path} must be positive")
    return result


def _positive_int(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{path} must be a positive integer")
    return value


def _ratio(value: Any, path: str) -> float:
    result = _positive_float(value, path)
    if result > 1:
        raise ConfigError(f"{path} must be at most 1")
    return result


@dataclass(frozen=True)
class RayConfig:
    address: str | None


@dataclass(frozen=True)
class PostgresConfig:
    dsn: str
    raw_schema: str
    work_schema: str


@dataclass(frozen=True)
class MinioConfig:
    endpoint: str
    access_key: str
    secret_key: str
    secure: bool
    bucket: str


@dataclass(frozen=True)
class AsrConfig:
    backend: str
    base_url: str
    health_url: str
    model: str
    language: str
    timeout_seconds: float
    batch_size: int


@dataclass(frozen=True)
class OcrConfig:
    backend: str
    minimum_confidence: float
    batch_size: int


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
class TrustConfig:
    maximum_automatic_tier: int


@dataclass(frozen=True)
class VersionConfig:
    pipeline: str
    source_gate: str
    asr: str
    correction: str
    ocr: str
    ai_extract: str
    rules: str
    output_schema: str


@dataclass(frozen=True)
class RuntimeConfig:
    version: int
    runner: str
    output_dir: Path
    ray: RayConfig
    postgres: PostgresConfig
    minio: MinioConfig
    asr: AsrConfig
    ocr: OcrConfig
    ai: AiConfig
    trust: TrustConfig
    versions: VersionConfig
    config_path: Path


def _load_ray(value: Any) -> RayConfig:
    data = _mapping(value, "ray")
    _exact_keys(data, "ray", {"address"})
    raw = data["address"]
    if raw is None:
        return RayConfig(address=None)
    return RayConfig(address=_text(raw, "ray.address"))


def _load_postgres(value: Any) -> PostgresConfig:
    data = _mapping(value, "postgres")
    _exact_keys(data, "postgres", {"dsn", "raw_schema", "work_schema"})
    return PostgresConfig(
        dsn=_text(data["dsn"], "postgres.dsn"),
        raw_schema=_text(data["raw_schema"], "postgres.raw_schema"),
        work_schema=_text(data["work_schema"], "postgres.work_schema"),
    )


def _load_minio(value: Any) -> MinioConfig:
    data = _mapping(value, "minio")
    _exact_keys(
        data,
        "minio",
        {"endpoint", "access_key", "secret_key", "secure", "bucket"},
    )
    if not isinstance(data["secure"], bool):
        raise ConfigError("minio.secure must be boolean")
    return MinioConfig(
        endpoint=_text(data["endpoint"], "minio.endpoint"),
        access_key=_text(data["access_key"], "minio.access_key"),
        secret_key=_text(data["secret_key"], "minio.secret_key"),
        secure=data["secure"],
        bucket=_text(data["bucket"], "minio.bucket"),
    )


def _load_asr(value: Any) -> AsrConfig:
    data = _mapping(value, "asr")
    _exact_keys(
        data,
        "asr",
        {
            "backend",
            "base_url",
            "health_url",
            "model",
            "language",
            "timeout_seconds",
            "batch_size",
        },
    )
    backend = _text(data["backend"], "asr.backend")
    if backend != "openai_compatible_whisper":
        raise ConfigError("asr.backend must be openai_compatible_whisper")
    return AsrConfig(
        backend=backend,
        base_url=_text(data["base_url"], "asr.base_url").rstrip("/"),
        health_url=_text(data["health_url"], "asr.health_url"),
        model=_text(data["model"], "asr.model"),
        language=_text(data["language"], "asr.language"),
        timeout_seconds=_positive_float(data["timeout_seconds"], "asr.timeout_seconds"),
        batch_size=_positive_int(data["batch_size"], "asr.batch_size"),
    )


def _load_ocr(value: Any) -> OcrConfig:
    data = _mapping(value, "ocr")
    _exact_keys(data, "ocr", {"backend", "minimum_confidence", "batch_size"})
    backend = _text(data["backend"], "ocr.backend")
    if backend != "rapidocr":
        raise ConfigError("ocr.backend must be rapidocr")
    return OcrConfig(
        backend=backend,
        minimum_confidence=_ratio(data["minimum_confidence"], "ocr.minimum_confidence"),
        batch_size=_positive_int(data["batch_size"], "ocr.batch_size"),
    )


def _load_ai(value: Any) -> AiConfig:
    data = _mapping(value, "ai")
    _exact_keys(
        data,
        "ai",
        {
            "provider",
            "base_url",
            "health_url",
            "api_key",
            "model",
            "concurrency",
            "timeout_seconds",
            "temperature",
            "max_tokens",
        },
    )
    provider = _text(data["provider"], "ai.provider")
    if provider != "openai":
        raise ConfigError("ai.provider must be openai")
    temperature = data["temperature"]
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise ConfigError("ai.temperature must be numeric")
    if not 0 <= float(temperature) <= 2:
        raise ConfigError("ai.temperature must be between 0 and 2")
    return AiConfig(
        provider=provider,
        base_url=_text(data["base_url"], "ai.base_url").rstrip("/"),
        health_url=_text(data["health_url"], "ai.health_url"),
        api_key=_text(data["api_key"], "ai.api_key"),
        model=_text(data["model"], "ai.model"),
        concurrency=_positive_int(data["concurrency"], "ai.concurrency"),
        timeout_seconds=_positive_float(data["timeout_seconds"], "ai.timeout_seconds"),
        temperature=float(temperature),
        max_tokens=_positive_int(data["max_tokens"], "ai.max_tokens"),
    )


def _load_trust(value: Any) -> TrustConfig:
    data = _mapping(value, "trust")
    _exact_keys(data, "trust", {"maximum_automatic_tier"})
    tier = _positive_int(data["maximum_automatic_tier"], "trust.maximum_automatic_tier")
    if tier not in {1, 2, 3}:
        raise ConfigError("trust.maximum_automatic_tier must be 1, 2, or 3")
    return TrustConfig(maximum_automatic_tier=tier)


def _load_versions(value: Any) -> VersionConfig:
    data = _mapping(value, "versions")
    expected = {
        "pipeline",
        "source_gate",
        "asr",
        "correction",
        "ocr",
        "ai_extract",
        "rules",
        "output_schema",
    }
    _exact_keys(data, "versions", expected)
    return VersionConfig(**{key: _text(data[key], f"versions.{key}") for key in expected})


def load_runtime_config(path: Path = DEFAULT_CONFIG_PATH) -> RuntimeConfig:
    """Load runtime.yml and reject unknown fields and non-Ray execution."""

    resolved = path.expanduser().resolve()
    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load runtime config {resolved}: {exc}") from exc
    data = _mapping(raw, "runtime")
    expected = {
        "version",
        "runner",
        "output_dir",
        "ray",
        "postgres",
        "minio",
        "asr",
        "ocr",
        "ai",
        "trust",
        "versions",
    }
    _exact_keys(data, "runtime", expected)
    if data["version"] != 1:
        raise ConfigError("runtime.version must be 1")
    runner = _text(data["runner"], "runner").lower()
    if runner != "ray":
        raise ConfigError("runner must be ray; Local Runner is intentionally unsupported")
    raw_output = Path(_text(data["output_dir"], "output_dir"))
    output_dir = raw_output if raw_output.is_absolute() else resolved.parent / raw_output
    return RuntimeConfig(
        version=1,
        runner=runner,
        output_dir=output_dir.resolve(),
        ray=_load_ray(data["ray"]),
        postgres=_load_postgres(data["postgres"]),
        minio=_load_minio(data["minio"]),
        asr=_load_asr(data["asr"]),
        ocr=_load_ocr(data["ocr"]),
        ai=_load_ai(data["ai"]),
        trust=_load_trust(data["trust"]),
        versions=_load_versions(data["versions"]),
        config_path=resolved,
    )
