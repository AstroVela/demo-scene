#!/usr/bin/env python3
"""Run with the image-capable local Vane build under ~/vane."""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Sequence
from importlib import metadata as importlib_metadata
from pathlib import Path
import sys
from urllib.parse import unquote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
EXPECTED_PYTHON = (3, 11)
EXPECTED_VANE_VERSION = "0.1.0a1"
EXPECTED_DUCKDB_VERSION = "0.1.0a1"
EXPECTED_ENGINE_VERSION = "v1.6.0-dev2"
EXPECTED_SOURCE_REVISION = "1b98b61172"
PROXY_VARIABLES = (
    "ALL_PROXY",
    "all_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
)


def _local_vane_root() -> Path:
    return Path(os.environ.get("VANE_LOCAL_ROOT", "~/vane")).expanduser().resolve()


def _install_hint() -> str:
    root = _local_vane_root()
    return (
        f"uv venv --python {root / '.venv/bin/python'} .venv\n"
        f"uv pip install --python .venv/bin/python "
        f"{root / 'dist/vane_ai-0.1.0a1-cp311-cp311-linux_x86_64.whl'}\n"
        "uv pip install --python .venv/bin/python -e '.[test]'"
    )


def _runtime_error(message: str) -> RuntimeError:
    return RuntimeError(
        f"{message}\ncurrent interpreter: {sys.executable}\n"
        f"required local Vane root: {_local_vane_root()}\ninstall with:\n{_install_hint()}"
    )


def _configure_loopback_network() -> None:
    # Ray workers must reach local ASR/Qwen directly instead of inheriting a
    # developer-machine SOCKS/HTTP proxy.
    for name in PROXY_VARIABLES:
        os.environ.pop(name, None)
    for name in ("NO_PROXY", "no_proxy"):
        os.environ[name] = "localhost,127.0.0.1,::1"
    os.environ.setdefault("VANE_PROGRESS", "0")
    os.environ.setdefault("RAY_DEDUP_LOGS", "0")


def require_local_vane() -> None:
    if tuple(sys.version_info[:2]) != EXPECTED_PYTHON:
        raise _runtime_error(
            f"Python must be {EXPECTED_PYTHON[0]}.{EXPECTED_PYTHON[1]}"
        )
    try:
        import duckdb
        import vane
        from vane.ai.functions import _prompt_relation
    except Exception as exc:
        raise _runtime_error(f"cannot import the local Vane runtime: {exc}") from exc
    distribution = importlib_metadata.distribution("vane-ai")
    direct_text = distribution.read_text("direct_url.json")
    if not direct_text:
        raise _runtime_error("vane-ai has no direct_url.json; local installation is required")
    direct_url = json.loads(direct_text).get("url")
    parsed = urlparse(str(direct_url))
    if parsed.scheme != "file":
        raise _runtime_error(f"vane-ai was not installed from a local file: {direct_url}")
    installed_from = Path(unquote(parsed.path)).resolve()
    try:
        installed_from.relative_to(_local_vane_root())
    except ValueError:
        raise _runtime_error(
            f"vane-ai came from {installed_from}, not {_local_vane_root()}"
        ) from None
    connection = duckdb.connect()
    try:
        engine_version, source_revision, _ = connection.execute(
            "pragma version"
        ).fetchone()
    finally:
        connection.close()
    identities = (
        ("Vane distribution", importlib_metadata.version("vane-ai"), EXPECTED_VANE_VERSION),
        ("Vane API", vane.__version__, EXPECTED_VANE_VERSION),
        ("DuckDB Python", duckdb.__version__, EXPECTED_DUCKDB_VERSION),
        ("DuckDB engine", engine_version, EXPECTED_ENGINE_VERSION),
        ("DuckDB source", source_revision, EXPECTED_SOURCE_REVISION),
    )
    for label, actual, expected in identities:
        if actual != expected:
            raise _runtime_error(
                f"{label} mismatch; expected {expected!r}, got {actual!r}"
            )
    if "image_columns" not in inspect.signature(_prompt_relation).parameters:
        raise _runtime_error("local vane.ai.prompt does not expose image_columns")
    if not callable(getattr(vane.runners, "set_runner_ray", None)):
        raise _runtime_error("local Vane runtime is missing Ray Runner support")


def main(argv: Sequence[str] | None = None) -> int:
    _configure_loopback_network()
    require_local_vane()
    if str(SOURCE_ROOT) not in sys.path:
        sys.path.insert(0, str(SOURCE_ROOT))
    from fund_investment_research.cli import main as cli_main

    return cli_main(list(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
