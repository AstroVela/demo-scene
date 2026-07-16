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

Vane is a multi-compute engine for multimodal data: it lets structured records, documents, images, SQL, stateless Python UDFs, stateful actors, and AI models work together in one composable and traceable Relation pipeline. Vane also separates pipeline logic from execution backends. This demo is verified with both `local` and `ray`; the checked-in default remains `local`.

## Architecture

![Vane multimodal claims data flow](docs/vane-claims-data-flow.en.png)

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
3. Runs photo-quality analysis through the Vane Runner, reuses one RapidOCR engine for supporting documents, and extracts fields such as claim number, claimant name, and loss date to determine whether the materials are complete, legible, and consistent with the current claim.
4. Sends only photos that pass completeness, quality, and hash validation to Qwen, which extracts structured facts including target-vehicle clarity, visible damage, damaged parts, damage types, severity, confidence, and uncertainty reasons.
5. Enforces a strict contract on model responses and aggregates all photo results for each claim, identifying model failures, conflicting evidence, unclear target vehicles, insufficient confidence, and high-severity risks that prevent automated handling.
6. Applies deterministic SQL precedence for requesting more materials, manual review, denial candidates, and payment candidates; validates the nine-column output contract; and writes the result to PostgreSQL in one transaction. The built-in fixture verifies that all four workflow outcomes remain reproducible.

## Run the demo

The demo requires the verified Python/Vane environment plus running PostgreSQL, MinIO, and Qwen services. Follow the [complete runbook](docs/runbook.md) for installation and service setup, then run:

```bash
python scripts/run_demo.py e2e
```

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
├── runtime.yml
│   # Configures the Vane Runner (local/ray), PostgreSQL, MinIO, OCR, and Qwen.
│
├── scripts/
│   └── run_demo.py
│       # Demo entry point; verifies Python, Vane, and DuckDB before invoking the CLI.
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
│   │   # Main DAG orchestrator: reads PostgreSQL, registers SQL inputs, schedules
│   │   # material processing, AI, and decision SQL, then hands results to publication.
│   │   └── [Vane] Uses vane.configure to select Local or Ray Runner;
│   │       uses map_batches for material processing and model-response validation;
│   │       uses Relation.write_parquet as the shared materialization path.
│   │
│   ├── vane_udfs.py
│   │   # Implements photo quality, document fields, material quality, and AI JSON checks.
│   │   └── [Vane] Defines stateless Functions, the reusable RapidOCR
│   │       DocumentOcrActor, and batch actors executed by the Runner.
│   │
│   ├── photo_ai.py
│   │   # Re-reads and hashes photos, builds damage prompts, and binds every request
│   │   # and response to the same claim, file, and SHA-256.
│   │   └── [Vane] Calls Qwen through vane.ai.prompt and materializes model
│   │       responses through the active Runner.
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
│   │   │   ├── int_claim_material_facts.sql
│   │   │   │   # Defines the material-processing contract and aggregates per-file
│   │   │   │   # object, hash, quality, and OCR facts produced by the Vane Runner.
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

The execution path is `run_demo.py → cli.py → pipeline.py → Vane Function/Actor/AI → SQL Relations → output_writer.py → verify_outputs.py`. Vane supplies the switchable execution backend, batch actors, multimodal model calls, and Relation materialization. The SQL files retain material aggregation and final decision logic, so the model extracts facts without deciding whether to pay or deny a claim.

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
- [Local Qwen2.5-VL service guide](docs/local-qwen-service.md)
- [Chinese architecture diagram](docs/vane-claims-data-flow.png)

All claims, documents, and identifiers are synthetic. Do not commit real claims, customer photos, private documents, production credentials, model weights, or generated runtime data.
