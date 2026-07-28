# Fund Investment Research Demo — Codex Handoff

## Workspace

- Use case: `fund-investment-research/`
- Branch: `fund-investment-research-demo`
- Base commit: `d2c6d9f5224a56ba0f4c077317b87bec68902eaa`
- Primary plan: `fund-investment-research/IMPLEMENTATION_PLAN.md`

The implementation follows this plan. Keep this handoff synchronized with the
checked-in Ray-only Demo and its acceptance evidence.

## Objective

Add a new Vane use case to this repository, similar in release shape and engineering discipline to:

- `claims-disposition`
- `procurement-compliance-audit`

The new use case is an auditable multimodal fund-investment-research workflow:

```text
historical research meeting audio and reports
  → domain-aware transcription and correction
  → source-bound research facts
  → new external signal facts
  → evidence-grounded links to approved thesis conditions
  → deterministic signal state and analyst review task
```

It must be a focused E2E business demo, not a generic research chatbot.

The customer-facing story must visibly demonstrate four direct business improvements:

1. higher-quality and faster research-knowledge production;
2. faster and deeper risk/opportunity signal analysis;
3. targeted verification instead of reopening every source and researching from scratch;
4. faster rollout of new research terms, rules and methods.

These business improvements are not claimed as capabilities that only Vane can provide. The Demo must separately show how Vane makes them repeatable, governed, reusable and recoverable.

## Confirmed Scope

The first release must provide only a small reproducible E2E case with:

- one fully synthetic biotech company;
- one approved, versioned investment thesis with typed conditions;
- multimodal inputs in PostgreSQL and MinIO;
- real ASR, OCR and AI execution with no runtime mock fallback;
- source identity, hash, quality and AI-response contracts;
- evidence-grounded event/metric/thesis impact edges;
- deterministic SQL status calculation;
- explicit separation of source facts, approved theses, model hypotheses and unresolved uncertainty;
- analyst-readable transcript correction and signal-evidence artifacts;
- one data-driven glossary-change drill that changes behavior without Pipeline code changes;
- stage identities and persisted processing status for failure isolation and changed/failed-input recomputation;
- ordinary Python business functions connected through thin Vane Function, Batch UDF or Actor adapters;
- atomic, cross-reference-validated outputs;
- `fixture`, `run`, `verify` and `e2e` commands;
- Ray Runner as the only formal execution backend; no Local Runner branch.

The fixture must produce exactly four signal states:

1. `SIG-CLINICAL=thesis_review_required`
2. `SIG-RUNWAY=thesis_supported`
3. `SIG-REGULATORY=manual_review`
4. `SIG-RUMOR=insufficient_evidence`

These are analyst-workflow states. They are not buy/sell decisions, target prices, position recommendations or final investment conclusions.

## Explicit Non-Goals

Do not add any of the following to this use case:

- benchmarks or performance comparisons;
- a runnable serial-Python comparison Pipeline or benchmark; documentation may explain what equivalent script orchestration would need to implement;
- CPU/GPU utilization or resource-cost collection;
- a benchmark CLI command;
- smart data querying;
- a Web UI or chat interface;
- a general vector database or generic RAG product;
- live web crawling or market-data ingestion;
- automatic modification of approved investment theses;
- automated trading or portfolio decisions;
- a real DolphinScheduler deployment.

Performance and resource-cost advantages will be demonstrated in a separate benchmark project.

## Key Design Decisions

### Business-value demonstration contract

The default E2E and its verification output must make the four business improvements inspectable without a Web UI:

| Direct improvement | Required visible action | Required evidence |
|---|---|---|
| Better research-knowledge production | Trace one audio segment from raw ASR through domain correction and quality disposition | timestamped raw/corrected transcript, correction event and knowledge status |
| Deeper signal analysis | Expand one new signal through facts, metrics, thesis conditions and both supporting and opposing evidence | analyst-readable evidence report with source locators and uncertainty |
| Targeted verification | Show that accepted facts, model hypotheses and unresolved items are separated | typed knowledge semantics and focused review tasks |
| Faster method rollout | Add a domain-term alias as data and rerun without editing Pipeline code | changed correction outcome plus unchanged Pipeline implementation |

`verify` should print mechanical PASS/FAIL assertions for these actions. It does not need to claim percentage improvements or measured analyst-time savings.

### Business fixture

Use the fully synthetic company `澜星生物` (`SYN-BIO-001`) and product `LX-101`, a fictional Nectin-4 ADC.

Approved thesis conditions:

- ORR must be at least 40%.
- Grade 3+ TRAE must be at most 35%.
- Cash runway must be at least 18 months.
- A key regulatory filing must progress as planned; trusted conflicts require human review.

The internal meeting audio should include terms such as Nectin-4, ADC, ORR, TRAE, DOR, PFS and BLA so that the ASR/domain-correction path is material to the story.

### Trust boundary

- PostgreSQL metadata supplies the trusted company, source role, trust tier and MinIO locator.
- The model cannot overwrite trusted identities or source roles.
- Low-trust chat screenshots cannot independently drive `thesis_supported` or `thesis_review_required`.
- AI may extract facts and propose impact relationships, but SQL owns the final state.
- Approved investment theses are read-only and versioned.
- Outputs must distinguish source facts, approved theses, model hypotheses and unresolved uncertainty.
- “Causal” output must be described as an evidence-grounded impact chain or causal hypothesis, never as automatically proven real-world causality.

### Audio boundary

- Preserve raw and corrected transcripts.
- Every correction must retain the original span, canonical term, glossary identity, reason and confidence.
- Numbers cannot be silently rewritten; uncertain numbers become review tasks.
- Gold transcripts and term labels are evaluation-only and must not enter the production fact path.
- The ASR implementation should use a reusable stateful Actor and configurable model path/device.
- Timestamped raw/corrected segments and individual correction events must be published as inspectable Demo artifacts.

### Change and recovery boundary

- Keep the four top-level commands; use optional fixture/run flags for the glossary-change and recovery drills.
- A stage result is identified by `source_id + source_sha256 + stage + stage_version`.
- Persist stage status and a result locator so a resumed run can select only new, changed or failed inputs.
- Row-level data-quality failures are quarantined with structured reasons and cannot enter automatic analysis.
- Systemic storage, model-service, SQL or publication failures still fail the run and preserve the last successful published snapshot.
- The recovery walkthrough must use a real invalid/corrected synthetic input, not a runtime mock fallback.

### Existing-Python boundary

- Core parsing, normalization, correction and response-validation logic should remain ordinary testable Python functions.
- Vane-specific modules should provide thin Function, Batch UDF or Actor adapters without duplicating business logic.
- The Demo may explain the additional state, idempotency, retry, merge and publication work a script-only design would require, but it must not implement or benchmark a second Python Pipeline.

### Output boundary

Planned outputs:

- `transcript_segments.jsonl`
- `asr_corrections.jsonl`
- `research_facts.jsonl`
- `thesis_impact_edges.jsonl`
- `research_signals.jsonl`
- `review_tasks.jsonl`
- `source_processing_status.jsonl`
- `asr_quality_metrics.json`
- `run_manifest.json`
- `signal_evidence_report.md`

Validate primary keys, knowledge/evidence semantics, enums, units, evidence references, thesis-condition references and low-trust-source isolation before atomic publication.

## Important References

Read these before implementing:

1. `fund-investment-research/IMPLEMENTATION_PLAN.md`
2. `claims-disposition/README.md`
3. `claims-disposition/src/claims_disposition_sql_pipeline/pipeline.py`
4. `claims-disposition/src/claims_disposition_sql_pipeline/vane_udfs.py`
5. `procurement-compliance-audit/README.zh-CN.md`
6. `procurement-compliance-audit/src/procurement_audit_sql_demo/pipeline.py`
7. `procurement-compliance-audit/src/procurement_audit_sql_demo/vane_functions.py`
8. The local Vane checkout's `examples/voice_ai_analytics.py`
9. The local Vane checkout's
   `multimodal_inference_benchmarks/audio_transcription/vane_main.py`

The customer-facing business proposal is maintained separately from this
public repository.

## Current State

- The complete package, strict Ray-only runtime configuration, launcher, CLI,
  synthetic fixture generator, SQL, outputs and verification code exist under
  `fund-investment-research/`.
- The launcher requires the image-capable local Vane wheel under `~/vane` and
  checks exact Vane/DuckDB identities before dispatch.
- Real Ray execution has passed ASR, glossary correction, RapidOCR,
  `vane.ai.prompt(..., image_columns=["image_bytes"])`, strict AI contracts,
  deterministic SQL and atomic publication.
- The default fixture publishes all four expected states, 14 facts and four
  focused review tasks; the value-oriented verifier passes.
- The glossary-before/glossary-after drill passes with an unchanged Pipeline
  hash and changed glossary hash, proving data-driven behavior change.
- The real corrupt/fixed-source recovery drill passes; resume recomputes only
  the affected clinical source stages and does not publish a partial snapshot.
- All 13 fast tests pass across configuration, strict contracts, domain logic,
  image-column forwarding, role semantics and deterministic state SQL.
- English/Chinese README, runbooks, walkthroughs, editable Excalidraw sources,
  rendered diagrams, queries and root README entries are present.

## Maintenance Notes

- Keep `runner: ray` strict; do not add a Local Runner fallback.
- Use `scripts/run_demo.py` so the local `~/vane` and image API checks cannot be
  bypassed accidentally.
- Bump the relevant stage version whenever ASR, OCR, AI or rule behavior changes.
- Preserve the model/SQL trust boundary: AI proposes observations and impact
  hypotheses; SQL owns final workflow state.
- Re-run the default, glossary and recovery scenarios after changes that affect
  Pipeline code or output contracts.

## Acceptance Summary

The first milestone is complete only when one real-service command:

```bash
.venv/bin/python scripts/run_demo.py e2e
```

loads the synthetic sources into PostgreSQL/MinIO, runs real ASR/OCR/AI, publishes cross-reference-valid outputs and verifies the four expected signal states without reading local fixture files during Pipeline execution.

The milestone also requires a documented glossary-change drill and recovery/resume drill. The walkthrough must point to real functions, Relations, SQL and output artifacts for every claimed non-performance advantage; it must not rely on an implemented Python comparison Pipeline.
