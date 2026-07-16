from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_PATH = PROJECT_ROOT / "scripts/run_demo.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("procurement_audit_run_demo", LAUNCHER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_requires_current_packaged_vane_runtime():
    launcher = _load_launcher()

    launcher.require_real_vane_runtime()
    import duckdb
    import vane

    prefix = Path(sys.prefix).resolve()
    assert Path(vane.__file__).resolve().is_relative_to(prefix)
    assert Path(duckdb.__file__).resolve().is_relative_to(prefix)
    assert not hasattr(launcher, "REAL_PREFIX")
    assert not hasattr(launcher, "worker_pythonpath")


def test_launcher_freezes_exact_runtime_identifiers():
    launcher = _load_launcher()

    assert launcher.EXPECTED_VANE_DISTRIBUTION_VERSION == "0.1.0.dev20260714234347"
    assert launcher.EXPECTED_VANE_API_VERSION == "0.1.0.dev20260714234347"
    assert launcher.EXPECTED_DUCKDB_PYTHON_VERSION == "0.1.0.dev20260714234347"
    assert launcher.EXPECTED_DUCKDB_ENGINE_VERSION == "v1.6.0-dev121"
    assert launcher.EXPECTED_DUCKDB_SOURCE_REVISION == "ca6948529b"


@pytest.mark.parametrize(
    ("field", "actual"),
    [
        (
            "Vane distribution",
            (
                "wrong",
                "0.1.0.dev20260714234347",
                "0.1.0.dev20260714234347",
                "v1.6.0-dev121",
                "ca6948529b",
            ),
        ),
        (
            "Vane API",
            (
                "0.1.0.dev20260714234347",
                "wrong",
                "0.1.0.dev20260714234347",
                "v1.6.0-dev121",
                "ca6948529b",
            ),
        ),
        (
            "DuckDB Python",
            (
                "0.1.0.dev20260714234347",
                "0.1.0.dev20260714234347",
                "wrong",
                "v1.6.0-dev121",
                "ca6948529b",
            ),
        ),
        (
            "DuckDB engine",
            (
                "0.1.0.dev20260714234347",
                "0.1.0.dev20260714234347",
                "0.1.0.dev20260714234347",
                "wrong",
                "ca6948529b",
            ),
        ),
        (
            "DuckDB source",
            (
                "0.1.0.dev20260714234347",
                "0.1.0.dev20260714234347",
                "0.1.0.dev20260714234347",
                "v1.6.0-dev121",
                "wrong",
            ),
        ),
    ],
)
def test_launcher_rejects_any_runtime_version_mismatch(field, actual):
    launcher = _load_launcher()

    with pytest.raises(RuntimeError, match=field):
        launcher.validate_runtime_versions(*actual)


def test_launcher_public_entry_is_the_only_script():
    assert sorted(path.name for path in (PROJECT_ROOT / "scripts").glob("*.py")) == [
        "run_demo.py"
    ]
