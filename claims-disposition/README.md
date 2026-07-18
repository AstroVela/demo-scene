# Auditable Multimodal Claims Triage with Vane

[简体中文](README.zh-CN.md)

A vehicle claim rarely lives in one table: claim records are stored in PostgreSQL, damage photos and supporting documents live in MinIO, and the final recommendation must remain reviewable. This demo turns those inputs into one Vane Relation pipeline that checks material quality, reuses a RapidOCR engine, extracts damage facts with a real local Qwen multimodal model, applies deterministic DuckDB SQL rules, and atomically publishes one recommendation per claim.

The synthetic fixture covers four workflow outcomes:

| Claim | Expected disposition |
| --- | --- |
| `CLM-APPROVE` | `approve_for_payment` |
| `CLM-DENY` | `deny_claim` |
| `CLM-MISSING` | `request_more_materials` |
| `CLM-REVIEW` | `manual_review` |

> These are workflow recommendations, not coverage decisions, liability findings, payment calculations, or regulated final denials.

## Why Vane

Vane is a multi-compute engine for multimodal data: it lets structured records, documents, images, SQL, stateless Python UDFs, stateful actors, and AI models work together in one composable and traceable Relation pipeline. Vane also separates pipeline logic from execution backends. The checked-in configuration defaults to the `local` Runner, and the same fixture is verified on both Local and Ray. Local creates one RapidOCR engine on the driver, processes each eligible supporting-document locator once, and exposes the immutable results to SQL; Ray initializes the native ONNX engine inside an isolated stateful Actor worker.

## Architecture

![Vane multimodal claims data flow](docs/vane-claims-data-flow.en.png)

The diagram shows the shared logical Relation boundaries; Local and Ray place OCR and AI execution differently as described below.

```text
PostgreSQL claims + MinIO photos/documents
  -> object, hash, quality, and OCR facts
  -> trusted photo requests
  -> Qwen multimodal fact extraction
  -> deterministic decision SQL
  -> contract validation
  -> atomic PostgreSQL publication
```

The core relation path is:

```text
stg_claims / stg_claim_materials / stg_run_config
  -> int_claim_material_facts
  -> int_claim_photo_ai
  -> int_claim_damage_facts
  -> int_claim_decision_facts
  -> claim_disposition
```

1. Reads four synthetic claims and their material metadata from PostgreSQL, then follows the stored MinIO locators to read vehicle-damage photos and supporting claim documents; the runtime never reads local fixture files directly.
2. Validates each material's file identity, order, role, media type, bucket, and canonical object path, then checks MinIO object existence and computes SHA-256 so that incorrect or replaced files cannot enter automated processing.
3. Calls `document_ocr_json` directly in `int_claim_document_ocr_udf.sql`, then lets downstream SQL extract claim number, claimant name, and loss date and determine whether the materials are complete, legible, and consistent with the current claim. Local runs one driver-owned RapidOCR engine and attaches an immutable result lookup; Ray attaches the reusable stateful Actor. Both paths return the same OCR JSON contract to SQL.
4. Sends only photos that pass completeness, quality, and hash validation to Qwen, which extracts structured facts including target-vehicle clarity, visible damage, damaged parts, damage types, severity, confidence, and uncertainty reasons.
5. Enforces the model-response contract in the direct Runner projection `int_claim_damage_validation_udf.sql`, then uses pure SQL to aggregate every photo result for each claim and identify failures, conflicting evidence, unclear target vehicles, insufficient confidence, and high-severity risks.
6. Applies deterministic SQL precedence for requesting more materials, manual review, denial candidates, and payment candidates; validates the nine-column output contract; and writes the result to PostgreSQL in one transaction. The built-in fixture verifies that all four workflow outcomes remain reproducible.

## Run the demo

This demo requires CPython 3.12 and pins the public PyPI release `vane-ai==0.1.0a1`. The release provides CPython 3.10, 3.11, and 3.12 `manylinux_2_28_x86_64` wheels (glibc 2.28 or newer), but the launcher accepts only this demo's validated CPython 3.12 runtime. Follow the [complete runbook](docs/runbook.md) to create the environment, install Vane with `python -m pip install vane-ai`, install the demo with `python -m pip install -r requirements.txt`, and prepare running PostgreSQL, MinIO, and Qwen services. Then run:

```bash
python scripts/run_demo.py e2e
```

`runtime.yml` defaults to `runner: local`. Set it to `runner: ray` and connect a Ray cluster to exercise the distributed Actor and AI Relation path; both modes have been verified with the real fixture, OCR, and Qwen service.

A successful run prints:

```text
loaded 4 claims and 8 objects
published 4 claim dispositions
verified 4 claim dispositions: CLM-APPROVE=approve_for_payment, CLM-DENY=deny_claim, CLM-MISSING=request_more_materials, CLM-REVIEW=manual_review
```

There is no AI mock fallback: unavailable services, unreadable images, invalid AI JSON, incompatible runtimes, SQL failures, and publication failures all exit nonzero.

## Implementation layout and where Vane is used

```text
claims-disposition/
├── pyproject.toml
│   # Declares Python/runtime dependencies, including the exact public-PyPI Vane pin.
│
├── requirements.txt
│   # Installs this source tree and its fast-test extra from pyproject.toml.
│
├── runtime.yml
│   # Configures the Vane Runner (Local by default), PostgreSQL, MinIO, OCR, and Qwen.
│
├── scripts/
│   └── run_demo.py
│       # Verifies CPython 3.12, exact Vane/DuckDB identities, required APIs,
│       # package origins, and loopback networking before invoking the CLI.
│
├── src/claims_disposition_sql_pipeline/
│   ├── cli.py
│   │   # Dispatches the fixture, run, verify, and e2e commands.
│   │
│   ├── config.py
│   │   # Loads and strictly validates runtime.yml into typed runtime settings.
│   │
│   ├── fixture_loader.py
│   │   # Generates synthetic claim materials and writes them to PostgreSQL and MinIO.
│   │   # The fixture initializes services; it is not a pipeline runtime source.
│   │
│   ├── pg.py
│   │   # Defines the PostgreSQL raw/output tables, reads claims, and probes connectivity.
│   │
│   ├── minio_store.py
│   │   # Wraps MinIO reads, existence checks, SHA-256, uploads, and cleanup.
│   │
│   ├── pipeline.py
│   │   # Owns the driver DuckDB catalog and a separate Runner connection, stages
│   │   # their boundary through temporary Parquet, and orchestrates the complete DAG.
│   │   └── [Vane] vane.configure selects Local or Ray; Relation.write_parquet
│   │       materializes each direct Runner SQL projection back into the driver catalog.
│   │       Local attaches a driver-built OCR lookup; Ray attaches DocumentOcrActor.
│   │
│   ├── vane_udfs.py
│   │   # Implements MinIO probes/hashes, photo quality, OCR normalization,
│   │   # document contracts, and strict model-response validation.
│   │   └── [Vane] Defines stateless Functions/attachment specs and the @vane.cls
│   │       DocumentOcrActor, instantiated on the Local driver or attached on Ray.
│   │
│   ├── photo_ai.py
│   │   # Re-reads and hashes photos, builds damage prompts, and binds every request
│   │   # and response to the same claim, file, and SHA-256.
│   │   └── [Vane] Local uses vane.ai.load_provider and reuses one async prompter
│   │       on the driver; Ray uses vane.ai.prompt and Runner materialization.
│   │
│   ├── sql/
│   │   ├── staging/
│   │   │   ├── stg_claims.sql
│   │   │   │   # Converts the PostgreSQL claim snapshot into a typed Claim Relation.
│   │   │   ├── stg_claim_materials.sql
│   │   │   │   # Expands materials_json per file and validates roles, media types,
│   │   │   │   # duplicate identities, and canonical MinIO locators.
│   │   │   └── stg_run_config.sql
│   │   │       # Exposes credential-free OCR, model, and run settings to SQL.
│   │   │
│   │   ├── intermediate/
│   │   │   ├── int_claim_material_inputs.sql / int_claim_object_facts.sql
│   │   │   │   # Express trusted locator gating and object availability as SQL facts.
│   │   │   ├── int_claim_*_udf.sql
│   │   │   │   # Direct Runner SQL projections for MinIO probes, hashes, photo quality,
│   │   │   │   # OCR, document contracts, and model-response validation.
│   │   │   ├── int_claim_material_facts.sql
│   │   │   │   # Joins UDF outputs and aggregates per-file facts into one claim row.
│   │   │   ├── int_claim_damage_validation_inputs.sql
│   │   │   │   # Binds model responses to trusted claim, file, and SHA-256 identities.
│   │   │   ├── int_claim_damage_facts.sql
│   │   │   │   # Aggregates Runner-validated photo damage facts per claim and
│   │   │   │   # detects conflicts, uncertainty, and high-severity risk.
│   │   │   └── int_claim_decision_facts.sql
│   │   │       # Uses deterministic SQL to build request-materials, manual-review,
│   │   │       # denial, and payment candidates with explicit precedence.
│   │   │
│   │   └── marts/
│   │       └── claim_disposition.sql
│   │           # Produces the final nine columns, including disposition, reason,
│   │           # next action, and supporting_facts_json.
│   │
│   ├── output_writer.py
│   │   # Validates the output contract and replaces the PostgreSQL snapshot atomically.
│   │
│   ├── verify_outputs.py
│   │   # Verifies that four synthetic claims produce the four intended outcomes.
│   │
│   └── assets/
│       # Synthetic vehicle photos and their provenance notices.
│
└── tests/fast/
    # Covers configuration, Runner orchestration, SQL paths, publication, and packaging.
```

The execution path is `run_demo.py → cli.py → pipeline.py → Vane Function/Actor/AI → SQL Relations → output_writer.py → verify_outputs.py`. The driver reads PostgreSQL/MinIO, owns the pure-SQL DuckDB catalog, and publishes the output. For every `*_udf.sql` projection, `pipeline.py` stages driver inputs as temporary Parquet, executes the projection through the selected Vane Runner, and registers the materialized result back in the driver catalog. Local builds the OCR lookup and AI response table with one driver-owned OCR implementation and one reused Vane provider prompter; Ray attaches the OCR Actor and executes AI through `vane.ai.prompt`. Both paths preserve the same SQL nodes and typed contracts.

## Decision boundary

Qwen extracts damage facts, confidence, and evidence limitations; it never decides whether to pay or deny a claim. SQL applies this priority order:

1. `request_more_materials`
2. `manual_review`
3. `deny_claim`
4. `approve_for_payment`

Missing material, uncertain evidence, conflicting photos, an unclear target vehicle, or high-severity risk cannot enter the automatic payment/denial candidates.

## Adapt it to your environment

- Replace the PostgreSQL snapshot with your claims source while keeping one row per claim.
- Replace MinIO with an S3-compatible object store behind the same locator/byte contract.
- Replace RapidOCR while preserving the OCR JSON boundary.
- Point `runtime.yml` at another OpenAI-compatible multimodal model that returns the same schema.
- Change or extend the SQL rules without moving the final decision into the model.

## Documentation and data policy

- [Operational runbook](docs/runbook.md)
- [Local Qwen2.5-VL service guide](../docs/local-qwen-service.md)
- [Chinese architecture diagram](docs/vane-claims-data-flow.png)

All claims, documents, and identifiers are synthetic. Do not commit real claims, customer photos, private documents, production credentials, model weights, or generated runtime data.
