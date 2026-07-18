# Procurement Conflict-of-Interest and Scoring Anomaly Audit with Vane

**English** | [简体中文](README.zh-CN.md)

Before a tender, one expert recommended “Jingwei Automation.” During evaluation, he did not recuse himself and gave that supplier an unusually high score; after removing his scores, the winner changes from `SUP-JW-001` to `SUP-ZJ-002`.

This demo combines structured score data with two image artifacts, extracts recommendation/participation/recusal facts, recalculates the ranking, and produces three deterministic audit findings:

```text
Result: review_required | 3 findings | flagged expert EXP-001
Winner recalculation: SUP-JW-001 -> SUP-ZJ-002
```

> The output is an evidence-backed review signal, not a legal, disciplinary, or final compliance decision.

## Why Vane

Vane is a multi-compute engine for multimodal data: it lets score tables, document images, SQL, stateless Python UDFs, stateful actors, and AI models work together in one composable and traceable Relation pipeline. The OCR worker is registered with `@vane.cls`, the strict response validator with `@vane.func`, and Qwen is accessed through Vane's AI APIs. The checked-in configuration defaults to the `local` Runner, and the same fixture is verified on both Local and Ray. Local creates one RapidOCR engine on the driver, processes each trusted evidence locator once, and exposes the immutable results to SQL; Ray attaches the OCR worker as a stateful expression and invokes Qwen through the `vane.ai.prompt` AI Function.

## Architecture

![Vane procurement compliance audit data flow](docs/vane-procurement-audit-data-flow.en.png)

The diagram shows the shared logical Relation boundaries; Local and Ray place OCR and AI execution differently as described below.

```text
PostgreSQL project/supplier/score/evidence rows + 2 MinIO PNG objects
  -> typed score and evidence relations
  -> RapidOCR (driver-local lookup or Ray actor)
  -> Qwen multimodal fact extraction
  -> strict AI response contract
  -> SQL score metrics and three audit rules
  -> audit_findings + audit_summary
```

1. Reads project, supplier, expert-score, and evidence-file metadata from PostgreSQL, then uses the stored `bucket/object_key` locators to read the recommendation record and committee minutes as two PNG images from MinIO.
2. Validates the project, suppliers, the complete four-expert-by-three-supplier score matrix, evidence roles, and MinIO locators so that downstream processing receives complete, trusted source data.
3. Calls `evidence_ocr_json` directly in `int_evidence_ocr_udf.sql`, then parses image text, OCR status, and confidence in `int_evidence_ocr.sql`; only quality-qualified evidence reaches multimodal analysis. Local uses one driver-owned RapidOCR engine and an immutable result lookup, while Ray uses the reusable stateful Actor. Both return the same OCR JSON contract.
4. Sends the images, OCR text, and supplier context to Qwen to extract structured facts such as which supplier the expert recommended, whether the expert participated or recused, the supporting evidence text, and confidence. A strict JSON contract and the trusted evidence role validate those facts.
5. Uses deterministic SQL to compare the related expert's score with the other experts' average and rank suppliers both with and without that expert, producing three findings: an undisclosed relationship without recusal, a materially elevated score, and a changed award result after removing the expert.
6. Produces `audit_findings.jsonl` and `audit_summary.jsonl`. With sufficient evidence, the result is `review_required` with reviewable metrics, thresholds, and evidence references; insufficient evidence is explicitly reported as `insufficient_evidence` instead of allowing the model to declare a violation.

## Run the demo

This demo requires CPython 3.12 and pins the public PyPI release `vane-ai==0.1.0a1`. The release provides CPython 3.10, 3.11, and 3.12 `manylinux_2_28_x86_64` wheels (glibc 2.28 or newer), but the launcher accepts only this demo's validated CPython 3.12 runtime. Follow the [complete runbook](docs/runbook.md) to create the environment, install Vane with `python -m pip install vane-ai`, install the demo with `python -m pip install -r requirements.txt`, and prepare running PostgreSQL, MinIO, and local Qwen services. Then run:

```bash
python scripts/run_demo.py e2e
```

`runtime.yml` defaults to `runner: local`. Set it to `runner: ray` and connect a Ray cluster to exercise the distributed Actor and AI Relation path; both modes have been verified with the real fixture, OCR, and Qwen service.

`e2e` seeds synthetic data into PostgreSQL/MinIO, then runs a pipeline whose inputs come only from those services. It performs real OCR and Qwen inference; there is no AI mock fallback. It produces:

```text
output/audit_findings.jsonl  # 3 rows
output/audit_summary.jsonl   # 1 row
```

## Implementation layout and where Vane is used

The DAG includes all 10 SQL files. Solid arrows show the main execution flow; dashed arrows show additional direct dependencies on trusted runtime data.

![Procurement compliance audit SQL dependency DAG](docs/vane-procurement-audit-sql-dag.png)

The purple `int_evidence_ai` node is not a SQL file: `ai.py` combines qualified OCR, trusted source metadata, MinIO image bytes, and supplier aliases, then uses Vane AI to create the relation that re-enters SQL validation.

```text
./
├── pyproject.toml
│   # Declares Python/runtime dependencies, including the exact public-PyPI Vane pin.
│
├── requirements.txt
│   # Installs this source tree and its fast-test extra from pyproject.toml.
│
├── runtime.yml
│   # Configures the Vane Runner (Local by default), PostgreSQL, MinIO, OCR,
│   # Qwen, and the JSONL output directory.
│
├── scripts/
│   └── run_demo.py
│       # Verifies CPython 3.12, exact Vane/DuckDB identities, required APIs,
│       # package origins, and loopback networking before invoking the CLI.
│
├── fixtures/expert-score-anomaly/
│   ├── project.json
│   ├── expert_scores.csv
│   ├── expert_recommendation.png
│   └── committee_minutes.png
│       # Local synthetic seed data used only to initialize PostgreSQL and MinIO;
│       # the pipeline never reads these files at runtime.
│
├── queries.sql
│   # Inspection queries for OCR, AI facts, score metrics, findings, and summary Relations.
│
├── src/procurement_audit_sql_demo/
│   ├── cli.py
│   │   # Dispatches fixture, run, and e2e; prints audit/ranking results and
│   │   # the capabilities exercised by the selected Runner.
│   │
│   ├── config.py
│   │   # Loads and strictly validates runtime.yml into typed runtime settings.
│   │
│   ├── fixture_loader.py
│   │   # Validates local seeds, writes business rows to PostgreSQL, and writes
│   │   # the recommendation and committee-minute images to MinIO.
│   │
│   ├── pg.py
│   │   # Defines the four raw project, supplier, expert-score, and evidence-locator
│   │   # tables and reads the complete business snapshot in stable order.
│   │
│   ├── minio_store.py
│   │   # Wraps MinIO image reads, uploads, bucket initialization, and fixture cleanup.
│   │
│   ├── source_data.py
│   │   # Validates the project, suppliers, 4×3 score matrix, and evidence locators,
│   │   # then converts them into a typed Arrow SourceBundle.
│   │
│   ├── pipeline.py
│   │   # Owns the driver DuckDB catalog and a separate Runner connection, stages
│   │   # their boundary through temporary Parquet, and executes all eight core Relations.
│   │   └── [Vane] vane.configure selects Local or Ray; Relation.write_parquet
│   │       materializes direct OCR/validation projections back into the driver catalog.
│   │       Local attaches a driver-built OCR lookup; Ray attaches EvidenceOcrActor.
│   │
│   ├── vane_functions.py
│   │   # Normalizes OCR output and enforces the strict AI JSON/document-type contract.
│   │   └── [Vane] @vane.func defines validate_audit_fact_json; @vane.cls defines
│   │       EvidenceOcrActor, instantiated on the Local driver or attached on Ray.
│   │
│   ├── ai.py
│   │   # Combines OCR text, supplier aliases, and images into multimodal requests,
│   │   # binds facts to trusted evidence roles, and retries one contract failure.
│   │   └── [Vane] Local uses vane.ai.load_provider and reuses one async prompter
│   │       on the driver; Ray uses vane.ai.prompt and Runner materialization.
│   │
│   ├── sql/
│   │   ├── staging/
│   │   │   ├── stg_scores.sql
│   │   │   │   # Normalizes valid PostgreSQL expert scores and attaches canonical supplier names and aliases.
│   │   │   └── stg_evidence_images.sql
│   │   │       # Selects OCR-supported PNG evidence while preserving trusted project, role, and MinIO locator fields.
│   │   │
│   │   ├── intermediate/
│   │   │   ├── int_evidence_ocr_udf.sql
│   │   │   │   # Direct Runner SQL invokes evidence_ocr_json for every staged evidence image.
│   │   │   ├── int_evidence_ocr.sql
│   │   │   │   # Parses Runner-produced JSON into typed OCR status, text, confidence, and line-count fields while retaining the raw response.
│   │   │   ├── int_conflict_validation_inputs.sql
│   │   │   │   # Joins each Vane AI response back to trusted PostgreSQL project, file, and evidence-role identities.
│   │   │   ├── int_conflict_validation_udf.sql
│   │   │   │   # Direct Runner SQL applies the strict AI response and document-type validator.
│   │   │   ├── int_conflict_facts.sql
│   │   │   │   # Parses validated JSON into typed compliance facts and rejects evidence whose trusted role disagrees with its document type.
│   │   │   └── int_score_metrics.sql
│   │   │       # Joins both evidence roles, resolves supplier aliases, computes expert-versus-peer deviation, and reranks with and without the expert.
│   │   │
│   │   └── marts/
│   │       ├── audit_findings.sql
│   │       │   # Emits the three deterministic non-recusal, score-bias, and award-impact findings only from eligible evidence.
│   │       └── audit_summary.sql
│   │           # Combines project settings, metrics, and finding counts into passed, review_required, or insufficient_evidence status.
│   │
│   ├── output_writer.py
│   │   # Validates findings, summary, and evidence references, then atomically
│   │   # replaces each of the two JSONL snapshots.
│   │
│   └── verify_outputs.py
│       # Verifies the synthetic case before publication: three findings and reranking.
│
└── tests/fast/
    # Covers source contracts, OCR Actor, AI contract, SQL DAG, Runner, and publication.
```

The execution path is `run_demo.py → cli.py → source_data.py → pipeline.py → Vane OCR/AI/validation → SQL Relations → verify_outputs.py → output_writer.py`. The driver reads PostgreSQL/MinIO, validates the Arrow `SourceBundle`, owns the pure-SQL DuckDB catalog, verifies the fixture result, and publishes JSONL. For the OCR and response-validation `*_udf.sql` projections, `pipeline.py` stages driver inputs as temporary Parquet, executes them through the selected Vane Runner, and registers the materialized results back in the driver catalog. Local builds the OCR lookup and AI response table with one driver-owned OCR implementation and one reused Vane provider prompter; Ray attaches the OCR Actor and executes AI through `vane.ai.prompt`. Downstream SQL keeps the same parsing, trusted-role filtering, score deviation, reranking, and audit-rule contracts.

## Audit logic and boundaries

The model extracts document type, expert, supplier, recommendation, participation, recusal, evidence text, and confidence; it does not decide whether a violation occurred. SQL produces:

1. `EXP-001-conflict-not-recused`: recommendation followed by participation without recusal.
2. `EXP-002-score-bias`: the related supplier is scored at least 15 points above peers.
3. `EXP-003-award-impact`: removing the expert changes the winner.

Both images must pass OCR and reach Qwen. Invalid response contracts fail the run; valid responses below the confidence threshold produce no findings and an `insufficient_evidence` summary.

## Adapt it to your environment

- Replace the four PostgreSQL raw tables with your procurement snapshot while preserving relation grain.
- Put your own MinIO/S3-compatible `bucket/object_key` locators in `evidence_files`.
- Replace RapidOCR while preserving the OCR JSON boundary.
- Point `runtime.yml` at another OpenAI-compatible multimodal model with the same response schema.
- Add explicit, testable SQL branches for new audit rules; keep risk conclusions out of the model.

## Documentation and data policy

- [Operational runbook](docs/runbook.md)
- [Local Qwen2.5-VL service guide](../docs/local-qwen-service.md) ([简体中文](../docs/local-qwen-service.zh.md))
- [Read-only intermediate queries](queries.sql)
- [Chinese architecture diagram](docs/vane-procurement-audit-data-flow.png)

Do not commit real procurement records, evidence documents, personal data, production credentials, model weights, or generated output.
