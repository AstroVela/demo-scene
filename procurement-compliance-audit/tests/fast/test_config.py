from __future__ import annotations

from pathlib import Path

import pytest

from procurement_audit_sql_demo.config import ConfigError, load_runtime_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]
VALID_YAML = """\
version: 1
runner: local
output_dir: output
postgres:
  dsn: postgresql://vane_insight:password@127.0.0.1:5432/vane_insight
  raw_schema: procurement_audit_raw
  project_table: projects
  supplier_table: suppliers
  score_table: expert_scores
  evidence_table: evidence_files
minio:
  endpoint: 127.0.0.1:9000
  access_key: vaneinsight
  secret_key: password
  secure: false
  bucket: procurement-compliance-audit-fixtures
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


def test_checked_in_config_selects_real_qwen_and_ray_runner():
    config = load_runtime_config(PROJECT_ROOT / "runtime.yml")

    assert config.runner == "ray"
    assert config.output_dir == PROJECT_ROOT / "output"
    assert config.postgres.raw_relation_names == (
        "procurement_audit_raw.projects",
        "procurement_audit_raw.suppliers",
        "procurement_audit_raw.expert_scores",
        "procurement_audit_raw.evidence_files",
    )
    assert config.minio.endpoint == "127.0.0.1:9000"
    assert config.minio.bucket == "procurement-compliance-audit-fixtures"
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


def test_config_rejects_invalid_postgres_identifier(tmp_path):
    path = tmp_path / "runtime.yml"
    path.write_text(
        VALID_YAML.replace("raw_schema: procurement_audit_raw", "raw_schema: bad-name"),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="SQL identifier"):
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
