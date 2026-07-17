# Claims Disposition Operational Runbook

[Back to the use case](../README.md) · [简体中文](runbook.zh-CN.md)

This runbook contains the exact environment, installation, service, configuration, command, and troubleshooting contracts for the claims demo.

## Verified environment

| Item | Verified value |
| --- | --- |
| Operating system | Ubuntu 24.04 x86_64, glibc 2.39 |
| Python | CPython 3.12 |
| Vane | `vane-ai==0.1.0a1` |
| PostgreSQL | `127.0.0.1:5432` |
| MinIO | `127.0.0.1:9000` |
| Model service | Qwen2.5-VL-3B on an NVIDIA CUDA GPU at `127.0.0.1:8001` |

The verified Vane release is published on public PyPI. Its Linux wheel targets
CPython 3.12, x86_64, and `manylinux_2_39`; a matching wheel is not promised for
older glibc, another Python minor version, or another CPU architecture.

Install the project-side Ubuntu tools:

```bash
sudo apt update
sudo apt install -y git curl python3.12 python3.12-venv
```

PostgreSQL, MinIO, and Qwen are external services. The launcher connects to them but does not install, start, stop, or restart them.

## Install from a clean checkout

Run all project commands from the `claims-disposition` directory.

### 1. Create the virtual environment

```bash
cd claims-disposition
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

The environment directory may have another name. The launcher validates the active interpreter and package origin rather than requiring the literal name `.venv`.

### 2. Install Vane from public PyPI

```bash
python -m pip install vane-ai
```

No alternate package index is required. The command installs Vane from public
PyPI; this demo's `pyproject.toml` pins the validated `vane-ai==0.1.0a1`
runtime, and the launcher rejects unvalidated Vane or custom DuckDB builds.

### 3. Install the demo

```bash
python -m pip install -r requirements.txt
python -m pip check
```

`requirements.txt` installs this source tree in editable mode with its test extra. `pyproject.toml` is authoritative:

| Dependency | Purpose |
| --- | --- |
| `vane-ai==0.1.0a1` | Vane APIs, custom DuckDB, and workers |
| `openai==2.45.0` | OpenAI-compatible Qwen client |
| `minio` | Object reads/writes and SHA-256 UDFs |
| `psycopg[binary]` | PostgreSQL input, fixtures, and atomic publication |
| `rapidocr`, `onnxruntime` | CPU OCR: driver-owned on Local, stateful Actor on Ray |
| `numpy`, `pillow` | Fixture images and image-quality calculations |
| `pyarrow` | Relation/Python data boundary |
| `pyyaml` | Strict `runtime.yml` loading |
| `pytz` | Stable fixture timestamps |
| `pytest` | Fast tests |

`pip check` must finish with `No broken requirements found`.

## Prepare the external services

### PostgreSQL and MinIO

The checked-in `runtime.yml` uses this local synthetic-data contract:

| Service | Required contract |
| --- | --- |
| PostgreSQL | database `vane_insight`, user/password `vane_insight` / `vane_insight_dev_password`, `127.0.0.1:5432` |
| MinIO | access/secret `vaneinsight` / `vaneinsight_dev_password`, endpoint `127.0.0.1:9000`, HTTP rather than TLS |

You may reuse existing services or install them using the official PostgreSQL and MinIO documentation. Update `runtime.yml` first if endpoints or credentials differ. The checked-in values are loopback demo credentials and must not be used in production.

The `fixture` command creates the required PostgreSQL schemas/tables and MinIO bucket, then refreshes the synthetic snapshot. It does not create the servers, PostgreSQL database/role, MinIO process, or MinIO access key.

Probe both services with the installed project:

```bash
python - <<'PY'
from claims_disposition_sql_pipeline.config import load_runtime_config
from claims_disposition_sql_pipeline.pipeline import probe_runtime

probe_runtime(load_runtime_config())
print("PostgreSQL and MinIO: OK")
PY
```

### Local Qwen service

Use a separate model-server environment and follow the [complete local Qwen2.5-VL guide](../../docs/local-qwen-service.md). The required service contract is:

```bash
curl -fsS -o /dev/null -w 'health HTTP %{http_code}\n' \
  http://127.0.0.1:8001/health
curl -fsS -H 'Authorization: Bearer dummy' \
  http://127.0.0.1:8001/v1/models | python -m json.tool
```

Expected: health is HTTP 200 and `data[].id` contains `Qwen2.5-VL-3B-Instruct`. The standalone guide also verifies one real image request to `/v1/chat/completions`.

## Run and verify

The launcher exposes four commands:

```bash
python scripts/run_demo.py fixture
python scripts/run_demo.py run
python scripts/run_demo.py verify
python scripts/run_demo.py e2e
```

- `fixture` refreshes four claims, four JPEG photos, and four generated PNG documents.
- `run` probes services, executes real OCR/Qwen inference and the SQL DAG, validates the output, and atomically replaces the PostgreSQL snapshot.
- `verify` reads PostgreSQL and requires the exact four fixture results.
- `e2e` runs `fixture -> run -> verify` and stops on the first nonzero result.

For the first run:

```bash
python scripts/run_demo.py e2e
```

Expected output:

```text
loaded 4 claims and 8 objects
published 4 claim dispositions
verified 4 claim dispositions: CLM-APPROVE=approve_for_payment, CLM-DENY=deny_claim, CLM-MISSING=request_more_materials, CLM-REVIEW=manual_review
```

Run deterministic launcher and installation-contract tests without Qwen or PostgreSQL/MinIO writes:

```bash
python -m pytest tests/fast -q
```

There is no AI mock fallback. An unavailable service, unreadable image, invalid AI JSON, incompatible runtime, SQL failure, or publication failure exits nonzero.

## Runtime configuration

`runtime.yml` is the only runtime configuration source; the application does not discover services from Docker or environment variables.

| Setting | Default |
| --- | --- |
| Runner | `local` |
| PostgreSQL DSN | `postgresql://vane_insight:***@127.0.0.1:5432/vane_insight` |
| Raw relation | `claims_disposition_raw.claims` |
| Output relation | `claims_disposition_output.claim_disposition` |
| MinIO | `127.0.0.1:9000`, bucket `claims-disposition-fixtures` |
| OCR | RapidOCR on CPU; required fields `claim_number`, `claimant_name`, `loss_date`; minimum mean confidence `0.70` |
| AI | OpenAI provider; `http://127.0.0.1:8001/v1`; model `Qwen2.5-VL-3B-Instruct`; concurrency `1`; timeout `120` seconds |

The checked-in configuration uses `runner: local`; `runner: ray` selects the
distributed path. Both modes were verified end to end with public
`vane-ai==0.1.0a1`, real RapidOCR, and the local Qwen service.

On Local, the pipeline creates one `DocumentOcrActor` implementation on the
driver, runs it once for every eligible supporting-document locator, and
attaches the immutable results as
`document_ocr_json(bucket, object_key)`. It also instantiates the configured
model through Vane's public provider API and reuses one async client on the
driver. This keeps the native ONNX sessions and async provider client outside
LocalRunner subprocess boundaries.

On Ray, `DocumentOcrActor` is attached as the stateful
`document_ocr_json(bucket, object_key)` expression and Qwen runs through
`vane.ai.prompt`. The OCR engine initializes lazily inside the isolated Actor
worker. The launcher sets `VANE_UDF_UNREGISTER_TIMEOUT_MS=60000` unless the
operator supplied another value, giving native Ray OCR workers enough time to
shut down cleanly.

In both modes, `int_claim_document_ocr_udf.sql` calls the same expression once
per eligible document, every `*_udf.sql` file remains a direct Runner
projection, and the following pure SQL file parses, joins, classifies, or
aggregates the same materialized contract. Driver-local inputs are staged as
temporary Parquet files and results are registered back in the driver's DuckDB
catalog. Switching Runner changes execution placement, not SQL or output
contracts.

The loader validates YAML shape, SQL identifiers, loopback URLs, required values, and numeric ranges. Diagnostics avoid printing the complete PostgreSQL DSN, MinIO secret, or AI key. For a loopback AI URL, the launcher removes HTTP proxy variables and augments `NO_PROXY`/`no_proxy`.

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

The writer validates all nine columns, types, enums, confidence, and timestamps before opening the publication transaction. It deletes the previous snapshot and inserts the new rows in one PostgreSQL transaction; failures roll back rather than leaving partial data.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `No matching distribution found for vane-ai` | Confirm Ubuntu 24.04 x86_64, CPython 3.12, and glibc 2.39 or newer, then run `python -m pip install vane-ai` against public PyPI |
| Python/Vane/DuckDB version mismatch | Reactivate the project environment and reinstall from public PyPI; the launcher prints the interpreter, prefix, expected/actual values, and install command |
| Ray cannot allocate memory or satisfy query demand | Stop stale Ray processes, free host memory, or connect to a Ray cluster with enough CPU, heap, and object-store capacity; this real OCR flow was validated with 8 CPUs and a 2 GiB object store |
| Vane or DuckDB resolves outside the current environment | Remove inherited `PYTHONPATH`, activate `.venv`, and repeat both installation steps |
| A direct dependency is missing | Run `python -m pip install -r requirements.txt` and `python -m pip check` |
| PostgreSQL connection/authentication fails | Check the endpoint, database, role, password, and permissions for schema/table creation and writes |
| MinIO connection/authentication fails | Check endpoint, credentials, HTTP/TLS mode, and bucket create/list/read/write/delete permissions |
| Qwen health, model, or image request fails | Follow the [Qwen guide](../../docs/local-qwen-service.md) for driver, OOM, port, served-name, model-download, and proxy checks |
| `fixture load failed` | Verify PostgreSQL and MinIO privileges; rerunning `fixture` refreshes only the synthetic snapshot |
| `verification failed` | Inspect the reported missing, extra, duplicate, or mismatched claim, fix the service/runtime issue, and rerun `e2e` |

## Exact runtime identifiers

| Component | Required identifier |
| --- | --- |
| Vane distribution metadata (`vane-ai`) | `0.1.0a1` |
| `vane.__version__` | `0.1.0a1` |
| DuckDB Python package | `0.1.0a1` |
| DuckDB engine | `v1.6.0-dev1` |
| DuckDB source revision | `398033a962` |
| OpenAI Python client | `2.45.0` |

The launcher also requires `vane.func`, `vane.cls`, `vane.attach_function`, `vane.configure`, `vane.ai.load_provider`, `vane.ai.prompt`, and `duckdb.ray_cxx`. Any mismatch fails explicitly instead of silently falling back to ordinary DuckDB.

## Data, credentials, and privacy

- Do not commit real claims, customer photos, private documents, production credentials, model weights, or generated runtime data.
- Values in `runtime.yml` are loopback synthetic-demo credentials only.
- Included vehicle images are governed by adjacent NOTICE files.
- Generated supporting documents contain synthetic fixture data only.
