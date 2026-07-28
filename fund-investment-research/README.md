# Auditable Multimodal Fund Research with Vane

[简体中文](README.zh-CN.md) | **English**

This Ray-only demo turns synthetic meeting audio, research PDFs, a chat screenshot, approved thesis conditions, and trusted source metadata into source-bound research facts, evidence-grounded impact hypotheses, deterministic workflow states, and focused analyst review tasks.

It is not an investment chatbot and does not produce buy/sell, target-price, or portfolio recommendations.

| Signal | Evidence story | SQL workflow state |
| --- | --- | --- |
| `SIG-CLINICAL` | 29% overall ORR and 43% Grade 3+ TRAE, while retaining a 62.5% n=8 subgroup counter-signal | `thesis_review_required` |
| `SIG-RUNWAY` | A trusted financial source reports 24 months of runway | `thesis_supported` |
| `SIG-REGULATORY` | The company says Q4 2026 remains on schedule; an expert says Q2 2027 | `manual_review` |
| `SIG-RUMOR` | A low-trust chat rumor has no original announcement | `insufficient_evidence` |

## Architecture

![Fund research data flow](docs/vane-fund-research-data-flow.png)

The demo accepts only the **Ray Runner**. ASR, OCR, glossary correction, and multimodal prompting execute through Ray. There is no Local Runner branch and no mock fallback.

```text
PostgreSQL identity/rules + MinIO WAV/PDF/PNG
  → source/hash/media/decode gate
  → Ray ASR Actor → raw transcript → Ray glossary Function
  → Ray OCR Actor → vane.ai.prompt(image_columns=["image_bytes"])
  → strict fact/impact contracts + trusted identity rebinding
  → deterministic SQL state
  → cross-reference validation + atomic snapshot
```

![Deterministic signal-state SQL DAG](docs/vane-fund-research-sql-dag.png)

The VLM extracts observations and proposes impact hypotheses. It cannot overwrite company identity, source role, trust tier, or final state. [`research_signals.sql`](src/fund_investment_research/sql/research_signals.sql) alone decides the workflow state.

## Visible business actions

The default run mechanically demonstrates:

1. raw ASR → domain correction → knowledge disposition, with ambiguous numbers preserved;
2. a clinical signal expanded into opposing overall evidence and supporting subgroup evidence;
3. typed source facts, approved theses, model hypotheses, uncertainty, and source-locatable review tasks;
4. a PostgreSQL glossary-data change that alters correction without Pipeline code changes.

`verify` checks these actions without inventing time-saved percentages or performance claims. Performance is explicitly out of scope.

## Run

The launcher requires CPython 3.11 and the image-capable local Vane build under `~/vane`, plus PostgreSQL, MinIO, OpenAI-compatible Whisper and Qwen2.5-VL services, `pdftoppm`, eSpeak/pyttsx3, and ffmpeg.

Follow the [runbook](docs/runbook.md), then run:

```bash
.venv/bin/python scripts/run_demo.py e2e
```

The launcher rejects a non-local Vane installation, mismatched Vane/DuckDB identities, an older AI API without `image_columns`, a non-Ray configuration, or the wrong Python version. Unavailable services, corrupt objects, invalid model JSON/semantics, SQL failures, and publication failures exit nonzero.

Successful snapshots are atomically published under `output/<scenario>/current` with ten inspectable artifacts, including timestamped transcripts, individual corrections, facts, impact edges, SQL states, focused tasks, stage status, a hash manifest, and an analyst-readable evidence report.

## Drills and implementation guide

- [Operational runbook](docs/runbook.md)
- [Demo walkthrough](docs/demo-walkthrough.md)
- [Chinese documentation](README.zh-CN.md)
- [Inspectable queries](queries.sql)
- [Synthetic fixture and runtime notices](NOTICE.md)

The walkthrough includes:

- `glossary-before → glossary-after`, changing only PostgreSQL `domain_terms`;
- `recovery-fault → recovery-fixed → run --resume`, proving that a corrected clinical PDF selects only the changed/failed source stages.

Core parsing and normalization remain ordinary testable Python in [`domain_logic.py`](src/fund_investment_research/domain_logic.py). [`vane_functions.py`](src/fund_investment_research/vane_functions.py) supplies thin Ray Function/Actor adapters, while [`pipeline.py`](src/fund_investment_research/pipeline.py) owns Ray-only orchestration.

No second serial-Python Pipeline is implemented. The walkthrough explains the stateful worker lifecycle, batching, contracts, idempotency, checkpoint selection, conflict handling, lineage, and atomic publication that a script-only implementation would need to add for equivalent behavior.

All companies, identifiers, documents, audio, and values are synthetic. Do not commit real research material, credentials, model weights, virtual environments, or generated outputs.
