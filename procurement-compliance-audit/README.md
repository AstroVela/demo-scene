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

Vane is a multi-compute engine for multimodal data: it lets score tables, document images, SQL, stateless Python UDFs, stateful actors, and AI models work together in one composable and traceable Relation pipeline. The OCR worker is registered with `@vane.cls` and attached as a SQL-callable stateful expression. The strict response validator is registered with `@vane.func`, and Qwen is invoked through the `vane.ai.prompt` AI Function. The pipeline can be developed locally and moved to high-concurrency distributed execution by switching the Runner rather than rewriting its business logic.

## Architecture

![Vane procurement compliance audit data flow](docs/vane-procurement-audit-data-flow.en.png)

```text
PostgreSQL project/supplier/score/evidence rows + 2 MinIO PNG objects
  -> typed score and evidence relations
  -> stateful RapidOCR
  -> Qwen multimodal fact extraction
  -> strict AI response contract
  -> SQL score metrics and three audit rules
  -> audit_findings + audit_summary
```

1. Reads project, supplier, expert-score, and evidence-file metadata from PostgreSQL, then uses the stored `bucket/object_key` locators to read the recommendation record and committee minutes as two PNG images from MinIO.
2. Validates the project, suppliers, the complete four-expert-by-three-supplier score matrix, evidence roles, and MinIO locators so that downstream processing receives complete, trusted source data.
3. Calls the attached stateful `evidence_ocr_json` expression directly in `int_evidence_ocr_udf.sql`, then parses image text, OCR status, and confidence in `int_evidence_ocr.sql`; only quality-qualified evidence reaches multimodal analysis, and the Actor reuses one RapidOCR engine.
4. Sends the images, OCR text, and supplier context to Qwen to extract structured facts such as which supplier the expert recommended, whether the expert participated or recused, the supporting evidence text, and confidence. A strict JSON contract and the trusted evidence role validate those facts.
5. Uses deterministic SQL to compare the related expert's score with the other experts' average and rank suppliers both with and without that expert, producing three findings: an undisclosed relationship without recusal, a materially elevated score, and a changed award result after removing the expert.
6. Produces `audit_findings.jsonl` and `audit_summary.jsonl`. With sufficient evidence, the result is `review_required` with reviewable metrics, thresholds, and evidence references; insufficient evidence is explicitly reported as `insufficient_evidence` instead of allowing the model to declare a violation.

## Run the demo

The demo requires the verified Python/Vane environment plus running PostgreSQL, MinIO, and local Qwen services. Follow the [complete runbook](docs/runbook.md), then run:

```bash
python scripts/run_demo.py e2e
```

`e2e` seeds synthetic data into PostgreSQL/MinIO, then runs a pipeline whose inputs come only from those services. It performs real OCR and Qwen inference; there is no AI mock fallback. It produces:

```text
output/audit_findings.jsonl  # 3 rows
output/audit_summary.jsonl   # 1 row
```

## Implementation layout and where Vane is used

```text
<project-root>/
├── runtime.yml
│   # Configures the Vane Runner (local/ray), PostgreSQL, MinIO, OCR,
│   # Qwen, and the JSONL output directory.
│
├── scripts/
│   └── run_demo.py
│       # Demo entry point; verifies Python, Vane, and DuckDB before invoking the CLI.
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
│   │   # Dispatches fixture, run, and e2e and prints the audit and ranking results.
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
│   │   # Main eight-node DAG orchestrator: reads sources, runs OCR and AI,
│   │   # executes score SQL, builds two marts, and publishes the final JSONL.
│   │   └── [Vane] Uses vane.configure to select Local or Ray Runner;
│   │       runs stateful OCR and AI-response validation directly from SQL;
│   │       uses Relation.write_parquet for Runner-backed SQL and AI stages.
│   │
│   ├── vane_functions.py
│   │   # Normalizes OCR output and enforces the strict AI JSON contract.
│   │   └── [Vane] validate_audit_fact_json is a stateless Function;
│   │       EvidenceOcrActor is attached as the stateful evidence_ocr_json
│   │       SQL expression and reuses one RapidOCR engine.
│   │
│   ├── ai.py
│   │   # Combines OCR text, supplier aliases, and images into multimodal requests,
│   │   # binds facts to trusted evidence roles, and retries one contract failure.
│   │   └── [Vane] Calls Qwen through vane.ai.prompt and materializes each
│   │       evidence response through the active Runner.
│   │
│   ├── sql/
│   │   ├── staging/
│   │   │   ├── stg_scores.sql
│   │   │   │   # Normalizes PostgreSQL expert scores and joins supplier names and aliases.
│   │   │   └── stg_evidence_images.sql
│   │   │       # Selects OCR-supported PNG evidence and trusted MinIO locators.
│   │   │
│   │   ├── intermediate/
│   │   │   ├── int_evidence_ocr_udf.sql
│   │   │   │   # Calls evidence_ocr_json as a direct Runner SQL projection.
│   │   │   ├── int_evidence_ocr.sql
│   │   │   │   # Converts the Runner-produced JSON into typed OCR fields.
│   │   │   ├── int_conflict_validation_inputs.sql
│   │   │   │   # Binds AI responses to trusted PostgreSQL evidence roles.
│   │   │   ├── int_conflict_validation_udf.sql
│   │   │   │   # Applies the strict response validator as a direct Runner SQL UDF.
│   │   │   ├── int_conflict_facts.sql
│   │   │   │   # Converts Runner-validated AI JSON into typed recommendation,
│   │   │   │   # participation, recusal, evidence-text, and confidence facts.
│   │   │   └── int_score_metrics.sql
│   │   │       # Matches supplier names and aliases, computes expert-versus-peer
│   │   │       # score deviation, and reranks suppliers with and without the expert.
│   │   │
│   │   └── marts/
│   │       ├── audit_findings.sql
│   │       │   # Uses deterministic SQL for non-recusal, score-bias, and award-impact findings.
│   │       └── audit_summary.sql
│   │           # Summarizes findings as passed, review_required,
│   │           # or insufficient_evidence.
│   │
│   ├── output_writer.py
│   │   # Validates findings, summary, and evidence references, then atomically writes JSONL.
│   │
│   └── verify_outputs.py
│       # Verifies the synthetic case produces three findings and the expected reranking.
│
└── tests/fast/
    # Covers source contracts, OCR Actor, AI contract, SQL DAG, Runner, and publication.
```

The execution path is `run_demo.py → cli.py → source_data.py → pipeline.py → Vane OCR/AI/validation → SQL Relations → output_writer.py`. Vane attaches the reusable OCR actor and response validator as SQL expressions and lets Local and Ray share the same direct-UDF and AI Relation boundaries. Each UDF call is isolated in a visible `*_udf.sql` node; downstream SQL owns parsing, trusted-role filtering, score deviation, reranking, and audit rules. Python remains only for source/service I/O, AI request binding and retry, Runner materialization, and atomic output publication.

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
