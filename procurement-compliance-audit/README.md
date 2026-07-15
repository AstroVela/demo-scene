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

Vane is a multi-compute engine for multimodal data: it lets score tables, document images, SQL, stateless Python UDFs, stateful actors, and AI models work together in one composable and traceable Relation pipeline. The pipeline can be developed locally and moved to high-concurrency distributed execution by switching the Runner rather than rewriting its business logic.

## Architecture

![Vane procurement compliance audit data flow](docs/vane-procurement-audit-data-flow.en.png)

[Open the PNG](docs/vane-procurement-audit-data-flow.en.png) · [Edit the Excalidraw source](docs/vane-procurement-audit-data-flow.en.excalidraw)

```text
PostgreSQL project/supplier/score/evidence rows + 2 MinIO PNG objects
  -> typed score and evidence relations
  -> stateful RapidOCR
  -> Qwen multimodal fact extraction
  -> strict AI response contract
  -> SQL score metrics and three audit rules
  -> audit_findings + audit_summary
```

## What the demo analyzes

| Input | Grain | Purpose |
| --- | --- | --- |
| PostgreSQL `projects` / `suppliers` | one project / project × supplier | Project, suppliers, original winner, and thresholds |
| PostgreSQL `expert_scores` | expert × supplier, 12 rows | Scores from four experts for three suppliers |
| PostgreSQL `evidence_files` | one row per artifact | Trusted role and MinIO `bucket/object_key` locator |
| Two MinIO PNG objects | one object per image artifact | Original recommendation and committee-minute bytes |

The core relations are:

```text
stg_scores / stg_evidence_images
  -> int_evidence_ocr
  -> int_evidence_ai
  -> int_conflict_facts
  -> int_score_metrics
  -> audit_findings
  -> audit_summary
```

All names, companies, and documents are synthetic. The fixture is constructed so that `EXP-001` scores Jingwei 18 points above the other experts' average and removing that expert changes the winner.

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

## Where Vane is used

| Need | Vane pattern |
| --- | --- |
| Reuse one initialized OCR engine across images | Stateful actor declared with `@vane.cls` |
| Send image bytes and OCR context to Qwen | Multimodal AI Function through `vane.ai.prompt` |
| Enforce the model-to-rule JSON boundary | Stateless UDF declared with `@vane.func` and attached to SQL |
| Calculate deviations, rerank suppliers, and produce findings | Relations and deterministic DuckDB SQL |

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
- [Local Qwen2.5-VL service guide (Chinese)](docs/local-qwen-service.zh.md)
- [Read-only intermediate queries](queries.sql)
- [Chinese architecture diagram](docs/vane-procurement-audit-data-flow.png)

Do not commit real procurement records, evidence documents, personal data, production credentials, model weights, or generated output.
