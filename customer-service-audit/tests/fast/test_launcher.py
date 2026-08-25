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


def test_exact_pinned_version_is_accepted() -> None:
    launcher = _load_launcher()

    assert launcher.validate_runtime_versions("0.1.0", "0.1.0") is None


def test_newer_local_development_build_is_accepted() -> None:
    launcher = _load_launcher()

    # setuptools-scm builds from a checkout ahead of the v0.1.0 tag
    # report a development version such as 0.2.0.dev553.
    assert launcher.validate_runtime_versions("0.2.0.dev553", "0.2.0.dev553") is None


def test_old_or_unrelated_versions_are_rejected() -> None:
    launcher = _load_launcher()

    with pytest.raises(RuntimeError, match="distribution"):
        launcher.validate_runtime_versions("0.0.9", "0.1.0")
    with pytest.raises(RuntimeError, match="distribution"):
        launcher.validate_runtime_versions("0.2.0.dev553.1", "0.1.0")
    with pytest.raises(RuntimeError, match="API"):
        launcher.validate_runtime_versions("0.1.0", "0.0.5")


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
