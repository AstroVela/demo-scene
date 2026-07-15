# Reproducible Vane Package Installation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the claims disposition Demo installable and runnable from a user-owned Python 3.12 virtual environment with the pinned TestPyPI Vane wheel and no developer-machine runtime dependency.

**Architecture:** Use one editable project environment for Vane, DuckDB, clients, OCR, and tests; keep PostgreSQL, MinIO, and the local Qwen/vLLM server as explicit external service contracts. Replace the legacy launcher with a current-environment validator, make packaging metadata authoritative, and enforce the public installation story with fast contract tests before proving it in a brand-new environment.

**Tech Stack:** CPython 3.12, `venv`, pip, `vane-ai==0.1.0.dev20260714234347`, custom DuckDB, OpenAI Python 2.45.0, PostgreSQL/psycopg, MinIO, RapidOCR/ONNX Runtime, PyArrow, pytest, Markdown.

---

### Task 1: Add failing installation and launcher contract tests

**Files:**
- Create: `tests/fast/test_launcher.py`
- Create: `tests/fast/test_release_shape.py`

- [ ] **Step 1: Write the launcher contract test**

Create tests that load `scripts/run_demo.py` dynamically and require the wished-for public API:

```python
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_PATH = PROJECT_ROOT / "scripts/run_demo.py"


def _load_launcher():
    assert LAUNCHER_PATH.is_file(), "public launcher must be scripts/run_demo.py"
    spec = importlib.util.spec_from_file_location("claims_run_demo", LAUNCHER_PATH)
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
    assert launcher.EXPECTED_PYTHON_VERSION == (3, 12)
    assert launcher.EXPECTED_VANE_DISTRIBUTION_VERSION == "0.1.0.dev20260714234347"
    assert launcher.EXPECTED_VANE_API_VERSION == "0.1.0.dev20260714234347"
    assert launcher.EXPECTED_DUCKDB_PYTHON_VERSION == "0.1.0.dev20260714234347"
    assert launcher.EXPECTED_DUCKDB_ENGINE_VERSION == "v1.6.0-dev121"
    assert launcher.EXPECTED_DUCKDB_SOURCE_REVISION == "ca6948529b"


def test_launcher_rejects_wrong_python_version():
    launcher = _load_launcher()
    with pytest.raises(RuntimeError, match="Python version mismatch"):
        launcher.validate_python_version((3, 11))


def test_launcher_rejects_missing_callable_api():
    launcher = _load_launcher()
    incomplete_vane = SimpleNamespace()
    with pytest.raises(RuntimeError, match="callable API"):
        launcher.validate_runtime_api(incomplete_vane, SimpleNamespace(ray_cxx=True))


def test_launcher_rejects_missing_ray_bridge():
    launcher = _load_launcher()
    complete_vane = SimpleNamespace(
        func=lambda: None,
        cls=lambda: None,
        attach_function=lambda: None,
        configure=lambda: None,
        ai=SimpleNamespace(prompt=lambda: None),
    )
    with pytest.raises(RuntimeError, match="ray_cxx"):
        launcher.validate_runtime_api(complete_vane, SimpleNamespace())


def test_launcher_public_entry_is_the_only_script():
    assert sorted(path.name for path in (PROJECT_ROOT / "scripts").glob("*.py")) == [
        "run_demo.py"
    ]
```

Include a five-row `pytest.mark.parametrize` table for Vane distribution, Vane API, DuckDB Python, DuckDB engine, and DuckDB source mismatch rejection, using the exact expected values from the design.

- [ ] **Step 2: Write the release installation contract test**

Create a test that parses `pyproject.toml`, reads both READMEs and both Qwen guides, and asserts the exact public contract:

```python
from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PINNED_VANE = "vane-ai==0.1.0.dev20260714234347"


def test_packaging_declares_every_direct_runtime_dependency():
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())["project"]
    dependencies = set(project["dependencies"])
    assert PINNED_VANE in dependencies
    assert "openai==2.45.0" in dependencies
    assert "test" in project["optional-dependencies"]
    assert (PROJECT_ROOT / "requirements.txt").read_text().splitlines() == [
        "# Install the pinned TestPyPI Vane wheel first; see README.md.",
        "-e .[test]",
    ]


def test_readmes_document_a_fresh_environment_and_all_services():
    for name in ("README.md", "README.zh-CN.md"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        for required in (
            "python3.12 -m venv .venv",
            "https://test.pypi.org/simple/",
            "--extra-index-url https://pypi.org/simple/",
            PINNED_VANE,
            "python -m pip install -r requirements.txt",
            "python scripts/run_demo.py e2e",
            "127.0.0.1:5432",
            "127.0.0.1:9000",
            "127.0.0.1:8001",
        ):
            assert required in text


def test_qwen_guides_pin_and_smoke_test_the_model_service():
    for name in ("docs/local-qwen-service.md", "docs/local-qwen-service.zh.md"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert "vllm==0.25.1" in text
        assert "66285546d2b821cf421d4f5eb2576359d3770cd3" in text
        assert "/health" in text
        assert "/v1/models" in text
        assert "/v1/chat/completions" in text
```

Add a focused scan across READMEs, local service guides, Python scripts, TOML, YAML, and requirements to reject the assembled legacy developer prefix string.

- [ ] **Step 3: Run the tests and verify RED**

Run:

```bash
/tmp/procurement-vane-ai-0.1.0.dev20260714234347/bin/python -m pytest tests/fast -q
```

Expected: FAIL because `scripts/run_demo.py` and Qwen guides do not exist, requirements and project metadata lack the new contract, and both READMEs still describe the legacy runtime.

- [ ] **Step 4: Commit the failing contract tests**

```bash
git add tests/fast/test_launcher.py tests/fast/test_release_shape.py
git commit -m "test: define reproducible Vane install contract"
```

### Task 2: Replace the launcher with a current-environment validator

**Files:**
- Delete: `scripts/run_with_vane.py`
- Create: `scripts/run_demo.py`
- Test: `tests/fast/test_launcher.py`

- [ ] **Step 1: Rename the launcher**

Run:

```bash
git mv scripts/run_with_vane.py scripts/run_demo.py
```

- [ ] **Step 2: Replace legacy path wiring with exact current-environment checks**

Implement these constants and diagnostics:

```python
EXPECTED_PYTHON_VERSION = (3, 12)
VANE_DISTRIBUTION_NAME = "vane-ai"
EXPECTED_VANE_DISTRIBUTION_VERSION = "0.1.0.dev20260714234347"
EXPECTED_VANE_API_VERSION = "0.1.0.dev20260714234347"
EXPECTED_DUCKDB_PYTHON_VERSION = "0.1.0.dev20260714234347"
EXPECTED_DUCKDB_ENGINE_VERSION = "v1.6.0-dev121"
EXPECTED_DUCKDB_SOURCE_REVISION = "ca6948529b"
INSTALL_HINT = (
    "python -m pip install -i https://test.pypi.org/simple/ "
    "--extra-index-url https://pypi.org/simple/ "
    f"vane-ai=={EXPECTED_VANE_DISTRIBUTION_VERSION}"
)
```

Add `validate_python_version`, `validate_runtime_versions`, `validate_runtime_api`, `_require_package_from_current_environment`, and `require_real_vane_runtime`. `require_real_vane_runtime` must open a DuckDB connection, execute `pragma version`, validate all five release identifiers, require the public Vane API and `ray_cxx`, and require both imported package files to be below the current `sys.prefix`.

Keep `configure_loopback_network` and `main`, but delete every fixed prefix, `site.addsitedir`, supplemental environment, and manual worker `PYTHONPATH` branch. Dispatch unchanged to:

```python
from claims_disposition_sql_pipeline.cli import main as cli_main
return cli_main(list(sys.argv[1:] if argv is None else argv))
```

- [ ] **Step 3: Run the launcher tests**

Run:

```bash
/tmp/procurement-vane-ai-0.1.0.dev20260714234347/bin/python -m pytest tests/fast/test_launcher.py -q
```

Expected: launcher-specific tests PASS; release-shape tests remain red until metadata and docs are updated.

### Task 3: Make packaging metadata authoritative

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Test: `tests/fast/test_release_shape.py`

- [ ] **Step 1: Add direct runtime and test dependencies**

Add these exact project dependencies while preserving the existing bounded direct dependencies:

```toml
"openai==2.45.0",
"vane-ai==0.1.0.dev20260714234347",
```

Add:

```toml
[project.optional-dependencies]
test = ["pytest>=8,<9"]

[tool.pytest.ini_options]
pythonpath = ["src"]
```

- [ ] **Step 2: Replace requirements with the standard editable entry**

Write exactly:

```text
# Install the pinned TestPyPI Vane wheel first; see README.md.
-e .[test]
```

- [ ] **Step 3: Run the packaging contract test**

Run:

```bash
/tmp/procurement-vane-ai-0.1.0.dev20260714234347/bin/python -m pytest tests/fast/test_release_shape.py::test_packaging_declares_every_direct_runtime_dependency -q
```

Expected: PASS.

### Task 4: Write the independent Qwen service guides

**Files:**
- Create: `docs/local-qwen-service.md`
- Create: `docs/local-qwen-service.zh.md`
- Test: `tests/fast/test_release_shape.py`

- [ ] **Step 1: Write both service guides from the verified contract**

Both guides must include:

```bash
python3.12 -m venv "$HOME/.venvs/claims-qwen"
source "$HOME/.venvs/claims-qwen/bin/activate"
python -m pip install --upgrade pip
python -m pip install uv "huggingface_hub>=0.34,<2"
uv pip install --python "$VIRTUAL_ENV/bin/python" \
  --torch-backend=auto \
  "vllm==0.25.1"

hf download Qwen/Qwen2.5-VL-3B-Instruct \
  --revision 66285546d2b821cf421d4f5eb2576359d3770cd3 \
  --local-dir "$HOME/models/Qwen2.5-VL-3B-Instruct"

CUDA_VISIBLE_DEVICES=0 vllm serve "$HOME/models/Qwen2.5-VL-3B-Instruct" \
  --served-model-name Qwen2.5-VL-3B-Instruct \
  --host 127.0.0.1 \
  --port 8001 \
  --api-key dummy \
  --dtype bfloat16 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.85 \
  --limit-mm-per-prompt '{"image": 1}' \
  --generation-config vllm
```

Document NVIDIA/GPU, Python, disk, and port preflight; `/health`, `/v1/models`, and a one-image `/v1/chat/completions` request; stop/restart; and driver/backend, OOM, model-name, port, download, and proxy troubleshooting. State that this service is loopback-only and separate from the project `.venv`.

- [ ] **Step 2: Run the Qwen guide contract test**

Run:

```bash
/tmp/procurement-vane-ai-0.1.0.dev20260714234347/bin/python -m pytest tests/fast/test_release_shape.py::test_qwen_guides_pin_and_smoke_test_the_model_service -q
```

Expected: PASS.

### Task 5: Rewrite both README installation paths

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Test: `tests/fast/test_release_shape.py`

- [ ] **Step 1: Replace all legacy install and command examples**

Both READMEs must present this order:

```text
verified platform
-> create and activate .venv
-> install pinned TestPyPI Vane
-> install requirements and run pip check
-> prepare/probe PostgreSQL, MinIO, and Qwen
-> run python scripts/run_demo.py e2e
-> optionally run fixture/run/verify separately
```

Include exact commands from the design, explain each direct dependency group, link both language variants and the matching Qwen guide, and document the four expected outputs. Keep the existing input/output contracts, SQL DAG, Vane feature explanation, business-safety boundary, fixture outcomes, and privacy section.

For PostgreSQL and MinIO, document the exact default endpoints and credentials from `runtime.yml`, state that they are local synthetic-Demo credentials, explain that `fixture` creates schemas/tables/bucket but not server processes, and give non-secret preflight commands or Python checks. Do not claim the launcher starts any service.

- [ ] **Step 2: Add install/runtime troubleshooting and exact release identifiers**

Cover at least:

- no matching `vane-ai` distribution;
- wrong Python/Vane/DuckDB version or package origin;
- missing OpenAI client or other package dependency;
- PostgreSQL authentication/connectivity;
- MinIO connectivity/credentials;
- Qwen health, served model, OOM, and proxy failures;
- unexpected fixture result.

- [ ] **Step 3: Run all release-shape tests**

Run:

```bash
/tmp/procurement-vane-ai-0.1.0.dev20260714234347/bin/python -m pytest tests/fast/test_release_shape.py -q
```

Expected: PASS, including the absence of the legacy developer runtime reference.

- [ ] **Step 4: Commit launcher, packaging, and documentation**

```bash
git add README.md README.zh-CN.md docs/local-qwen-service.md docs/local-qwen-service.zh.md pyproject.toml requirements.txt scripts/run_demo.py scripts/run_with_vane.py
git commit -m "docs: make Vane package installation reproducible"
```

### Task 6: Run the complete fast verification suite

**Files:**
- Modify only if a test exposes a contract defect.

- [ ] **Step 1: Run all fast tests in the verified package environment**

Run:

```bash
/tmp/procurement-vane-ai-0.1.0.dev20260714234347/bin/python -m pytest tests/fast -q
```

Expected: all tests PASS with no warnings caused by project code.

- [ ] **Step 2: Verify formatting, imports, and repository references**

Run:

```bash
git diff --check
rg -n '/home/[^ ]+/vane/\.venv-system|run_with_vane|supplemental dependencies' \
  README.md README.zh-CN.md docs/local-qwen-service.md \
  docs/local-qwen-service.zh.md scripts pyproject.toml requirements.txt
```

Expected: `git diff --check` exits 0 and the release-file reference scan returns no matches. Historical design/plan records are deliberately outside this scan.

### Task 7: Prove installation and E2E from a brand-new environment

**Files:**
- No repository modifications expected.
- Create outside repository: `/tmp/claims-disposition-vane-ai-0.1.0.dev20260714234347-fresh`

- [ ] **Step 1: Create the fresh Python 3.12 environment**

Run:

```bash
python3.12 -m venv /tmp/claims-disposition-vane-ai-0.1.0.dev20260714234347-fresh
/tmp/claims-disposition-vane-ai-0.1.0.dev20260714234347-fresh/bin/python -m pip install --upgrade pip
```

- [ ] **Step 2: Install the exact TestPyPI package and project requirements**

Run:

```bash
/tmp/claims-disposition-vane-ai-0.1.0.dev20260714234347-fresh/bin/python -m pip install \
  -i https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  vane-ai==0.1.0.dev20260714234347
/tmp/claims-disposition-vane-ai-0.1.0.dev20260714234347-fresh/bin/python -m pip install -r requirements.txt
/tmp/claims-disposition-vane-ai-0.1.0.dev20260714234347-fresh/bin/python -m pip check
```

Expected: exact Vane wheel installed, current project installed editable, and `No broken requirements found`.

- [ ] **Step 3: Prove package provenance and runtime identifiers**

Use the fresh interpreter to print `sys.executable`, `sys.prefix`, `vane.__file__`, `duckdb.__file__`, `openai.__version__`, the five pinned runtime identifiers, `duckdb.ray_cxx`, and whether the legacy prefix occurs in `sys.path`.

Expected: all package paths are inside the fresh prefix, identifiers match the design, `ray_cxx` is true, and the legacy prefix check is false.

- [ ] **Step 4: Run fast tests from the fresh environment**

Run:

```bash
/tmp/claims-disposition-vane-ai-0.1.0.dev20260714234347-fresh/bin/python -m pytest tests/fast -q
```

Expected: all tests PASS.

- [ ] **Step 5: Probe existing services without starting or restarting them**

Use project config and the fresh interpreter to call `probe_runtime` for PostgreSQL/MinIO and `probe_qwen` for the model health endpoint. Clear loopback proxy variables exactly as the launcher does.

Expected: PostgreSQL, MinIO, and Qwen all report OK.

- [ ] **Step 6: Run the real E2E and a separate verify**

Run:

```bash
/tmp/claims-disposition-vane-ai-0.1.0.dev20260714234347-fresh/bin/python scripts/run_demo.py e2e
/tmp/claims-disposition-vane-ai-0.1.0.dev20260714234347-fresh/bin/python scripts/run_demo.py verify
```

Expected:

```text
loaded 4 claims and 8 objects
published 4 claim dispositions
verified 4 claim dispositions: CLM-APPROVE=approve_for_payment, CLM-DENY=deny_claim, CLM-MISSING=request_more_materials, CLM-REVIEW=manual_review
```

- [ ] **Step 7: Run final repository checks**

Run:

```bash
git status --short
git log -3 --oneline
```

Expected: only intentional plan/implementation commits, no virtual environment or runtime output files, and no uncommitted changes.
