# Procurement Audit Operational Runbook

[Back to the use case](../README.md) · [简体中文](runbook.zh-CN.md)

This runbook contains the exact environment, installation, model-service, configuration, execution, and troubleshooting contracts for the procurement audit demo.

## Verified environment

| Item | Verified value |
| --- | --- |
| Operating system | Ubuntu 24.04 x86_64, glibc 2.39 |
| Python | CPython 3.12 |
| Vane | `vane-ai==0.1.0a1` |
| PostgreSQL | `127.0.0.1:5432`, database `vane_insight` |
| MinIO | `127.0.0.1:9000`, HTTP |
| Model service | `Qwen2.5-VL-3B-Instruct` on a local NVIDIA GPU |

The verified Vane release is published on public PyPI. Its Linux wheel targets CPython 3.12, x86_64, and `manylinux_2_39`; support for older glibc versions, other Python minor versions, or other CPU architectures is not guaranteed.

Install the project-side Ubuntu tools:

```bash
sudo apt update
sudo apt install -y git curl python3.12 python3.12-venv
```

## Install from a clean checkout

Run all project commands from the `procurement-compliance-audit` directory.

### 1. Create the virtual environment

```bash
cd procurement-compliance-audit
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### 2. Install Vane from public PyPI

```bash
python -m pip install vane-ai
```

No alternate package index is required. The command installs Vane from public PyPI; this demo's `pyproject.toml` pins the validated `vane-ai==0.1.0a1` runtime, and the launcher rejects other runtime identifiers.

### 3. Install the demo

```bash
python -m pip install -r requirements.txt
python -m pip check
```

This installs the source in editable mode; `pyproject.toml` is authoritative:

| Dependency | Purpose |
| --- | --- |
| `vane-ai==0.1.0a1` | Vane APIs, custom DuckDB, and workers |
| `openai==2.45.0` | OpenAI-compatible Qwen client |
| `psycopg` | Read and initialize PostgreSQL raw tables |
| `minio` | Read and initialize raw material objects in MinIO |
| `rapidocr`, `onnxruntime` | CPU OCR: driver-owned on Local, stateful Actor on Ray |
| `pillow` | Image reads |
| `pyarrow` | Relation/Python data boundary |
| `pyyaml` | Strict `runtime.yml` loading |
| `pytest` | Fast tests |

`pip check` must report no broken requirements.

## Prepare PostgreSQL and MinIO

The checked-in `runtime.yml` uses these loopback contracts by default:

| Service | Default contract |
| --- | --- |
| PostgreSQL | `postgresql://vane_insight:***@127.0.0.1:5432/vane_insight` |
| MinIO | access/secret `vaneinsight` / `vaneinsight_dev_password`, `127.0.0.1:9000`, HTTP |

You may reuse existing services or install them from their official documentation. The `fixture` command creates the four raw tables and MinIO bucket and refreshes synthetic data; it does not create the servers, PostgreSQL database/role, MinIO process, or access key.

After configuring the services, initialize the sources independently with:

```bash
python scripts/run_demo.py fixture
```

The runtime pipeline never reads `fixtures/`; that directory is only a reproducible synthetic seed for the `fixture` command.

## Prepare the local Qwen service

The model server must use a separate environment. Follow the shared [Qwen2.5-VL setup guide](../../docs/local-qwen-service.md) for NVIDIA, vLLM, model download, startup, and troubleshooting.

Verify the service contract:

```bash
curl -fsS -o /dev/null -w 'health HTTP %{http_code}\n' \
  http://127.0.0.1:8001/health
curl -fsS -H 'Authorization: Bearer dummy' \
  http://127.0.0.1:8001/v1/models | python -m json.tool
```

Expected: health is HTTP 200 and the model list contains `Qwen2.5-VL-3B-Instruct`. The standalone guide also verifies a real image request to `/v1/chat/completions`.

## Run and verify

The launcher exposes three commands:

```bash
python scripts/run_demo.py fixture
python scripts/run_demo.py run
python scripts/run_demo.py e2e
```

- `fixture` writes one project, three suppliers, twelve scores, and two evidence locators to PostgreSQL, plus two PNG objects to MinIO.
- `run` probes PostgreSQL/MinIO, reads every input from those services, and executes real OCR, Qwen, and the SQL DAG.
- `e2e` runs `fixture -> run` in order.

For a first run:

```bash
python scripts/run_demo.py e2e
```

Expected terminal output:

```text
Result: review_required | 3 findings | flagged expert EXP-001
Winner recalculation: SUP-JW-001 -> SUP-ZJ-002
```

The command writes only:

```text
output/audit_findings.jsonl  # 3 rows
output/audit_summary.jsonl   # 1 row
```

There is no AI mock fallback. The command fails explicitly if Qwen is unavailable, an image is unreadable, AI JSON violates its contract, or the Vane runtime is incompatible.

Run deterministic tests without starting Qwen:

```bash
python -m pytest tests/fast -q
```

## Synthetic seed and source contract

`fixtures/expert-score-anomaly/` contains exactly four synthetic seed files, read only by the `fixture` command:

| File | Grain | Purpose |
| --- | --- | --- |
| `project.json` | one procurement project | Suppliers, MinIO object keys, original winner, and rule thresholds |
| `expert_scores.csv` | expert × supplier, 12 rows | Scores from four experts for three suppliers |
| `expert_recommendation.png` | one image | `EXP-001` recommends Jingwei before the tender |
| `committee_minutes.png` | one image | `EXP-001` participates and is marked as not recused |

All names, companies, and documents are synthetic. The score matrix guarantees that:

- `SUP-JW-001` has the highest average with all experts;
- `EXP-001` gives Jingwei 98 while other experts average 80, an 18-point deviation;
- `SUP-ZJ-002` ranks first after removing `EXP-001`.

At runtime, the authoritative sources are the PostgreSQL `projects`, `suppliers`, `expert_scores`, and `evidence_files` tables plus the MinIO bucket `procurement-compliance-audit-fixtures`. `evidence_files.bucket/object_key` is the trusted PostgreSQL-to-MinIO locator. The pipeline, OCR implementation, and AI request builder never read local paths.

## Relation contracts

| Relation | Materialization | Grain | Purpose |
| --- | --- | --- | --- |
| `stg_scores` | view | expert × supplier | Type, score, and supplier contracts |
| `stg_evidence_images` | view | evidence image | Trusted locator and role |
| `int_evidence_ocr` | table | evidence image | Typed output from the SQL-called stateful OCR expression |
| `int_evidence_ai` | table | evidence image | Raw Qwen JSON response |
| `int_conflict_facts` | view | evidence image | Validated recommendation, participation, and recusal facts |
| `int_score_metrics` | view | project × conflict signal | Peer average, score delta, and both rankings |
| `audit_findings` | table | finding | Three deterministic rules |
| `audit_summary` | table | project | Project-level audit status |

`int_evidence_ai` is the only core intermediate relation assembled by Python business logic. The transient `*_udf` relations are defined in SQL and materialized by the active Runner. Each image request is bound to `project_id/file_id`, independently of actor execution order. The returned `document_type` must match the fixture's trusted role; a mismatch triggers one contract-reinforced retry with the same image, and SQL applies the role binding again.

Use `queries.sql` to inspect all eight relations in the same connection:

```sql
select * from int_score_metrics;
select * from audit_findings order by rule_id;
select * from audit_summary;
```

## AI response and decision contracts

Qwen returns only document type, expert ID, supplier, recommendation, participation, recusal, source evidence text, and confidence. It does not decide whether a violation occurred.

The local service may wrap JSON in one complete code fence. The validator normalizes only that outer fence and rejects surrounding prose, missing or unknown fields, invalid types, and placeholder evidence. The response is prevalidated at the AI boundary and independently validated again by the attached stateless UDF in SQL.

Both images must pass OCR and reach Qwen. Missing OCR coverage fails the run without publishing output. When both calls complete but either AI confidence is below `0.75`, SQL emits no findings and marks the summary `insufficient_evidence`.

The deterministic findings are:

1. `EXP-001-conflict-not-recused`
2. `EXP-002-score-bias`
3. `EXP-003-award-impact`

## Output contract

With the normal fixture, `audit_findings.jsonl` contains exactly three rows and `audit_summary.jsonl` exactly one. With insufficient evidence, findings contains zero rows and summary contains one row with status `insufficient_evidence`.

Before writing, the pipeline validates fields, primary keys, enums, counts, and evidence references. Each file is atomically replaced through a temporary file in the same directory.

## Runtime configuration

The checked-in `runtime.yml` defines:

| Setting | Default |
| --- | --- |
| Runner | `local` |
| PostgreSQL raw tables | `procurement_audit_raw.projects`, `suppliers`, `expert_scores`, `evidence_files` |
| MinIO | `127.0.0.1:9000`, bucket `procurement-compliance-audit-fixtures` |
| Output directory | `output` |
| OCR | RapidOCR on CPU, minimum confidence `0.60` |
| AI | OpenAI provider at `http://127.0.0.1:8001/v1`; model `Qwen2.5-VL-3B-Instruct`; concurrency `1`; timeout `120` seconds |

The checked-in configuration uses:

```yaml
runner: local
```

The checked-in value is `runner: local`; change it to `runner: ray` for the distributed path. Both modes were verified end to end with public `vane-ai==0.1.0a1`, real RapidOCR, and the local Qwen service.

On Local, the pipeline creates one `EvidenceOcrActor` implementation on the driver, processes every trusted evidence locator once, and attaches the immutable results as `evidence_ocr_json(bucket, object_key)`. It also instantiates the configured model through Vane's public provider API and reuses one async client on the driver. This keeps native ONNX sessions and the async provider client outside LocalRunner subprocess boundaries.

On Ray, `EvidenceOcrActor` is attached as the stateful `evidence_ocr_json(bucket, object_key)` expression and Qwen runs through `vane.ai.prompt`. The OCR engine initializes lazily inside its isolated Actor worker. The launcher sets `VANE_UDF_UNREGISTER_TIMEOUT_MS=60000` unless the operator supplied another value, giving native Ray OCR workers enough time to shut down cleanly.

In both modes, `int_evidence_ocr_udf.sql` calls the same expression once per image and `int_evidence_ocr.sql` parses the same materialized JSON. Response validation keeps the `int_conflict_validation_udf.sql` then `int_conflict_facts.sql` shape. Driver-local inputs are staged as temporary Parquet files and Runner results are registered in the driver's DuckDB catalog for the next pure SQL node. Switching Runner changes execution placement, not SQL or output contracts. A real multi-node target cluster still requires its own infrastructure smoke test.

## Troubleshooting

| Symptom | Resolution |
| --- | --- |
| `No matching distribution found for vane-ai` | Confirm Ubuntu 24.04 x86_64, Python 3.12, and glibc 2.39 or newer, then run `python -m pip install vane-ai` against public PyPI |
| Python/Vane/DuckDB version mismatch | Reactivate `.venv` and reinstall from public PyPI; the launcher reports the current interpreter, prefix, and expected/actual values |
| Ray cannot allocate memory or satisfy query demand | Stop stale Ray processes, free host memory, or connect to a Ray cluster with enough CPU, heap, and object-store capacity; this real OCR flow was validated with 8 CPUs and a 2 GiB object store |
| A direct dependency is missing | Run `python -m pip install -r requirements.txt` and `python -m pip check` |
| PostgreSQL connection, authentication, or table initialization failure | Check the DSN, database/role, port, and schema/table read/write permissions |
| MinIO connection, authentication, or object-read failure | Check endpoint, HTTP/TLS, access key, and bucket list/read/write/delete permissions |
| Qwen health or image request fails | Use the [Qwen guide](../../docs/local-qwen-service.md) to check port, driver, OOM, model name, and proxy settings |
| Output does not contain three findings | Inspect the terminal error and Qwen response; the default fixture's OCR or AI confidence did not meet its threshold |

## Exact runtime identifiers

| Component | Required identifier |
| --- | --- |
| Vane distribution metadata (`vane-ai`) | `0.1.0a1` |
| `vane.__version__` | `0.1.0a1` |
| DuckDB Python package | `0.1.0a1` |
| DuckDB engine | `v1.6.0-dev1` |
| DuckDB source revision | `398033a962` |
| OpenAI Python client | `2.45.0` |

The required API surface includes `vane.func`, `vane.cls`, `vane.attach_function`, `vane.configure`, `vane.ai.load_provider`, `vane.ai.prompt`, and `duckdb.ray_cxx`. Any identity or API mismatch fails startup instead of silently falling back to ordinary DuckDB. Runtime upgrades must update the launcher and real end-to-end validation together.

## Data, credentials, and privacy

- Do not commit real procurement records, evidence documents, personal data, production credentials, model weights, or generated output.
- Fixture names, companies, scores, and documents are synthetic.
- `runtime.yml` contains loopback demo configuration only.
