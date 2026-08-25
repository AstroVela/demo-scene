"""Static runtime configuration for the customer service audit demo."""

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


class ConfigError(ValueError):
    """Raised when the checked-in runtime configuration is incomplete."""


@dataclass(frozen=True)
class MinioConfig:
    endpoint: str
    access_key: str
    secret_key: str
    secure: bool
    bucket: str
    recordings_prefix: str
    analysis_prefix: str


@dataclass(frozen=True)
class AsrConfig:
    engine: str
    model: str
    device: str
    compute_type: str
    language: str
    beam_size: int
    min_text_chars: int
    # openai-audio (gateway ASR) carries its own endpoint, credential and
    # request timeout.
    base_url: str = ""
    api_key: str = ""
    timeout_seconds: float = 0.0


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
    minio: MinioConfig
    asr: AsrConfig
    ai: AiConfig

    def redacted_summary(self) -> str:
        """Return useful diagnostics without credentials."""

        return (
            f"runner={self.runner}; "
            f"minio={self.minio.endpoint}/{self.minio.bucket} secure={self.minio.secure}; "
            f"asr={self.asr.engine}/{self.asr.model}/{self.asr.device}; "
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


def _boolean(section: Mapping[str, Any], section_name: str, key: str) -> bool:
    path = f"{section_name}.{key}"
    value = section.get(key)
    if not isinstance(value, bool):
        raise ConfigError(f"{path} is required and must be boolean")
    return value


def _prefix(section: Mapping[str, Any], section_name: str, key: str) -> str:
    path = f"{section_name}.{key}"
    value = _string(section, section_name, key)
    if value.startswith("/") or not value.endswith("/") or "//" in value:
        raise ConfigError(f"{path} must be a relative object prefix ending in '/'")
    return value


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


def _asr_http_url(section: Mapping[str, Any], key: str) -> str:
    """Validate an OpenAI-compatible ASR gateway URL (http or https, any host)."""

    path = f"asr.{key}"
    raw_value = section.get(key)
    if isinstance(raw_value, str) and _CONTROL_CHARACTERS.search(raw_value):
        raise ConfigError(f"{path} must not contain control characters")
    value = _string(section, "asr", key)
    try:
        parsed = urlsplit(value)
        # Explicitly reading the port rejects non-numeric ports and ports
        # outside 1-65535 instead of failing later at connect time.
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{path} must be a valid http(s) URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or port == 0:
        raise ConfigError(f"{path} must be a valid http(s) URL")
    return value


def _positive_integer(section: Mapping[str, Any], section_name: str, key: str) -> int:
    path = f"{section_name}.{key}"
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{path} is required and must be a positive integer")
    return value


def _finite_number(
    section: Mapping[str, Any],
    section_name: str,
    key: str,
    *,
    allow_zero: bool,
) -> float:
    path = f"{section_name}.{key}"
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path} must be numeric")
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
    runner = root.get("runner")
    if not isinstance(runner, str) or runner not in {"local", "ray"}:
        raise ConfigError("runner must be local or ray")

    minio_data = _section(root, "minio")
    minio = MinioConfig(
        endpoint=_string(minio_data, "minio", "endpoint"),
        access_key=_string(minio_data, "minio", "access_key"),
        secret_key=_string(minio_data, "minio", "secret_key"),
        secure=_boolean(minio_data, "minio", "secure"),
        bucket=_string(minio_data, "minio", "bucket"),
        recordings_prefix=_prefix(minio_data, "minio", "recordings_prefix"),
        analysis_prefix=_prefix(minio_data, "minio", "analysis_prefix"),
    )

    asr_data = _section(root, "asr")
    engine = _string(asr_data, "asr", "engine")
    model = _string(asr_data, "asr", "model")
    language = _string(asr_data, "asr", "language")
    min_text_chars = _positive_integer(asr_data, "asr", "min_text_chars")
    if engine == "faster-whisper":
        device = _string(asr_data, "asr", "device")
        if device not in {"cpu", "cuda"}:
            raise ConfigError("asr.device must be cpu or cuda")
        asr = AsrConfig(
            engine=engine,
            model=model,
            device=device,
            compute_type=_string(asr_data, "asr", "compute_type"),
            language=language,
            beam_size=_positive_integer(asr_data, "asr", "beam_size"),
            min_text_chars=min_text_chars,
        )
    elif engine == "openai-audio":
        asr = AsrConfig(
            engine=engine,
            model=model,
            device="",
            compute_type="",
            language=language,
            beam_size=1,
            min_text_chars=min_text_chars,
            base_url=_asr_http_url(asr_data, "base_url"),
            api_key=_string(asr_data, "asr", "api_key"),
            timeout_seconds=_finite_number(
                asr_data, "asr", "timeout_seconds", allow_zero=False
            ),
        )
    else:
        raise ConfigError(
            "asr.engine must be faster-whisper or openai-audio"
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
        concurrency=_positive_integer(ai_data, "ai", "concurrency"),
        timeout_seconds=_finite_number(
            ai_data,
            "ai",
            "timeout_seconds",
            allow_zero=False,
        ),
        temperature=_finite_number(
            ai_data,
            "ai",
            "temperature",
            allow_zero=True,
        ),
        max_tokens=_positive_integer(ai_data, "ai", "max_tokens"),
    )

    return RuntimeConfig(
        version=1,
        runner=runner,
        minio=minio,
        asr=asr,
        ai=ai,
    )
