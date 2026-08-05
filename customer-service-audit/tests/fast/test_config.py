from __future__ import annotations

from pathlib import Path

import pytest

from customer_service_audit.config import (
    ConfigError,
    DEFAULT_CONFIG_PATH,
    load_runtime_config,
)


def test_default_config_loads_and_is_secret_light() -> None:
    config = load_runtime_config(DEFAULT_CONFIG_PATH)
    assert config.version == 1
    assert config.runner in {"local", "ray"}
    assert config.asr.engine == "faster-whisper"
    assert config.ai.provider == "openai"
    assert config.minio.recordings_prefix.endswith("/")
    assert config.minio.analysis_prefix.endswith("/")
    # The run configuration row must stay free of credentials.
    assert "secret" not in config.redacted_summary().lower()
    assert config.minio.secret_key not in config.redacted_summary()


def test_missing_section_raises(tmp_path: Path) -> None:
    bad = tmp_path / "runtime.yml"
    bad.write_text("version: 1\nrunner: local\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_runtime_config(bad)


def test_invalid_runner_rejected(tmp_path: Path) -> None:
    content = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    bad = tmp_path / "runtime.yml"
    bad.write_text(content.replace("runner: local", "runner: spark"), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_runtime_config(bad)


def test_invalid_version_rejected(tmp_path: Path) -> None:
    content = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    bad = tmp_path / "runtime.yml"
    bad.write_text(content.replace("version: 1", "version: 2"), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_runtime_config(bad)


def test_non_loopback_ai_url_rejected(tmp_path: Path) -> None:
    content = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    bad = tmp_path / "runtime.yml"
    bad.write_text(
        content.replace("http://127.0.0.1:8001/v1", "https://api.example.com/v1"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_runtime_config(bad)
