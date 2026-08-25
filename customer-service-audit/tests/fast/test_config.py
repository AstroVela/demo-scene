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
    assert config.runner == "ray"
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
    bad.write_text(content.replace("runner: ray", "runner: spark"), encoding="utf-8")
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


_OPENAI_AUDIO_ASR_BLOCK = """asr:
  engine: openai-audio
  model: qwen-asr
  language: zh
  min_text_chars: 8
  base_url: http://127.0.0.1:8005/v1
  api_key: dummy
  timeout_seconds: 30.0
"""

_FASTER_WHISPER_ASR_BLOCK = """asr:
  engine: faster-whisper
  model: small
  device: cpu
  compute_type: int8
  language: zh
  beam_size: 5
  min_text_chars: 8
"""


def _openai_audio_config(tmp_path: Path, asr_block: str) -> Path:
    content = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    assert _FASTER_WHISPER_ASR_BLOCK.strip() in content
    path = tmp_path / "runtime.yml"
    path.write_text(
        content.replace(_FASTER_WHISPER_ASR_BLOCK.strip(), asr_block.strip()),
        encoding="utf-8",
    )
    return path


def test_openai_audio_config_parses_with_timeout(tmp_path: Path) -> None:
    config = load_runtime_config(_openai_audio_config(tmp_path, _OPENAI_AUDIO_ASR_BLOCK))
    assert config.asr.engine == "openai-audio"
    assert config.asr.base_url == "http://127.0.0.1:8005/v1"
    # The transcription request uses this field; it must exist on the config.
    assert config.asr.timeout_seconds == pytest.approx(30.0)


def test_openai_audio_requires_timeout_seconds(tmp_path: Path) -> None:
    block = _OPENAI_AUDIO_ASR_BLOCK.replace("  timeout_seconds: 30.0\n", "")
    with pytest.raises(ConfigError, match="asr.timeout_seconds"):
        load_runtime_config(_openai_audio_config(tmp_path, block))


@pytest.mark.parametrize("bad_timeout", ["  timeout_seconds: 0.0\n", "  timeout_seconds: -5.0\n"])
def test_openai_audio_rejects_non_positive_timeout(tmp_path: Path, bad_timeout: str) -> None:
    block = _OPENAI_AUDIO_ASR_BLOCK.replace("  timeout_seconds: 30.0\n", bad_timeout)
    with pytest.raises(ConfigError, match="asr.timeout_seconds"):
        load_runtime_config(_openai_audio_config(tmp_path, block))


@pytest.mark.parametrize("bad_url", [
    "http://127.0.0.1:not-a-port/v1",
    "http://127.0.0.1:99999/v1",
    "http://127.0.0.1:0/v1",
    "ftp://127.0.0.1:8005/v1",
])
def test_openai_audio_rejects_malformed_base_url(tmp_path: Path, bad_url: str) -> None:
    block = _OPENAI_AUDIO_ASR_BLOCK.replace(
        "base_url: http://127.0.0.1:8005/v1", f"base_url: {bad_url}"
    )
    with pytest.raises(ConfigError, match="asr.base_url"):
        load_runtime_config(_openai_audio_config(tmp_path, block))


def test_openai_audio_accepts_https_and_omitted_port(tmp_path: Path) -> None:
    block = _OPENAI_AUDIO_ASR_BLOCK.replace(
        "base_url: http://127.0.0.1:8005/v1", "base_url: https://asr.example.com/v1"
    )
    config = load_runtime_config(_openai_audio_config(tmp_path, block))
    assert config.asr.base_url == "https://asr.example.com/v1"
