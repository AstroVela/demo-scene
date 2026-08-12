from __future__ import annotations

from pathlib import Path

import pytest

from claims_disposition_sql_pipeline.config import ConfigError, load_runtime_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_checked_in_config_selects_ray_runner():
    config = load_runtime_config(PROJECT_ROOT / "runtime.yml")

    assert config.runner == "ray"
    assert "runner=ray" in config.redacted_summary()


def test_config_rejects_unknown_runner(tmp_path):
    text = (PROJECT_ROOT / "runtime.yml").read_text(encoding="utf-8")
    path = tmp_path / "runtime.yml"
    path.write_text(
        text.replace("runner: ray", "runner: threaded"),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="runner"):
        load_runtime_config(path)
