# Procurement Audit Operational Runbook

[Back to the use case](../README.md) · [简体中文](runbook.zh-CN.md)

This runbook contains the exact environment, installation, model-service, configuration, execution, and troubleshooting contracts for the procurement audit demo.

## Verified environment

| Item | Verified value |
| --- | --- |
| Operating system | Ubuntu 24.04 x86_64, glibc 2.39 |
| Python | CPython 3.12 |
| Vane | `vane-ai==0.1.0.dev20260714234347` |
| PostgreSQL | `127.0.0.1:5432`, database `vane_insight` |
| MinIO | `127.0.0.1:9000`, HTTP |
| Model service | `Qwen2.5-VL-3B-Instruct` on a local NVIDIA GPU |

The TestPyPI Vane wheel targets CPython 3.12, Linux x86_64, and `manylinux_2_39`. Support for older glibc versions, other Python minor versions, or other CPU architectures is not guaranteed.

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

### 2. Install the verified Vane wheel

```bash
python -m pip install -i https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  vane-ai==0.1.0.dev20260714234347
```

Keep both indexes so Vane comes from TestPyPI while ordinary dependencies resolve from PyPI. Keep the exact version because the launcher rejects runtimes not validated for this demo.

### 3. Install the demo

```bash
python -m pip install -r requirements.txt
python -m pip check
```

This installs the source in editable mode; `pyproject.toml` is authoritative:

| Dependency | Purpose |
| --- | --- |
| `vane-ai==0.1.0.dev20260714234347` | Vane APIs, custom DuckDB, and workers |
| `openai==2.45.0` | OpenAI-compatible Qwen client |
| `psycopg` | Read and initialize PostgreSQL raw tables |
| `minio` | Read and initialize raw material objects in MinIO |
| `rapidocr`, `onnxruntime` | Stateful CPU OCR actor |
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

The model server must use a separate environment. Follow the checked-in [Qwen2.5-VL setup guide (Chinese)](local-qwen-service.zh.md) for NVIDIA, vLLM, model download, startup, and troubleshooting.

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

At runtime, the authoritative sources are the PostgreSQL `projects`, `suppliers`, `expert_scores`, and `evidence_files` tables plus the MinIO bucket `procurement-compliance-audit-fixtures`. `evidence_files.bucket/object_key` is the trusted PostgreSQL-to-MinIO locator. The pipeline, OCR actor, and AI request builder never read local paths.

## Relation contracts

| Relation | Materialization | Grain | Purpose |
| --- | --- | --- | --- |
| `stg_scores` | view | expert × supplier | Type, score, and supplier contracts |
| `stg_evidence_images` | view | evidence image | Trusted locator and role |
| `int_evidence_ocr` | table | evidence image | OCR text, confidence, and line count |
| `int_evidence_ai` | table | evidence image | Raw Qwen JSON response |
| `int_conflict_facts` | view | evidence image | Validated recommendation, participation, and recusal facts |
| `int_score_metrics` | view | project × conflict signal | Peer average, score delta, and both rankings |
| `audit_findings` | table | finding | Three deterministic rules |
| `audit_summary` | table | project | Project-level audit status |

`int_evidence_ai` is the only Python-materialized intermediate relation. Each image request is bound to `project_id/file_id`, independently of actor execution order. The returned `document_type` must match the fixture's trusted role; a mismatch triggers one contract-reinforced retry with the same image, and SQL applies the role binding again.

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

For a compatible distributed entry point, change:

```yaml
runner: local
```

to:

```yaml
runner: ray
```

PostgreSQL/MinIO source contracts and business SQL remain unchanged. Both modes are end-to-end verified on the single-host fixture and use the same Runner-backed Relation path. Driver-local Arrow inputs are staged as temporary Parquet files, and `Relation.write_parquet()` dispatches OCR, AI, and validation plans through the active Vane Runner (`LocalRunner.run_write` or `RayRunner.run_write`). Vane selects the matching subprocess or Ray execution backend for batch functions. The materialized Arrow results are then registered in the driver's DuckDB catalog for deterministic joins and aggregates. A real multi-node target cluster still requires its own infrastructure smoke test.

The pinned Vane build does not implement `LocalRunner.run_iter_tables`, so the shared materializer deliberately uses the Runner-backed write API instead of a direct DuckDB export. A narrow compatibility adapter only normalizes the Local Runner progress-callback signature in this build; it does not bypass the Runner.

## Troubleshooting

| Symptom | Resolution |
| --- | --- |
| `No matching distribution found for vane-ai` | Confirm Ubuntu 24.04 x86_64 and Python 3.12, then use the complete TestPyPI plus extra-index command |
| Python/Vane/DuckDB version mismatch | Reactivate `.venv` and reinstall the pinned wheel; the launcher reports the current interpreter, prefix, and expected/actual values |
| A direct dependency is missing | Run `python -m pip install -r requirements.txt` and `python -m pip check` |
| PostgreSQL connection, authentication, or table initialization failure | Check the DSN, database/role, port, and schema/table read/write permissions |
| MinIO connection, authentication, or object-read failure | Check endpoint, HTTP/TLS, access key, and bucket list/read/write/delete permissions |
| Qwen health or image request fails | Use the [Qwen guide](local-qwen-service.zh.md) to check port, driver, OOM, model name, and proxy settings |
| Output does not contain three findings | Inspect the terminal error and Qwen response; the default fixture's OCR or AI confidence did not meet its threshold |

## Exact runtime identifiers

| Component | Required identifier |
| --- | --- |
| Vane distribution metadata (`vane-ai`) | `0.1.0.dev20260714234347` |
| `vane.__version__` | `0.1.0.dev20260714234347` |
| DuckDB Python package | `0.1.0.dev20260714234347` |
| DuckDB engine | `v1.6.0-dev121` |
| DuckDB source revision | `ca6948529b` |
| OpenAI Python client | `2.45.0` |

Any identity or required Vane API mismatch fails startup instead of silently falling back to ordinary DuckDB. Runtime upgrades must update the launcher and real end-to-end validation together.

## Data, credentials, and privacy

- Do not commit real procurement records, evidence documents, personal data, production credentials, model weights, or generated output.
- Fixture names, companies, scores, and documents are synthetic.
- `runtime.yml` contains loopback demo configuration only.
