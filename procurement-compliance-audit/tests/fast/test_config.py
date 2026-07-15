from __future__ import annotations

from pathlib import Path

import pytest

from procurement_audit_sql_demo.config import ConfigError, load_runtime_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VALID_YAML = """\
version: 1
runner: local
fixture_dir: fixtures/expert-score-anomaly
output_dir: output
ocr:
  engine: rapidocr
  device: cpu
  minimum_confidence: 0.60
ai:
  provider: openai
  base_url: http://127.0.0.1:8001/v1
  health_url: http://127.0.0.1:8001/health
  api_key: dummy
  model: Qwen2.5-VL-3B-Instruct
  concurrency: 1
  timeout_seconds: 120.0
  temperature: 0.0
  max_tokens: 512
"""


def test_checked_in_config_selects_real_qwen_and_local_runner():
    config = load_runtime_config(PROJECT_ROOT / "runtime.yml")

    assert config.runner == "local"
    assert config.fixture_dir == PROJECT_ROOT / "fixtures/expert-score-anomaly"
    assert config.output_dir == PROJECT_ROOT / "output"
    assert config.ai.provider == "openai"
    assert config.ai.model == "Qwen2.5-VL-3B-Instruct"
    assert config.ai.base_url == "http://127.0.0.1:8001/v1"
    assert config.ai.health_url == "http://127.0.0.1:8001/health"
    assert config.ocr.engine == "rapidocr"


def test_config_rejects_non_loopback_ai_endpoint(tmp_path):
    path = tmp_path / "runtime.yml"
    path.write_text(
        VALID_YAML.replace(
            "base_url: http://127.0.0.1:8001/v1",
            "base_url: https://models.example.com/v1",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="loopback HTTP URL"):
        load_runtime_config(path)


@pytest.mark.parametrize("runner", ["native", "threaded", ""])
def test_config_rejects_unknown_runner(tmp_path, runner):
    path = tmp_path / "runtime.yml"
    path.write_text(
        VALID_YAML.replace("runner: local", f"runner: {runner}"),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="runner"):
        load_runtime_config(path)
