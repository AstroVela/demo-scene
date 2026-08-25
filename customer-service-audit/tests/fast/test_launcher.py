from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_PATH = PROJECT_ROOT / "scripts/run_demo.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location(
        "customer_service_audit_run_demo",
        LAUNCHER_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _complete_vane_api() -> SimpleNamespace:
    return SimpleNamespace(
        func=lambda: None,
        cls=lambda: None,
        attach_function=lambda: None,
        configure=lambda: None,
        ai=SimpleNamespace(prompt=lambda: None, load_provider=lambda: None),
        ray_cxx=object(),
    )


def test_runtime_api_requires_stateful_actor_and_ray_bridge() -> None:
    launcher = _load_launcher()
    without_cls = _complete_vane_api()
    without_cls.cls = None

    with pytest.raises(RuntimeError, match=r"vane\.cls"):
        launcher.validate_runtime_api(without_cls)

    without_bridge = _complete_vane_api()
    del without_bridge.ray_cxx
    with pytest.raises(RuntimeError, match=r"vane\.ray_cxx"):
        launcher.validate_runtime_api(without_bridge)


def test_install_hint_uses_the_public_release() -> None:
    launcher = _load_launcher()

    assert launcher.INSTALL_HINT == "uv pip install 'vane-ai[openai]==0.1.0'"


def test_config_path_argument_parsing(monkeypatch):
    launcher = _load_launcher()

    assert launcher._config_path_argument(["run", "--config", "a.yml"]) == "a.yml"
    assert launcher._config_path_argument(["run", "--config=a.yml"]) == "a.yml"
    assert launcher._config_path_argument(["e2e"]) is None
    # A trailing --config without a value must not crash the launcher.
    assert launcher._config_path_argument(["run", "--config"]) is None


def test_launcher_uses_explicit_config_on_clean_checkout(monkeypatch):
    # On a clean checkout only runtime.example.yml exists; the launcher must
    # honor --config instead of failing on the missing default runtime.yml.
    import customer_service_audit.cli as cli_module
    import customer_service_audit.config as config_module
    from customer_service_audit.config import EXAMPLE_CONFIG_PATH

    launcher = _load_launcher()
    monkeypatch.setattr(launcher, "require_real_vane_runtime", lambda: None)
    monkeypatch.setattr(
        config_module, "DEFAULT_CONFIG_PATH", config_module.PROJECT_ROOT / "missing-runtime.yml"
    )
    seen: dict[str, object] = {}

    def fake_cli(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(cli_module, "main", fake_cli)

    exit_code = launcher.main(["run", "--config", str(EXAMPLE_CONFIG_PATH)])

    assert exit_code == 0
    assert seen["argv"] == ["run", "--config", str(EXAMPLE_CONFIG_PATH)]


def test_launcher_reports_missing_default_config_with_copy_hint(
    monkeypatch, capsys
):
    import customer_service_audit.config as config_module

    launcher = _load_launcher()
    monkeypatch.setattr(launcher, "require_real_vane_runtime", lambda: None)
    monkeypatch.setattr(
        config_module, "DEFAULT_CONFIG_PATH", config_module.PROJECT_ROOT / "missing-runtime.yml"
    )

    exit_code = launcher.main(["run"])

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert "runtime configuration error" in stderr
    assert "cp " in stderr and "runtime.example.yml" in stderr


def test_loopback_bypass_preserves_proxy_for_remote_dependencies(monkeypatch):
    launcher = _load_launcher()
    proxy = "http://proxy.example:8080"
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    monkeypatch.setenv("NO_PROXY", "internal.example")
    monkeypatch.setenv("no_proxy", "internal.example")

    launcher.configure_loopback_network("http://localhost:8001/v1")

    assert launcher.os.environ["HTTPS_PROXY"] == proxy
    for name in ("NO_PROXY", "no_proxy"):
        assert launcher.os.environ[name].split(",") == [
            "internal.example",
            "localhost",
            "127.0.0.1",
            "::1",
        ]
