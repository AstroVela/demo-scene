# Vane Procurement Expert Scoring Anomaly SQL Demo

**English** | [简体中文](README.zh-CN.md)

Before a tender, one expert recommended “Jingwei Automation.” During evaluation, he did not recuse himself and gave the same supplier an unusually high score. After removing his scores, the winner changes from `SUP-JW-001` to `SUP-ZJ-002`.

That is the entire demo. In one short pipeline, it shows how Vane coordinates stateful Python resources, a multimodal model, and deterministic SQL:

- a stateful UDF reuses a RapidOCR engine to read text from two images;
- an AI Function sends the images and OCR text to `Qwen2.5-VL-3B-Instruct` and extracts facts only;
- a stateless UDF strictly validates the AI JSON, forming a contract between the model and the rules;
- SQL calculates the scoring deviation, reranks the suppliers, and evaluates three audit rules.

Expected result: 3 findings, project status `review_required`, flagged expert `EXP-001`, and winner change `SUP-JW-001 -> SUP-ZJ-002`.

![Vane procurement compliance audit data flow](docs/vane-procurement-audit-data-flow.en.png)

[Open the editable Excalidraw source](docs/vane-procurement-audit-data-flow.en.excalidraw)

## Verified environment

This repository's release validation uses the following platform. Other platforms may work, but they are not currently verified:

| Item | Verified value |
| --- | --- |
| Operating system | Ubuntu 24.04 x86_64 (glibc 2.39) |
| Python | CPython 3.12 |
| Vane | `vane-ai==0.1.0.dev20260714234347` |
| Model service | `Qwen2.5-VL-3B-Instruct` on a local NVIDIA GPU |

The Vane wheel on TestPyPI is built for CPython 3.12, Linux x86_64, and `manylinux_2_39`. Support for older glibc versions, other Python minor versions, or other CPU architectures is therefore not guaranteed.

Install the basic Ubuntu tools:

```bash
sudo apt update
sudo apt install -y git curl python3.12 python3.12-venv
```

## Five-minute quickstart

All commands below run inside this project's own `.venv` and do not depend on a developer-specific local environment.

### 1. Create the project environment

```bash
cd procurement-compliance-audit
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### 2. Install the pinned Vane build from TestPyPI

```bash
python -m pip install -i https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  vane-ai==0.1.0.dev20260714234347
```

`--extra-index-url` allows regular Vane dependencies to continue coming from PyPI. Do not remove the pinned version: the launcher rejects runtimes that have not been validated for this demo.

### 3. Install project dependencies

```bash
python -m pip install -r requirements.txt
python -m pip check
```

This installs the current source in editable mode together with these direct dependencies:

- `openai==2.45.0`: calls the local OpenAI-compatible Qwen service;
- `rapidocr` and `onnxruntime`: provide the CPU OCR actor;
- `pillow`: reads the images;
- `pyarrow`: provides the Vane relation/Python data boundary;
- `pyyaml`: reads the strict `runtime.yml` configuration;
- `pytest`: runs the fast tests.

### 4. Start the local Qwen service

For complete NVIDIA, vLLM, model download, startup, and troubleshooting instructions, see:

**[Local Qwen2.5-VL service setup guide (Chinese)](docs/local-qwen-service.zh.md)**

The README checks only the service contract required by this project:

```bash
curl -fsS -o /dev/null -w 'health HTTP %{http_code}\n' \
  http://127.0.0.1:8001/health
curl -fsS -H 'Authorization: Bearer dummy' \
  http://127.0.0.1:8001/v1/models | python -m json.tool
```

The health endpoint should return HTTP 200, and the model list should contain `Qwen2.5-VL-3B-Instruct`. The standalone guide also includes a real-image `/v1/chat/completions` check.

### 5. Run the real demo

```bash
python scripts/run_demo.py
```

This command has no AI mock fallback. It fails explicitly if Qwen is unavailable, an image is unreadable, the AI JSON violates its contract, or the Vane API/version is incompatible.

On success, the terminal displays:

```text
Result: review_required | 3 findings | flagged expert EXP-001
Winner recalculation: SUP-JW-001 -> SUP-ZJ-002
```

Only two result files are produced:

```text
output/audit_findings.jsonl  # 3 rows
output/audit_summary.jsonl   # 1 row
```

You can run the fast deterministic tests without starting Qwen:

```bash
python -m pytest tests/fast -q
```

## Four source files

`fixtures/expert-score-anomaly/` contains exactly four pieces of business data:

| File | Grain | Purpose |
| --- | --- | --- |
| `project.json` | one procurement project | suppliers, image locators, original winner, and rule thresholds |
| `expert_scores.csv` | expert × supplier, 12 rows | total scores from 4 experts for 3 suppliers |
| `expert_recommendation.png` | one image artifact | `EXP-001` recommends Jingwei Automation before the tender |
| `committee_minutes.png` | one image artifact | `EXP-001` participates in the evaluation and is marked as not recused |

All names, companies, and documents are synthetic demo materials.

The scoring matrix is deliberately constructed so that:

- `SUP-JW-001` has the highest average score when all experts participate;
- `EXP-001` gives Jingwei a score of 98, while the other experts' average for Jingwei is 80, producing an 18-point deviation;
- `SUP-ZJ-002` has the highest average after `EXP-001` is removed.

## Eight-node data flow

```text
project.json + expert_scores.csv + 2 PNG
  ├── stg_scores
  └── stg_evidence_images
        └── int_evidence_ocr       [stateful UDF]
              └── int_evidence_ai  [Qwen multimodal AI Function]
                    └── int_conflict_facts [stateless UDF]

stg_scores + int_conflict_facts
  └── int_score_metrics            [SQL]
        └── audit_findings          [SQL, 3 rules]
              └── audit_summary    [SQL, 1 project]
```

| Relation | Materialization | Grain | Processing logic |
| --- | --- | --- | --- |
| `stg_scores` | view | expert × supplier | type, score, and supplier contracts |
| `stg_evidence_images` | view | evidence image | locators for the two PNG files |
| `int_evidence_ocr` | table | evidence image | full OCR text, confidence, and line count |
| `int_evidence_ai` | table | evidence image | raw Qwen JSON response |
| `int_conflict_facts` | view | evidence image | validated recommendation, participation, and recusal facts |
| `int_score_metrics` | view | project × conflict signal | peer average, score delta, and two rankings |
| `audit_findings` | table | finding | three deterministic rules |
| `audit_summary` | table | project | project-level audit status |

`int_evidence_ai` is the only intermediate relation materialized by Python. The reason is that the current multimodal entry point is a relation API rather than a text-only SQL built-in. The runner creates one logical single-row request per image and binds it directly to `project_id/file_id`, independently of the model actor's execution order. The returned `document_type` must match the file's trusted `role` from the fixture; otherwise, the contract is reinforced and retried once with the same image. The SQL fact node applies the same role binding again to prevent bypassing the Python boundary.

## Three key Vane APIs

Stateful OCR UDF:

```python
@vane.cls(actor_number=1, return_dtype="VARCHAR", name="evidence_ocr_json", gpus=0)
class EvidenceOcrActor:
    def __init__(self, allowed_root=None, engine_factory=None):
        self.engine = (engine_factory or build_rapidocr)()

    def __call__(self, local_path: str) -> str:
        value = Path(local_path).read_bytes()
        # One actor processes both images in sequence, initializing the engine once.
        return normalize_ocr_observations(self.engine(value))
```

Multimodal AI Function:

```python
result = vane.ai.prompt(
    one_image_relation,
    "prompt_text",
    image_columns=["image_bytes"],
    provider="openai",
    model="Qwen2.5-VL-3B-Instruct",
    provider_options=provider_options,
    prompt_options=prompt_options,
    system_message=AUDIT_FACT_SYSTEM_MESSAGE,
    output_column="raw_response",
    num_gpus=0,
)
```

Stateless AI contract UDF:

```python
@vane.func(return_dtype="VARCHAR", name="validate_audit_fact_json")
def validate_audit_fact_json_udf(raw_response: str) -> str:
    return validate_audit_fact_json(raw_response)

vane.attach_function(
    validate_audit_fact_json_udf,
    connection=connection,
    alias="validate_audit_fact_json",
    parameters=["VARCHAR"],
    replace=True,
)
```

## Why AI extracts facts only

The model answers only these questions: What kind of document is the image? What is the expert ID? What is the supplier name? Was the supplier recommended? Did the expert participate? Did the expert recuse? What is the source evidence text? How confident is the extraction?

The model does not decide whether a violation occurred. SQL produces the final conclusion, so thresholds, ranking methods, finding IDs, severities, and recommended actions remain reviewable, testable, and reproducible:

1. `EXP-001-conflict-not-recused`: the expert participates without recusal after recommending the supplier;
2. `EXP-002-score-bias`: the expert scores the related supplier at least 15 points above peers;
3. `EXP-003-award-impact`: the winner changes after the expert is removed.

The local service on port 8001 does not implement OpenAI `response_format`, and Qwen may wrap the JSON in one complete JSON code fence. The stateless contract normalizes only that single complete outer fence. It rejects prose outside the fence, missing or unknown fields, invalid types, and placeholder values such as “image source text.” The final response is first prevalidated at the AI boundary, then independently validated again by the actual attached stateless UDF in SQL.

The pipeline can continue only after both fixture images pass the OCR threshold and each has been sent to Qwen. If OCR misses either image, execution fails with the missing `file_id` and publishes no output, preventing the default command from appearing successful without running the complete AI scenario. If both calls complete but either AI confidence is below 0.75, SQL emits no deterministic findings and marks the summary as `insufficient_evidence`.

## Two outputs

A successful run produces only:

```text
output/audit_findings.jsonl
output/audit_summary.jsonl
```

With the normal fixture, the first file contains exactly three rows and the second exactly one. With insufficient evidence, findings contains zero rows while summary still contains one row with status `insufficient_evidence`. Before writing, the pipeline validates fields, primary keys, enums, counts, and evidence references, then atomically replaces each file using a temporary file in the same directory.

To debug intermediate relations in the same connection, run:

```sql
-- queries.sql contains all eight read-only queries
select * from int_score_metrics;
select * from audit_findings order by rule_id;
select * from audit_summary;
```

## Adapt it to your own scenario

- PostgreSQL / SRM: replace the four Arrow input tables produced by `fixture_loader.py` with database snapshots; the eight core relation names do not need to change.
- MinIO / S3: replace `local_path` with an object locator, and read validated object bytes inside the stateful actor.
- Enterprise OCR: retain the `evidence_ocr_json(local_path)` JSON contract while replacing actor initialization and invocation.
- Your own multimodal model: change the loopback endpoint/model in `runtime.yml` while retaining the OpenAI-compatible provider and eight-field response schema.
- New rules: add explicit, testable SQL branches to `audit_findings.sql`; do not let the model emit risk conclusions directly.

## Installation and runtime troubleshooting

| Symptom | Resolution |
| --- | --- |
| `No matching distribution found for vane-ai` | Confirm Ubuntu 24.04 x86_64 and Python 3.12, then use the complete TestPyPI and extra-index command |
| launcher reports a Python/Vane/DuckDB version mismatch | Reactivate `.venv` and reinstall with the pinned-version command; the error lists the current interpreter, prefix, and expected/actual values |
| `ModuleNotFoundError: openai` | Run `python -m pip install -r requirements.txt` and `python -m pip check` |
| Qwen health check or image request fails | See the ports, driver, OOM, model-name, and proxy troubleshooting in the [local Qwen service guide (Chinese)](docs/local-qwen-service.zh.md) |
| Output does not contain 3 findings | The default fixture's OCR or AI confidence did not meet the threshold; inspect the terminal error and Qwen response instead of substituting mock output |

## local and ray

By default, `runtime.yml` uses the runner supported by the currently installed version:

```yaml
runner: local
```

For distributed execution, change it to:

```yaml
runner: ray
```

The fixture, UDF contracts, AI relation calls, and seven SQL files remain unchanged. Current release validation covers and defaults to `local` only. `ray` is a configuration-compatible entry point, but it is not a verified release capability for this demo until a separate smoke test passes on the target cluster.

The launcher checks not only API shape but also the exact runtime identities validated for this demo:

| Component | Pinned identity |
| --- | --- |
| Vane distribution metadata (`vane-ai`) | `0.1.0.dev20260714234347` |
| `vane.__version__` | `0.1.0.dev20260714234347` |
| DuckDB Python package | `0.1.0.dev20260714234347` |
| DuckDB engine | `v1.6.0-dev121` |
| DuckDB source revision | `ca6948529b` |
| OpenAI Python client | `2.45.0` |

If any identity or required Vane API does not match, startup fails instead of silently falling back to ordinary DuckDB. When upgrading the runtime, update the launcher, design document, and real end-to-end validation results together.
