# Multimodal Training Data Release with Vane

[English](README.md) | [简体中文](README.zh-CN.md)

Build a typed release table from document, text, image, and audio assets with
Vane Relations and Python batch UDFs. Each modality runs in its own typed branch;
the branches are combined with `union` before Relation filters, aggregations,
and writers produce release and rejection artifacts.

The default run uses five pinned public files from Apache Arrow and Wikimedia
Commons. It verifies their catalog, source manifest, and payload hashes before
processing, then releases four records and rejects one low-resolution SVG. The
lightweight processors cover UTF-8 text, SVG dimensions, and PCM WAV metadata.

## Vane Data Focus

- Filter one input Relation into four modality branches and combine their
  type-compatible outputs with `union`.
- Run Python processors through `map_batches` with a shared Arrow schema and
  selectable `subprocess_task` or `ray_task` execution.
- Use Relation aggregations and writers to produce typed Parquet releases,
  reviewable CSV summaries, and a JSON execution manifest.

## Quick Start

Python 3.10 or newer is required. From this directory:

`requirements.txt` installs the validated `vane-ai==0.1.0a1` release from
public PyPI. Vane can also be installed directly with `pip install vane-ai`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
VANE_RUNNER=local-fast .venv/bin/python src/multimodal_training_data.py
```

The default run writes Parquet, CSV, and JSON artifacts under
`output/multimodal_training_data`. It should process five records, release four,
and reject one low-resolution validation image.

Run the focused test suite with:

```bash
VANE_RUNNER=local-fast .venv/bin/python -m unittest discover -s tests \
  -p 'test_multimodal_training_data.py' -v
```

## Public Data Snapshot

The checked-in snapshot contains:

- two Apache Arrow README files under Apache-2.0;
- two Wikimedia Commons SVG files under CC0-1.0;
- one Wikimedia Commons WAV file under CC0-1.0.

The authoritative per-asset lineage, versions, authors, license URLs, and
hashes are recorded in
`data/multimodal_training_data/public_sources.csv`. The repository-level Apache
2.0 license does not replace the per-asset metadata.

To re-download the public assets and verify that their bytes still match the
pinned hashes:

```bash
.venv/bin/python scripts/prepare_multimodal_training_data.py --refresh
```

Refreshing is the only step that requires network access. An upstream change
causes the script to fail instead of silently replacing the snapshot.

## Documentation

- [English tutorial](docs/multimodal_training_data.en.md)
- [中文教程](docs/multimodal_training_data.zh-CN.md)

The tutorials describe the Arrow schemas, modality processors, Vane APIs,
execution backends, output contract, parser scope, and source metadata.

## Repository Layout

```text
multimodal-training-data/
├── data/multimodal_training_data/   # manifests and pinned public assets
├── docs/                            # English and Chinese tutorials
├── src/                             # executable Vane pipeline
├── scripts/                         # public snapshot preparation
├── tests/                           # contract and end-to-end tests
├── README.md
├── README.zh-CN.md
└── requirements.txt
```
