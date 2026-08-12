#!/usr/bin/env python3
"""Run the demo with the validated Vane package."""

from __future__ import annotations

from collections.abc import Sequence
from importlib import metadata as importlib_metadata
import os
from pathlib import Path
import sys
from urllib.parse import urlparse


EXPECTED_PYTHON_VERSION = (3, 12)
VANE_DISTRIBUTION_NAME = "vane-ai"
EXPECTED_VANE_DISTRIBUTION_VERSION = "0.1.0"
EXPECTED_VANE_API_VERSION = "0.1.0"
EXPECTED_VANE_ENGINE_VERSION = "v1.5.0-vane.b1c745e9c4"
EXPECTED_VANE_SOURCE_REVISION = "0c2adbf409"
DEFAULT_VANE_UDF_UNREGISTER_TIMEOUT_MS = "60000"
INSTALL_HINT = "uv pip install 'vane-ai[openai]==0.1.0'"


def _runtime_error(message: str) -> RuntimeError:
    return RuntimeError(
        f"{message}\n"
        f"current interpreter: {sys.executable}\n"
        f"current prefix: {sys.prefix}\n"
        f"install the verified package with:\n  {INSTALL_HINT}\n"
        "then run: uv pip install -r requirements.txt"
    )


def validate_python_version(actual: tuple[int, int]) -> None:
    """Require the CPython major/minor version used for release validation."""

    if actual != EXPECTED_PYTHON_VERSION:
        expected = ".".join(str(part) for part in EXPECTED_PYTHON_VERSION)
        current = ".".join(str(part) for part in actual)
        raise _runtime_error(
            f"Python version mismatch; expected {expected}, got {current}"
        )


def validate_runtime_versions(
    vane_distribution_version: str,
    vane_api_version: str,
    vane_engine_version: str,
    vane_source_revision: str,
) -> None:
    """Fail fast unless every verified runtime identifier is exact."""

    actual_versions = (
        (
            "Vane distribution",
            vane_distribution_version,
            EXPECTED_VANE_DISTRIBUTION_VERSION,
        ),
        ("Vane API", vane_api_version, EXPECTED_VANE_API_VERSION),
        ("Vane engine", vane_engine_version, EXPECTED_VANE_ENGINE_VERSION),
        ("Vane source", vane_source_revision, EXPECTED_VANE_SOURCE_REVISION),
    )
    for label, actual, expected in actual_versions:
        if actual != expected:
            raise _runtime_error(
                f"{label} version mismatch; expected {expected!r}, got {actual!r}"
            )


def validate_runtime_api(vane_module: object) -> None:
    """Require the Vane APIs and Ray bridge used by this Demo."""

    missing = [
        name
        for name in ("func", "cls", "attach_function", "configure")
        if not callable(getattr(vane_module, name, None))
    ]
    if missing:
        raise _runtime_error(
            "real Vane runtime is missing callable API: "
            + ", ".join(f"vane.{name}" for name in missing)
        )
    ai_module = getattr(vane_module, "ai", None)
    for name in ("prompt", "load_provider"):
        if not callable(getattr(ai_module, name, None)):
            raise _runtime_error(
                f"real Vane runtime is missing callable vane.ai.{name}"
            )
    if not hasattr(vane_module, "ray_cxx"):
        raise _runtime_error("real Vane runtime is missing vane.ray_cxx")


def require_package_from_current_environment(
    label: str,
    package_file: str | None,
) -> None:
    """Require a package module to live below the active Python prefix."""

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

    validate_python_version(tuple(sys.version_info[:2]))
    try:
        import vane
    except Exception as exc:
        raise _runtime_error(f"cannot import vane: {exc}") from exc

    try:
        connection = vane.connect()
        try:
            engine_version, source_revision, _ = connection.execute(
                "pragma version"
            ).fetchone()
        finally:
            connection.close()
        validate_runtime_versions(
            importlib_metadata.version(VANE_DISTRIBUTION_NAME),
            str(getattr(vane, "__version__", "<missing>")),
            str(engine_version),
            str(source_revision),
        )
    except RuntimeError:
        raise
    except Exception as exc:
        raise _runtime_error(f"cannot identify the validated Vane runtime: {exc}") from exc

    validate_runtime_api(vane)
    require_package_from_current_environment("vane", getattr(vane, "__file__", None))


def configure_loopback_network(base_url: str) -> None:
    """Bypass proxies for loopback AI calls without affecting remote traffic."""

    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        return

    required_hosts = ("localhost", "127.0.0.1", "::1")
    for name in ("NO_PROXY", "no_proxy"):
        entries = [item.strip() for item in os.environ.get(name, "").split(",")]
        entries = [item for item in entries if item]
        for host in required_hosts:
            if host not in entries:
                entries.append(host)
        os.environ[name] = ",".join(entries)


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the runtime, configure loopback access, and dispatch a command."""

    os.environ.setdefault(
        "VANE_UDF_UNREGISTER_TIMEOUT_MS",
        DEFAULT_VANE_UDF_UNREGISTER_TIMEOUT_MS,
    )
    require_real_vane_runtime()

    from claims_disposition_sql_pipeline.config import load_runtime_config

    config = load_runtime_config()
    configure_loopback_network(config.ai.base_url)

    from claims_disposition_sql_pipeline.cli import main as cli_main

    return cli_main(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
