# Fund Research Demo Walkthrough

This walkthrough ties every non-performance advantage to a real action, code path, and artifact. It does not claim time-saved percentages and does not implement a second serial-Python Pipeline.

Start with:

```bash
.venv/bin/python scripts/run_demo.py e2e
```

## 1. Knowledge production

Inspect:

```bash
sed -n '1,3p' output/default/current/transcript_segments.jsonl
sed -n '1,20p' output/default/current/asr_corrections.jsonl
cat output/default/current/asr_quality_metrics.json
```

Show raw Whisper text, traceable `LX101 → LX-101` / `actin-4 → Nectin-4` events, and the unresolved “six or sixteen months” number. The code path is `configured_asr_actor → apply_domain_glossary → configured_glossary_function → _quality_metrics`.

A plain Python script could call the same services, but equivalent behavior would also need worker lifecycle/reuse, batching, stable segment IDs, raw/corrected dual storage, per-edit events, numeric invariants, retry policy, and output contracts.

## 2. Deeper signal analysis

```bash
sed -n '1,80p' output/default/current/signal_evidence_report.md
rg 'SIG-CLINICAL|SUBGROUP_ORR' \
  output/default/current/research_facts.jsonl \
  output/default/current/thesis_impact_edges.jsonl \
  output/default/current/research_signals.jsonl
```

The clinical signal keeps opposing overall ORR/safety evidence and a supporting n=8 subgroup hypothesis. The SQL state remains `thesis_review_required`. The regulatory signal preserves two trusted, incompatible BLA statements and becomes `manual_review`.

Point to `extract_document_with_vane`, `ROLE_REQUIRED_METRICS`, `ROLE_REQUIRED_IMPACTS`, `bind_ai_facts`, and `research_signals.sql`.

A script-only implementation would need request/source binding, JSON and role-semantic retries, normalization, trust joins, conflict sets, typed condition evaluation, and a rule layer that does not adopt the model's final-state prose.

## 3. Focused verification

```bash
cat output/default/current/review_tasks.jsonl
```

Every task names a judgment, fact IDs, an original locator, and a bounded next action. The evidence report separates `source_fact`, `approved_thesis`, `model_hypothesis`, and `uncertainty`. A tier-3 rumor can request source verification but cannot drive an automatic thesis state.

The relevant code is `_review_tasks`, `_validate_outputs`, and `render_evidence_report`.

A script would additionally need stable keys, reference-integrity checks, trust gates, and a consistency check between its human report and structured outputs.

## 4. Glossary-data rollout

```bash
.venv/bin/python scripts/run_demo.py fixture --scenario glossary-before
.venv/bin/python scripts/run_demo.py run
.venv/bin/python scripts/run_demo.py verify --scenario glossary-before

.venv/bin/python scripts/run_demo.py fixture --scenario glossary-after
.venv/bin/python scripts/run_demo.py run
.venv/bin/python scripts/run_demo.py verify --scenario glossary-after
```

Only PostgreSQL `domain_terms` changes. The target correction and knowledge disposition change, while `pipeline_sha256` remains equal.

For equivalent safe rollout, a script would need glossary snapshots, version hashing, cache invalidation, impact selection, before/after comparison, rollback, and manifest recording.

## 5. Real failure and local recomputation

```bash
.venv/bin/python scripts/run_demo.py fixture --scenario recovery-fault
.venv/bin/python scripts/run_demo.py run  # expected nonzero

.venv/bin/python scripts/run_demo.py fixture --scenario recovery-fixed
.venv/bin/python scripts/run_demo.py run --resume
.venv/bin/python scripts/run_demo.py verify --scenario recovery-fixed
```

The fault is corrupt MinIO PDF bytes. The fixed run must show only `SRC-CLINICAL` in `resume_recomputed_source_ids`.

Point to `StageStateStore`, `_cached_or_pending`, `fetch_and_validate_source`, and `publish_outputs`.

A script-only equivalent would need a stage-state database, idempotency keys, result locators, failure/quarantine semantics, version invalidation, changed-input anti-joins, cached-result revalidation, downstream merge, and an atomic multi-file commit.

Close by noting that the business improvements also depend on data, models, research methods, and analysts. Vane's role here is to make the actions repeatable, governed, source-bound, recoverable, and publishable in one Ray Relation workflow.
