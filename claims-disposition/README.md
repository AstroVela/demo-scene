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

[Open the PNG](docs/vane-claims-data-flow.en.png) · [Edit the Excalidraw source](docs/vane-claims-data-flow.en.excalidraw)

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

All transformations except the AI relation are ordinary DuckDB `.sql` files; the pipeline has no dbt, Jinja, macro, or `ref()` dependency.

## What the demo does

1. Loads four synthetic claims from PostgreSQL and eight JPEG/PNG objects from MinIO.
2. Verifies locators, hashes, image quality, OCR fields, and claim-number consistency.
3. Sends only trusted damage photos to Qwen and extracts damage and uncertainty facts.
4. Applies deterministic SQL rules to choose one of the four workflow dispositions.
5. Validates the nine-column output contract and replaces the PostgreSQL result snapshot in one transaction.

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

## Where Vane is used

| Need | Vane pattern |
| --- | --- |
| Object, hash, image-quality, OCR-field, and AI-contract checks | Stateless UDFs declared with `@vane.func` |
| Reuse one initialized RapidOCR engine | Stateful actor declared with `@vane.cls(actor_number=1, gpus=0)` |
| Send image bytes and structured context to Qwen | Multimodal AI Function through `vane.ai.prompt` |
| Join facts and apply reviewable business rules | Relations and deterministic DuckDB SQL |

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
