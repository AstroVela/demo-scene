# Claims Disposition SQL

[中文版](README.zh-CN.md)

This standalone, dbt-free Vane Demo performs first-level vehicle-claim triage. It reads claims from PostgreSQL, fetches JPEG damage photos and PNG documents from MinIO, runs image-quality analysis and stateful RapidOCR, calls a real local Qwen multimodal service through `vane.ai.prompt`, applies deterministic DuckDB SQL rules, and atomically publishes one recommendation per claim back to PostgreSQL.

The included fixture demonstrates all four workflow outcomes:

| Claim | Expected disposition |
| --- | --- |
| `CLM-APPROVE` | `approve_for_payment` |
| `CLM-DENY` | `deny_claim` |
| `CLM-MISSING` | `request_more_materials` |
| `CLM-REVIEW` | `manual_review` |

These are workflow recommendations, not coverage decisions, liability findings, payment calculations, or regulated final denials.

## Verified environment

The release was validated on the following platform. Other platforms may work, but are not currently release-tested:

| Item | Verified value |
| --- | --- |
| Operating system | Ubuntu 24.04 x86_64, glibc 2.39 |
| Python | CPython 3.12 |
| Vane | `vane-ai==0.1.0.dev20260714234347` |
| PostgreSQL | loopback service at `127.0.0.1:5432` |
| MinIO | loopback service at `127.0.0.1:9000` |
| Model service | Qwen2.5-VL-3B on an NVIDIA CUDA GPU at `127.0.0.1:8001` |

The TestPyPI Vane wheel targets CPython 3.12, Linux x86_64, and `manylinux_2_39`. A matching wheel is not promised for older glibc, another Python minor version, or another CPU architecture.

Ubuntu project-side tools:

```bash
sudo apt update
sudo apt install -y git curl python3.12 python3.12-venv
```

PostgreSQL, MinIO, and Qwen are services, not pip packages. The launcher uses them but does not install, start, stop, or restart them.

## Quick start from a clean checkout

Run every project command from this repository directory.

### 1. Create the project environment

```bash
cd claims-disposition
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

The virtual environment directory may have another name. The launcher validates the active interpreter and package origin rather than requiring the literal name `.venv`.

### 2. Install the verified Vane wheel from TestPyPI

```bash
python -m pip install -i https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  vane-ai==0.1.0.dev20260714234347
```

Keep both indexes: Vane comes from TestPyPI while its ordinary dependencies are resolved from PyPI. Keep the exact version: `scripts/run_demo.py` intentionally rejects unvalidated Vane or custom DuckDB builds.

### 3. Install the Demo and all direct dependencies

```bash
python -m pip install -r requirements.txt
python -m pip check
```

`requirements.txt` installs this source tree in editable mode with its test extra. The authoritative direct dependencies are in `pyproject.toml`:

| Dependency | Purpose |
| --- | --- |
| `vane-ai==0.1.0.dev20260714234347` | Vane APIs, custom DuckDB, and local workers |
| `openai==2.45.0` | OpenAI-compatible client used by the Qwen provider |
| `minio` | Material object reads/writes and SHA-256 UDFs |
| `psycopg[binary]` | PostgreSQL input, fixture loading, and atomic publication |
| `rapidocr`, `onnxruntime` | Stateful CPU OCR actor and inference runtime |
| `numpy`, `pillow` | Fixture images and image-quality calculations |
| `pyarrow` | Relation and Python data boundary used by Vane |
| `pyyaml` | Strict `runtime.yml` loading |
| `pytz` | Stable timezone-aware synthetic fixture timestamps |
| `pytest` | Fast launcher and release-installation contract tests |

`pip check` must finish with `No broken requirements found`.

### 4. Prepare PostgreSQL and MinIO

The checked-in `runtime.yml` contains local synthetic-Demo credentials:

| Service | Required local contract |
| --- | --- |
| PostgreSQL | database `vane_insight`, user/password `vane_insight` / `vane_insight_dev_password`, `127.0.0.1:5432` |
| MinIO | access/secret `vaneinsight` / `vaneinsight_dev_password`, S3 endpoint `127.0.0.1:9000`, HTTP rather than TLS |

You may use existing local services or install them with the official PostgreSQL and MinIO instructions. If your endpoints or credentials differ, update `runtime.yml` before running anything. These checked-in values are for loopback synthetic data only; do not reuse them in production.

The `fixture` command creates the required PostgreSQL schemas/tables and MinIO bucket, then loads synthetic data. It does not create a PostgreSQL server, database, database role, MinIO server process, or MinIO access key.

After the services are running, probe both with the installed project code:

```bash
python - <<'PY'
from claims_disposition_sql_pipeline.config import load_runtime_config
from claims_disposition_sql_pipeline.pipeline import probe_runtime

probe_runtime(load_runtime_config())
print("PostgreSQL and MinIO: OK")
PY
```

### 5. Prepare the local Qwen service

The model server belongs in a separate environment. Follow the complete NVIDIA, vLLM, pinned-model download, startup, smoke-test, and troubleshooting guide:

**[Local Qwen2.5-VL service guide](docs/local-qwen-service.md)**

The project requires this contract:

```bash
curl -fsS -o /dev/null -w 'health HTTP %{http_code}\n' \
  http://127.0.0.1:8001/health
curl -fsS -H 'Authorization: Bearer dummy' \
  http://127.0.0.1:8001/v1/models | python -m json.tool
```

Expected: health is HTTP 200 and `data[].id` contains `Qwen2.5-VL-3B-Instruct`. The separate guide also verifies a real image request to `/v1/chat/completions`.

### 6. Run the real end-to-end Demo

```bash
python scripts/run_demo.py e2e
```

`e2e` runs `fixture -> run -> verify` and stops on the first nonzero result. A successful run prints:

```text
loaded 4 claims and 8 objects
published 4 claim dispositions
verified 4 claim dispositions: CLM-APPROVE=approve_for_payment, CLM-DENY=deny_claim, CLM-MISSING=request_more_materials, CLM-REVIEW=manual_review
```

There is no AI mock fallback. An unavailable service, unreadable image, invalid AI JSON, incompatible runtime, SQL failure, or publication failure exits nonzero.

### 7. Run fast deterministic tests

These tests validate the launcher and public installation contract without calling Qwen or changing PostgreSQL/MinIO data:

```bash
python -m pytest tests/fast -q
```

## Commands

The launcher exposes exactly four commands, all through the current environment:

```bash
python scripts/run_demo.py fixture
python scripts/run_demo.py run
python scripts/run_demo.py verify
python scripts/run_demo.py e2e
```

- `fixture` refreshes the local synthetic snapshot with four claims, four JPEG photos, and four generated PNG documents.
- `run` probes PostgreSQL and MinIO, executes real OCR and Qwen inference, runs the complete SQL DAG, validates the nine-column output, and atomically replaces the PostgreSQL output snapshot.
- `verify` reads PostgreSQL directly and requires the exact four fixture results.
- `e2e` runs all three steps in order and is the recommended first-run and release-validation entry.

## Runtime configuration

`runtime.yml` is the sole runtime configuration source. The application does not discover services from Docker or environment variables.

| Setting | Default |
| --- | --- |
| PostgreSQL DSN | `postgresql://vane_insight:***@127.0.0.1:5432/vane_insight` |
| Raw relation | `claims_disposition_raw.claims` |
| Output relation | `claims_disposition_output.claim_disposition` |
| MinIO | `127.0.0.1:9000`, bucket `claims-disposition-fixtures` |
| OCR | RapidOCR on CPU, required fields `claim_number`, `claimant_name`, `loss_date`, minimum mean confidence `0.70` |
| AI | OpenAI provider, `http://127.0.0.1:8001/v1`, model `Qwen2.5-VL-3B-Instruct`, concurrency `1`, timeout `120` seconds |

The loader validates YAML shape, SQL identifiers, loopback URLs, required values, and numeric ranges. Diagnostics identify unavailable services without printing the complete PostgreSQL DSN, MinIO secret, or AI key. For a loopback AI URL the launcher removes HTTP proxy variables and augments `NO_PROXY`/`no_proxy`.

## How Vane is used

The Demo combines three Vane capability types in one DuckDB pipeline:

- stateless UDFs declared with `@vane.func` check MinIO objects and hashes, measure image quality, parse OCR fields, and validate AI JSON;
- a stateful UDF declared with `@vane.cls(actor_number=1, gpus=0)` initializes RapidOCR once and reuses the actor;
- the AI Function `vane.ai.prompt` sends trusted image bytes and a structured prompt to Qwen, then downstream SQL applies the business decision rules.

The complete data flow is shown below. You can also open the English [PNG](docs/vane-claims-data-flow.en.png) or edit the [Excalidraw source](docs/vane-claims-data-flow.en.excalidraw).

![Vane multimodal claims data flow](docs/vane-claims-data-flow.en.png)

```text
runtime.yml
  + PostgreSQL claims
  + MinIO JPEG photos and PNG documents
        -> staging relations
        -> object / quality / OCR facts
        -> trusted photo requests
        -> Qwen multimodal AI Function
        -> damage and uncertainty facts
        -> deterministic decision SQL
        -> nine-column contract validation
        -> atomic PostgreSQL publication
```

The fixed relation order is:

```text
stg_claims
  -> stg_claim_materials
  -> stg_run_config
  -> int_claim_material_facts
  -> int_claim_photo_ai
  -> int_claim_damage_facts
  -> int_claim_decision_facts
  -> claim_disposition
```

`int_claim_photo_ai` is the only Python-created intermediate table. All other transformations are ordinary DuckDB `.sql` files with no dbt, Jinja, macro, or `ref()` dependency.

## Data contracts

The raw PostgreSQL grain is one row per claim:

```sql
create schema if not exists claims_disposition_raw;

create table if not exists claims_disposition_raw.claims (
  claim_id text primary key,
  scenario text not null,
  description text not null,
  submitted_at timestamptz not null,
  is_test_claim boolean not null,
  materials_json jsonb not null
);
```

`materials_json` is an ordered array of MinIO locators. Supported pairs are `damage_photo + image/jpeg` and `supporting_document + image/png`.

The output grain is one row per claim:

```sql
create schema if not exists claims_disposition_output;

create table if not exists claims_disposition_output.claim_disposition (
  claim_id text primary key,
  disposition text not null,
  disposition_confidence numeric(4, 2) not null,
  primary_reason_code text not null,
  reason_summary text not null,
  next_action text not null,
  supporting_facts_json jsonb not null,
  created_by text not null,
  decided_at timestamptz not null
);
```

Publication deletes the previous snapshot and inserts the new rows in one PostgreSQL transaction. Validation happens before opening that transaction, and a failed write rolls back rather than leaving a partial snapshot.

## Installation and runtime troubleshooting

| Symptom | Action |
| --- | --- |
| `No matching distribution found for vane-ai` | Confirm Ubuntu 24.04 x86_64, CPython 3.12, glibc 2.39 or newer, and the complete TestPyPI plus extra-index command |
| Python/Vane/DuckDB version mismatch | Reactivate the project environment and reinstall the exact wheel; the launcher prints the interpreter, prefix, expected/actual values, and install command |
| Vane or DuckDB is outside the current environment | Remove an inherited `PYTHONPATH`, activate `.venv`, and rerun the two installation steps |
| `ModuleNotFoundError` for `openai`, `minio`, `psycopg`, or another direct dependency | Run `python -m pip install -r requirements.txt` and `python -m pip check` |
| PostgreSQL connection or authentication fails | Check `127.0.0.1:5432`, the database/role/password, and `runtime.yml`; the account needs schema/table creation and write access |
| MinIO connection or authentication fails | Check `127.0.0.1:9000`, access/secret, and HTTP/TLS mode; the account needs bucket creation, list, read, write, and delete access |
| Qwen health, model, or image request fails | Follow the [Qwen service guide](docs/local-qwen-service.md) for driver, OOM, port, served-name, model download, and proxy checks |
| `fixture load failed` | Verify PostgreSQL privileges and MinIO bucket/object privileges; rerunning `fixture` safely refreshes only the four synthetic claims |
| `verification failed` | Inspect the missing, extra, duplicate, or mismatched claim reported by the command, then rerun `e2e` after correcting the service or runtime issue |

## Exact runtime identifiers

The launcher validates more than the public API shape:

| Component | Required identifier |
| --- | --- |
| Vane distribution metadata (`vane-ai`) | `0.1.0.dev20260714234347` |
| `vane.__version__` | `0.1.0.dev20260714234347` |
| DuckDB Python package | `0.1.0.dev20260714234347` |
| DuckDB engine | `v1.6.0-dev121` |
| DuckDB source revision | `ca6948529b` |
| OpenAI Python client | `2.45.0` |

It also requires `vane.func`, `vane.cls`, `vane.attach_function`, `vane.configure`, `vane.ai.prompt`, and `duckdb.ray_cxx`. Any mismatch fails explicitly rather than silently falling back to ordinary DuckDB.

## Data, credentials, and privacy

- Do not commit real claims, customer photos, private documents, production credentials, model weights, or generated runtime data.
- Values in `runtime.yml` are loopback synthetic-Demo credentials only.
- Included vehicle images are governed by their adjacent NOTICE files.
- Generated supporting documents contain synthetic fixture data only.
