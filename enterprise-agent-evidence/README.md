# Enterprise Agent Evidence Governance with Vane

[English](README.md) | [简体中文](README.zh-CN.md)

Build auditable multimodal context before evidence reaches an enterprise Agent.
The pipeline parses document, text, image, and audio assets, joins them to
business requirements, and uses SQL to find missing evidence, conflicting
claims, stale observations, and media risks. It writes governed Agent context
and an ordered review queue; model execution and retrieval are outside this
example.

## Vane Data Focus

- Read scenario tables and the asset catalog as Relations, then validate and
  join them with SQL.
- Parse each referenced public asset once through a typed `map_batches` branch
  before linking it to one or more cases.
- Build evidence gaps, claim conflicts, governed context, and the ordered
  review queue with Relation SQL, aggregations, and writers.

The default offline data combines five pinned public files with four synthetic
cases whose gap, conflict, freshness, and review paths are reproducible.

## Quick Start

Python 3.10 or newer is required. From this directory:

`requirements.txt` installs the validated `vane-ai==0.1.0a1` release from
public PyPI. Vane can also be installed directly with `pip install vane-ai`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
VANE_RUNNER=local-fast .venv/bin/python src/enterprise_multimodal_agent.py
```

Expected summary:

```text
Business cases: 4
Public assets: 5
Evidence records: 8
Missing requirements: 1
Conflicting claims: 1
Cases requiring review: 3
Output directory: output/enterprise_multimodal_agent
```

Run the focused tests with:

```bash
VANE_RUNNER=local-fast .venv/bin/python -m unittest discover -s tests \
  -p 'test_enterprise_multimodal_agent.py' -v
```

## Fail-Closed Checks

Before processing the default data, the executable verifies:

- every scenario CSV against its pinned columns, row count, and SHA-256;
- the public asset catalog, source manifest, and five payloads against the
  public snapshot;
- required fields, dates, modalities, primary keys, and foreign keys;
- claim-key/value pairing and observation dates relative to review dates;
- payload hashes and modality-specific UTF-8, SVG, and WAV parsing rules,
  including explicit rejection of invalid UTF-8 and non-positive WAV sample
  rates.

Only assets referenced by an evidence link enter the batch UDFs. Default paths
in `manifest.json` are repository-relative, so generated metadata does not leak
a developer workstation path.

## Outputs

The default run writes to `output/enterprise_multimodal_agent/`:

| File | Purpose |
| --- | --- |
| `asset_features.parquet` | Five unique parsed public assets |
| `evidence_features.parquet` | Eight typed case-to-asset evidence rows |
| `agent_context.parquet` | Four governed case contexts |
| `evidence_gaps.csv` | Missing required modalities |
| `evidence_conflicts.csv` | Conflicting claims and evidence IDs |
| `review_queue.csv` | Blocked and needs-review cases in handling order |
| `status_summary.csv` | Counts by review state |
| `manifest.json` | Versions, verified snapshots, input hashes, counts, and backends |

## Documentation

- [English tutorial](docs/enterprise_multimodal_agent.en.md)
- [中文教程](docs/enterprise_multimodal_agent.zh-CN.md)

The tutorials describe the schemas, Vane APIs, SQL policy, output contract,
custom-input behavior, and current example scope.

## Refreshing Public Assets

Re-download the fixed public sources and verify their expected hashes with:

```bash
.venv/bin/python scripts/prepare_enterprise_agent_assets.py --refresh
```

This is the only operation that requires network access. A changed upstream
payload fails verification instead of silently replacing the snapshot.

## Repository Layout

```text
enterprise-agent-evidence/
├── data/enterprise_multimodal_agent/  # pinned scenarios and public assets
├── docs/                              # English and Chinese tutorials
├── src/                               # governance pipeline and media helper
├── scripts/                           # public snapshot preparation
├── tests/                             # focused contract and failure tests
├── README.md
├── README.zh-CN.md
└── requirements.txt
```
