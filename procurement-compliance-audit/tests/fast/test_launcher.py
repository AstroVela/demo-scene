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

    assert launcher.EXPECTED_VANE_DISTRIBUTION_VERSION == "0.1.0a1"
    assert launcher.EXPECTED_VANE_API_VERSION == "0.1.0a1"
    assert launcher.EXPECTED_DUCKDB_PYTHON_VERSION == "0.1.0a1"
    assert launcher.EXPECTED_DUCKDB_ENGINE_VERSION == "v1.6.0-dev1"
    assert launcher.EXPECTED_DUCKDB_SOURCE_REVISION == "398033a962"
    assert launcher.INSTALL_HINT == "python -m pip install vane-ai"
    assert launcher.DEFAULT_VANE_UDF_UNREGISTER_TIMEOUT_MS == "60000"


@pytest.mark.parametrize(
    ("field", "actual"),
    [
        (
            "Vane distribution",
            (
                "wrong",
                "0.1.0a1",
                "0.1.0a1",
                "v1.6.0-dev1",
                "398033a962",
            ),
        ),
        (
            "Vane API",
            (
                "0.1.0a1",
                "wrong",
                "0.1.0a1",
                "v1.6.0-dev1",
                "398033a962",
            ),
        ),
        (
            "DuckDB Python",
            (
                "0.1.0a1",
                "0.1.0a1",
                "wrong",
                "v1.6.0-dev1",
                "398033a962",
            ),
        ),
        (
            "DuckDB engine",
            (
                "0.1.0a1",
                "0.1.0a1",
                "0.1.0a1",
                "wrong",
                "398033a962",
            ),
        ),
        (
            "DuckDB source",
            (
                "0.1.0a1",
                "0.1.0a1",
                "0.1.0a1",
                "v1.6.0-dev1",
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
