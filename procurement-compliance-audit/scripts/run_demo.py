#!/usr/bin/env python3
"""Run the demo with the validated public-PyPI Vane package."""

from __future__ import annotations

import os
from importlib import metadata as importlib_metadata
from pathlib import Path
import sys
from collections.abc import Sequence
from urllib.parse import urlparse


PROXY_VARIABLES = (
    "all_proxy",
    "ALL_PROXY",
    "http_proxy",
    "HTTP_PROXY",
    "https_proxy",
    "HTTPS_PROXY",
)
EXPECTED_PYTHON_VERSION = (3, 12)
VANE_DISTRIBUTION_NAME = "vane-ai"
EXPECTED_VANE_DISTRIBUTION_VERSION = "0.1.0a1"
EXPECTED_VANE_API_VERSION = "0.1.0a1"
EXPECTED_DUCKDB_PYTHON_VERSION = "0.1.0a1"
EXPECTED_DUCKDB_ENGINE_VERSION = "v1.6.0-dev1"
EXPECTED_DUCKDB_SOURCE_REVISION = "398033a962"
DEFAULT_VANE_UDF_UNREGISTER_TIMEOUT_MS = "60000"
INSTALL_HINT = "python -m pip install vane-ai"


def _runtime_error(message: str) -> RuntimeError:
    return RuntimeError(
        f"{message}\n"
        f"current interpreter: {sys.executable}\n"
        f"current prefix: {sys.prefix}\n"
        f"install the verified package with:\n  {INSTALL_HINT}\n"
        "then run: python -m pip install -r requirements.txt"
    )


def validate_runtime_versions(
    vane_distribution_version: str,
    vane_api_version: str,
    duckdb_python_version: str,
    duckdb_engine_version: str,
    duckdb_source_revision: str,
) -> None:
    """Fail fast unless every release runtime identifier is exactly pinned."""

    actual_versions = (
        ("Vane distribution", vane_distribution_version, EXPECTED_VANE_DISTRIBUTION_VERSION),
        ("Vane API", vane_api_version, EXPECTED_VANE_API_VERSION),
        ("DuckDB Python", duckdb_python_version, EXPECTED_DUCKDB_PYTHON_VERSION),
        ("DuckDB engine", duckdb_engine_version, EXPECTED_DUCKDB_ENGINE_VERSION),
        ("DuckDB source", duckdb_source_revision, EXPECTED_DUCKDB_SOURCE_REVISION),
    )
    for label, actual, expected in actual_versions:
        if actual != expected:
            raise _runtime_error(
                f"{label} version mismatch; expected {expected!r}, got {actual!r}"
            )


def _require_package_from_current_environment(label: str, package_file: str | None) -> None:
    try:
        package_path = Path(package_file or "").resolve()
        package_path.relative_to(Path(sys.prefix).resolve())
    except (OSError, TypeError, ValueError):
        raise _runtime_error(
            f"{label} must be imported from the current Python environment; "
            f"got {package_file or '<unknown>'}"
        ) from None


def require_real_vane_runtime() -> None:
    """Require the verified Vane wheel in the active Python environment."""

    actual_python = tuple(sys.version_info[:2])
    if actual_python != EXPECTED_PYTHON_VERSION:
        raise _runtime_error(
            "Python version mismatch; expected "
            f"{EXPECTED_PYTHON_VERSION[0]}.{EXPECTED_PYTHON_VERSION[1]}, "
            f"got {actual_python[0]}.{actual_python[1]}"
        )
    try:
        import duckdb
        import vane
    except Exception as exc:
        raise _runtime_error(f"cannot import duckdb and vane: {exc}") from exc
    try:
        connection = duckdb.connect()
        try:
            engine_version, source_revision, _ = connection.execute(
                "pragma version"
            ).fetchone()
        finally:
            connection.close()
        validate_runtime_versions(
            importlib_metadata.version(VANE_DISTRIBUTION_NAME),
            str(getattr(vane, "__version__", "<missing>")),
            str(getattr(duckdb, "__version__", "<missing>")),
            str(engine_version),
            str(source_revision),
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise _runtime_error(f"cannot identify the validated Vane runtime: {exc}") from exc
    missing = [
        name
        for name in ("func", "cls", "attach_function", "configure")
        if not callable(getattr(vane, name, None))
    ]
    if missing:
        raise _runtime_error(
            "real Vane runtime is missing callable API: "
            + ", ".join(f"vane.{name}" for name in missing)
        )
    if not callable(getattr(getattr(vane, "ai", None), "prompt", None)):
        raise _runtime_error("real Vane runtime is missing vane.ai.prompt")
    _require_package_from_current_environment("vane", getattr(vane, "__file__", None))
    _require_package_from_current_environment(
        "duckdb", getattr(duckdb, "__file__", None)
    )
    if not hasattr(duckdb, "ray_cxx"):
        raise _runtime_error("real Vane runtime DuckDB is missing duckdb.ray_cxx")


def configure_loopback_network(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        return
    for name in PROXY_VARIABLES:
        os.environ.pop(name, None)
    required_hosts = ("localhost", "127.0.0.1", "::1")
    for name in ("NO_PROXY", "no_proxy"):
        entries = [item.strip() for item in os.environ.get(name, "").split(",")]
        entries = [item for item in entries if item]
        for host in required_hosts:
            if host not in entries:
                entries.append(host)
        os.environ[name] = ",".join(entries)


def main(argv: Sequence[str] | None = None) -> int:
    os.environ.setdefault(
        "VANE_UDF_UNREGISTER_TIMEOUT_MS",
        DEFAULT_VANE_UDF_UNREGISTER_TIMEOUT_MS,
    )
    require_real_vane_runtime()
    from procurement_audit_sql_demo.config import load_runtime_config

    config = load_runtime_config()
    configure_loopback_network(config.ai.base_url)
    from procurement_audit_sql_demo.cli import main as cli_main

    return cli_main(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
