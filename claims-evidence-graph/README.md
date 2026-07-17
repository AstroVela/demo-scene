# Claims Evidence Graph with Vane

Build auditable evidence tables from multimodal claim materials using Vane
relations, Python batch processors, SQL aggregation, and optional VLM image
understanding.

The public demo ships with a synthetic fixture generator. It does not include
real claim data, model weights, generated outputs, or third-party datasets with
unclear redistribution terms.

## Architecture

```text
photos / scanned documents / attachment metadata
  -> normalized claim and file manifests
  -> Arrow table / Vane relation
  -> relation.map_batches(...) photo and document processors
  -> optional semantic profile: vane.ai.prompt(image_columns=["file_bytes"])
  -> DuckDB SQL aggregation
  -> JSONL / Parquet / run metadata / validation report
```

The demo focuses on the data plane: stable table contracts, batch execution,
image-column handoff to a VLM, SQL review aggregation, and auditable output
materialization. It is not an insurance decisioning system and does not prove
real claim adjudication accuracy.

## Quick Start

Run the synthetic fixture demo:

Vane is installed from public PyPI. The reproducible environment currently constrains the public release to `vane-ai==0.1.0a1`; outside this setup it can also be installed directly with `python -m pip install vane-ai`.

```bash
cd claims-evidence-graph
./setup.sh
```

This will:

1. Create `.venv` if needed.
2. Install Python dependencies from `requirements.txt`.
3. Generate a synthetic local workspace under `workspace/quality-fixtures`.
4. Run the deterministic `baseline` profile.
5. Write JSONL outputs and validation metadata under
   `workspace/quality-fixtures/outputs`.

Expected fixture counts:

```text
claim_files: 8
photo_evidence: 7
document_evidence: 1
review_tasks: 5
claim_summary: 1
photo_human_labels: 7
photo_eval_metrics: 1
```

Check the run:

```bash
cat workspace/quality-fixtures/outputs/validation_report.json
cat workspace/quality-fixtures/outputs/run_metadata.json
```

The run is valid when `input.ok` and `output.ok` are both `true`.

## Example Queries

The fixture output is plain JSONL, so it can be inspected directly or queried
with DuckDB:

```bash
duckdb -init queries.sql
```

`queries.sql` reads the generated output files and shows:

- claim-level packet status;
- review tasks;
- photo evidence and quality review flags;
- photo quality evaluation metrics.

## Outputs

Baseline outputs:

```text
claim_files.jsonl
photo_evidence.jsonl
document_evidence.jsonl
evidence_nodes.jsonl
review_tasks.jsonl
claim_summary.jsonl
photo_human_labels.jsonl
photo_eval_metrics.jsonl
run_metadata.json
validation_report.json
```

Semantic outputs add:

```text
photo_damage_evidence.jsonl
photo_model_runs.jsonl
photo_damage_eval_metrics.jsonl  # when photo labels are supplied
```

Treat `validation_report.json` as the gate before drawing conclusions from any
evidence table. If semantic validation fails, retry or route the packet to human
review rather than trusting partial model output.

## Semantic Profile

The `semantic` profile adds photo damage evidence by calling an
OpenAI-compatible VLM endpoint through `vane.ai.prompt`.

Install optional model-server dependencies only when needed:

```bash
.venv/bin/python -m pip install -e ".[qwen]"
```

Start a local Qwen adapter after downloading model weights in your own
environment:

```bash
.venv/bin/claims-qwen-openai-server \
  --model-path "$HOME/models/Qwen2.5-VL-3B-Instruct" \
  --served-model-name Qwen2.5-VL-3B-Instruct \
  --host 127.0.0.1 \
  --port 8001
```

Run semantic processing against a prepared proxy workspace:

```bash
.venv/bin/claims-evidence-graph \
  --workspace-root workspace \
  --data-root workspace/claims-poc \
  --output-dir workspace/outputs-semantic \
  --profile semantic \
  --image-model Qwen2.5-VL-3B-Instruct \
  --image-model-base-url http://127.0.0.1:8001/v1 \
  --image-model-api-key EMPTY \
  --execution-backend local \
  --batch-size 8 \
  --skip-parquet
```

Use `--profile semantic_strict --max-image-model-errors 0` when any VLM failure
should fail the run instead of being routed to review.

## Full Proxy Workspace

For a fuller proxy-data run, prepare data locally after checking each source
license. Do not commit raw data to this repository.

```text
workspace/
  claims-poc/
    manifests/
      claims.jsonl
      claim_files.jsonl
    claim_packets/
  raw/
    cardd/
    funsd/
```

Run the deterministic profile on that workspace:

```bash
.venv/bin/claims-evidence-graph \
  --workspace-root workspace \
  --data-root workspace/claims-poc \
  --output-dir workspace/outputs-baseline \
  --profile baseline \
  --execution-backend local \
  --batch-size 8 \
  --skip-parquet
```

The manifests are an ingestion contract for the demo. In a real system, an
upload service, object-store inventory, claim API, or spreadsheet export should
produce equivalent claim/file relations automatically.

## How It Works

The pipeline is implemented as an importable Python package under
`src/claims_evidence_graph_pipeline`:

- `contracts.py` defines Arrow and DuckDB table contracts.
- `quality_fixtures.py` builds the public synthetic fixture workspace.
- `pipeline.py` orchestrates validation, Vane relation execution, SQL
  aggregation, and output materialization.
- `udfs.py` contains the photo quality and document extraction batch processors.
- `photo_vlm.py` contains the optional semantic image evidence path.
- `validation.py` validates inputs and outputs before results are trusted.

The same code path supports local, Ray task, and Ray actor execution backends.

## Tests

Tests are intentionally kept as fast contract checks for the package code. They
do not require raw proxy data or a running model endpoint.

```bash
.venv/bin/python -m pytest tests/fast/test_claims_evidence_graph_pipeline.py -q
```

## Cleanup

Remove generated fixture data:

```bash
./teardown.sh
```

Remove generated data and the local virtual environment:

```bash
./teardown.sh -v
```

## Data And Privacy Policy

- Do not commit real claims, customer photos, private documents, API keys, or
  model weights.
- Do not commit `workspace/raw` unless every source license explicitly permits
  redistribution.
- Generated JSONL/Parquet outputs are reproducible artifacts and should stay
  local unless they are intentionally curated for release.
- The bundled fixture generator creates synthetic local data for CI and public
  demos.
