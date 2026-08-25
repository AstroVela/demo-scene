from __future__ import annotations

from pathlib import Path

import pytest

from customer_service_audit.config import (
    ConfigError,
    DEFAULT_CONFIG_PATH,
    EXAMPLE_CONFIG_PATH,
    load_runtime_config,
)

_FASTER_WHISPER_ASR_BLOCK = (
    "asr:\n"
    "  engine: faster-whisper\n"
    "  model: small\n"
    "  device: cpu\n"
    "  compute_type: int8\n"
    "  language: zh\n"
    "  beam_size: 5\n"
    "  min_text_chars: 8"
)

_OPENAI_AUDIO_ASR_TEMPLATE = (
    "asr:\n"
    "  engine: openai-audio\n"
    "  model: qwen-asr\n"
    "  base_url: {base_url}\n"
    "  api_key: your-asr-api-key\n"
    "  language: zh\n"
    "{timeout_line}"
    "  min_text_chars: 8"
)

_OPENAI_AUDIO_TIMEOUT_LINE = "  timeout_seconds: 120.0\n"


def _example_content() -> str:
    return EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8")


def _openai_audio_config(
    tmp_path: Path, base_url: str, timeout_line: str = _OPENAI_AUDIO_TIMEOUT_LINE
) -> Path:
    content = _example_content()
    assert _FASTER_WHISPER_ASR_BLOCK in content
    replacement = _OPENAI_AUDIO_ASR_TEMPLATE.format(
        base_url=base_url, timeout_line=timeout_line
    )
    path = tmp_path / "runtime.yml"
    path.write_text(
        content.replace(_FASTER_WHISPER_ASR_BLOCK, replacement), encoding="utf-8"
    )
    return path


def test_example_config_loads_and_is_secret_light() -> None:
    # Tests run against the checked-in example so they pass on a clean clone.
    config = load_runtime_config(EXAMPLE_CONFIG_PATH)
    assert config.version == 1
    # The example keeps the documented, verified execution shape.
    assert config.runner == "ray"
    assert config.asr.engine == "faster-whisper"
    assert config.asr.model == "small"
    assert config.ai.provider == "openai"
    assert config.ai.base_url == "http://127.0.0.1:8001/v1"
    assert config.ai.health_url == "http://127.0.0.1:8001/health"
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
    bad = tmp_path / "runtime.yml"
    bad.write_text(
        _example_content().replace("runner: ray", "runner: spark"), encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_runtime_config(bad)


def test_invalid_version_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "runtime.yml"
    bad.write_text(
        _example_content().replace("version: 1", "version: 2"), encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_runtime_config(bad)


def test_remote_ai_url_accepted(tmp_path: Path) -> None:
    remote = tmp_path / "runtime.yml"
    remote.write_text(
        _example_content()
        .replace("http://127.0.0.1:8001/v1", "https://ai-gateway.internal/v1")
        .replace("http://127.0.0.1:8001/health", "https://ai-gateway.internal/health"),
        encoding="utf-8",
    )
    config = load_runtime_config(remote)
    assert config.ai.base_url == "https://ai-gateway.internal/v1"
    assert config.ai.health_url == "https://ai-gateway.internal/health"


def test_invalid_ai_url_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "runtime.yml"
    bad.write_text(
        _example_content().replace("http://127.0.0.1:8001/v1", "not-a-url"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_runtime_config(bad)


def test_missing_default_config_gives_copy_hint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import customer_service_audit.config as config_module

    missing = tmp_path / "runtime.yml"
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", missing)
    with pytest.raises(ConfigError, match="cp .*runtime.example.yml"):
        load_runtime_config(missing)


def test_openai_audio_config_parses_with_timeout(tmp_path: Path) -> None:
    config = load_runtime_config(
        _openai_audio_config(tmp_path, "http://127.0.0.1:8005/v1")
    )
    assert config.asr.engine == "openai-audio"
    assert config.asr.base_url == "http://127.0.0.1:8005/v1"
    # The transcription request uses this field; it must exist on the config.
    assert config.asr.timeout_seconds == pytest.approx(120.0)


def test_openai_audio_requires_timeout_seconds(tmp_path: Path) -> None:
    path = _openai_audio_config(tmp_path, "http://127.0.0.1:8005/v1", timeout_line="")
    with pytest.raises(ConfigError, match="asr.timeout_seconds"):
        load_runtime_config(path)


@pytest.mark.parametrize("bad_timeout", ["  timeout_seconds: 0.0\n", "  timeout_seconds: -5.0\n"])
def test_openai_audio_rejects_non_positive_timeout(tmp_path: Path, bad_timeout: str) -> None:
    path = _openai_audio_config(
        tmp_path, "http://127.0.0.1:8005/v1", timeout_line=bad_timeout
    )
    with pytest.raises(ConfigError, match="asr.timeout_seconds"):
        load_runtime_config(path)


@pytest.mark.parametrize("bad_url", [
    "http://127.0.0.1:not-a-port/v1",
    "http://127.0.0.1:99999/v1",
    "http://127.0.0.1:0/v1",
    "ftp://127.0.0.1:8005/v1",
])
def test_openai_audio_rejects_malformed_base_url(tmp_path: Path, bad_url: str) -> None:
    with pytest.raises(ConfigError, match="asr.base_url"):
        load_runtime_config(_openai_audio_config(tmp_path, bad_url))


def test_openai_audio_accepts_https_and_omitted_port(tmp_path: Path) -> None:
    config = load_runtime_config(
        _openai_audio_config(tmp_path, "https://asr.example.com/v1")
    )
    assert config.asr.base_url == "https://asr.example.com/v1"


def test_local_runtime_yml_loads_when_present() -> None:
    # The personal git-ignored runtime.yml must load with the same rules.
    if not DEFAULT_CONFIG_PATH.exists():
        pytest.skip("personal runtime.yml not present")
    config = load_runtime_config(DEFAULT_CONFIG_PATH)
    assert config.version == 1
    assert config.runner in {"local", "ray"}
