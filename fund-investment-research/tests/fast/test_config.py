from pathlib import Path

import pytest
import yaml

from fund_investment_research.config import ConfigError, load_runtime_config


PROJECT = Path(__file__).resolve().parents[2]


def test_runtime_is_ray_only():
    config = load_runtime_config(PROJECT / "runtime.yml")
    assert config.runner == "ray"


def test_local_runner_is_rejected(tmp_path):
    raw = yaml.safe_load((PROJECT / "runtime.yml").read_text(encoding="utf-8"))
    raw["runner"] = "local"
    path = tmp_path / "runtime.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="runner must be ray"):
        load_runtime_config(path)


def test_unknown_config_field_is_rejected(tmp_path):
    raw = yaml.safe_load((PROJECT / "runtime.yml").read_text(encoding="utf-8"))
    raw["benchmark"] = True
    path = tmp_path / "runtime.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ConfigError, match="extra=.*benchmark"):
        load_runtime_config(path)
