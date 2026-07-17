from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_PATH = PROJECT_ROOT / "scripts/run_demo.py"
VANE_VERSION = "0.1.0a1"
DUCKDB_ENGINE_VERSION = "v1.6.0-dev1"
DUCKDB_SOURCE_REVISION = "398033a962"


def _load_launcher():
    assert LAUNCHER_PATH.is_file(), "public launcher must be scripts/run_demo.py"
    spec = importlib.util.spec_from_file_location("claims_run_demo", LAUNCHER_PATH)
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
        ai=SimpleNamespace(prompt=lambda: None),
    )


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
    assert not hasattr(launcher, "configure_import_paths")


def test_launcher_freezes_exact_runtime_identifiers():
    launcher = _load_launcher()

    assert launcher.EXPECTED_PYTHON_VERSION == (3, 12)
    assert launcher.VANE_DISTRIBUTION_NAME == "vane-ai"
    assert launcher.EXPECTED_VANE_DISTRIBUTION_VERSION == VANE_VERSION
    assert launcher.EXPECTED_VANE_API_VERSION == VANE_VERSION
    assert launcher.EXPECTED_DUCKDB_PYTHON_VERSION == VANE_VERSION
    assert launcher.EXPECTED_DUCKDB_ENGINE_VERSION == DUCKDB_ENGINE_VERSION
    assert launcher.EXPECTED_DUCKDB_SOURCE_REVISION == DUCKDB_SOURCE_REVISION
    assert launcher.INSTALL_HINT == "python -m pip install vane-ai"
    assert launcher.DEFAULT_VANE_UDF_UNREGISTER_TIMEOUT_MS == "60000"


def test_launcher_rejects_wrong_python_version_with_install_help():
    launcher = _load_launcher()

    with pytest.raises(RuntimeError) as raised:
        launcher.validate_python_version((3, 11))

    message = str(raised.value)
    assert "Python version mismatch" in message
    assert sys.executable in message
    assert sys.prefix in message
    assert "python -m pip install vane-ai" in message
    obsolete_index = ".".join(("test", "pypi", "org"))
    assert obsolete_index not in message
    assert "python -m pip install -r requirements.txt" in message


@pytest.mark.parametrize(
    ("field", "actual"),
    [
        (
            "Vane distribution",
            (
                "wrong",
                VANE_VERSION,
                VANE_VERSION,
                DUCKDB_ENGINE_VERSION,
                DUCKDB_SOURCE_REVISION,
            ),
        ),
        (
            "Vane API",
            (
                VANE_VERSION,
                "wrong",
                VANE_VERSION,
                DUCKDB_ENGINE_VERSION,
                DUCKDB_SOURCE_REVISION,
            ),
        ),
        (
            "DuckDB Python",
            (
                VANE_VERSION,
                VANE_VERSION,
                "wrong",
                DUCKDB_ENGINE_VERSION,
                DUCKDB_SOURCE_REVISION,
            ),
        ),
        (
            "DuckDB engine",
            (
                VANE_VERSION,
                VANE_VERSION,
                VANE_VERSION,
                "wrong",
                DUCKDB_SOURCE_REVISION,
            ),
        ),
        (
            "DuckDB source",
            (
                VANE_VERSION,
                VANE_VERSION,
                VANE_VERSION,
                DUCKDB_ENGINE_VERSION,
                "wrong",
            ),
        ),
    ],
)
def test_launcher_rejects_any_runtime_version_mismatch(field, actual):
    launcher = _load_launcher()

    with pytest.raises(RuntimeError, match=field):
        launcher.validate_runtime_versions(*actual)


def test_launcher_rejects_missing_callable_api():
    launcher = _load_launcher()

    with pytest.raises(RuntimeError, match="callable API"):
        launcher.validate_runtime_api(
            SimpleNamespace(),
            SimpleNamespace(ray_cxx=True),
        )


def test_launcher_rejects_missing_ai_prompt():
    launcher = _load_launcher()
    vane_without_prompt = _complete_vane_api()
    vane_without_prompt.ai = SimpleNamespace()

    with pytest.raises(RuntimeError, match=r"vane\.ai\.prompt"):
        launcher.validate_runtime_api(
            vane_without_prompt,
            SimpleNamespace(ray_cxx=True),
        )


def test_launcher_rejects_missing_ray_bridge():
    launcher = _load_launcher()

    with pytest.raises(RuntimeError, match="ray_cxx"):
        launcher.validate_runtime_api(_complete_vane_api(), SimpleNamespace())


def test_launcher_rejects_package_outside_current_environment():
    launcher = _load_launcher()

    with pytest.raises(RuntimeError, match="current Python environment"):
        launcher.require_package_from_current_environment(
            "vane",
            "/opt/unrelated/site-packages/vane/__init__.py",
        )


def test_launcher_public_entry_is_the_only_script():
    assert sorted(path.name for path in (PROJECT_ROOT / "scripts").glob("*.py")) == [
        "run_demo.py"
    ]


def test_cli_usage_names_the_public_launcher():
    from claims_disposition_sql_pipeline import cli

    with pytest.raises(SystemExit) as raised:
        cli.main([])

    message = str(raised.value)
    assert "run_demo.py" in message
    assert "run_with_vane.py" not in message
