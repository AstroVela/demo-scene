from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_PATH = PROJECT_ROOT / "scripts/run_demo.py"
VANE_VERSION = "0.1.0"
VANE_ENGINE_VERSION = "v1.5.0-vane.b1c745e9c4"
VANE_SOURCE_REVISION = "0c2adbf409"
VANE_INSTALL = "uv pip install 'vane-ai[openai]==0.1.0'"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("procurement_audit_run_demo", LAUNCHER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_requires_current_packaged_vane_runtime():
    launcher = _load_launcher()

    launcher.require_real_vane_runtime()
    import vane

    prefix = Path(sys.prefix).resolve()
    assert Path(vane.__file__).resolve().is_relative_to(prefix)
    assert not hasattr(launcher, "REAL_PREFIX")
    assert not hasattr(launcher, "worker_pythonpath")


def test_launcher_freezes_exact_runtime_identifiers():
    launcher = _load_launcher()

    assert launcher.EXPECTED_VANE_DISTRIBUTION_VERSION == VANE_VERSION
    assert launcher.EXPECTED_VANE_API_VERSION == VANE_VERSION
    assert launcher.EXPECTED_VANE_ENGINE_VERSION == VANE_ENGINE_VERSION
    assert launcher.EXPECTED_VANE_SOURCE_REVISION == VANE_SOURCE_REVISION
    assert launcher.INSTALL_HINT == VANE_INSTALL
    assert launcher.DEFAULT_VANE_UDF_UNREGISTER_TIMEOUT_MS == "60000"


@pytest.mark.parametrize(
    ("field", "actual"),
    [
        (
            "Vane distribution",
            (
                "wrong",
                VANE_VERSION,
                VANE_ENGINE_VERSION,
                VANE_SOURCE_REVISION,
            ),
        ),
        (
            "Vane API",
            (
                VANE_VERSION,
                "wrong",
                VANE_ENGINE_VERSION,
                VANE_SOURCE_REVISION,
            ),
        ),
        (
            "Vane engine",
            (
                VANE_VERSION,
                VANE_VERSION,
                "wrong",
                VANE_SOURCE_REVISION,
            ),
        ),
        (
            "Vane source",
            (
                VANE_VERSION,
                VANE_VERSION,
                VANE_ENGINE_VERSION,
                "wrong",
            ),
        ),
    ],
)
def test_launcher_rejects_any_runtime_version_mismatch(field, actual):
    launcher = _load_launcher()

    with pytest.raises(RuntimeError, match=field):
        launcher.validate_runtime_versions(*actual)


def test_loopback_bypass_preserves_proxy_for_remote_dependencies(monkeypatch):
    launcher = _load_launcher()
    proxy = "http://proxy.example:8080"
    monkeypatch.setenv("HTTPS_PROXY", proxy)
    monkeypatch.setenv("NO_PROXY", "internal.example")
    monkeypatch.setenv("no_proxy", "internal.example")

    launcher.configure_loopback_network("http://127.0.0.1:8001/v1")

    assert launcher.os.environ["HTTPS_PROXY"] == proxy
    for name in ("NO_PROXY", "no_proxy"):
        assert launcher.os.environ[name].split(",") == [
            "internal.example",
            "localhost",
            "127.0.0.1",
            "::1",
        ]
