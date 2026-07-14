# Web Text Deduplication with Vane

Detect exact and near-duplicate text with deterministic MinHash fingerprints,
global LSH candidates, exact Jaccard verification, and stable graph clusters.
The pipeline writes candidate diagnostics, review tables, and a deduplicated
representative release.

## Vane Data Focus

- Read the offline fixture as a Relation, with an optional custom data source
  for pinned Common Crawl WARC byte ranges.
- Compute MinHash fingerprints and exact pair scores through typed
  `map_batches` stages.
- Use SQL for global LSH candidate joins, pair-growth diagnostics, recursive
  connected components, representative selection, and artifact writers.

## Example Data

The default fixture contains 24 repository-authored documents under Apache-2.0.
It uses reserved `.example` domains and contains six exact/near-duplicate groups
plus six singleton documents. Its schema, SHA-256, row count, license, data
classification, and expected results are pinned in
`data/web_text_deduplication/documents_snapshot.json`.

An optional Common Crawl workflow reads selected WARC byte ranges into the same
pipeline. It is networked and opt-in; extracted third-party text is generated
under the gitignored `workspace/` directory rather than committed to the
repository.

## Quick Start

Python 3.10 or newer is required:

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python examples/web_text_deduplication.py
~~~

Expected summary:

~~~text
Source WARC records: 0
Text blocks: 24
Global pair space: 276
Candidate pair slots: 80 / 1000000
LSH candidate pairs: 18
Candidate reduction: 93.48%
Duplicate pairs: 18
Clusters: 12
Output directory: output/web_text_deduplication
~~~

The 18 accepted edges contain six exact and twelve near-duplicate pairs. The
pipeline retains 12 representatives.

Run the focused tests:

~~~bash
.venv/bin/python -m unittest discover -s tests -p 'test_*crawl*.py' -v
.venv/bin/python -m unittest discover -s tests -p 'test_web_text_deduplication.py' -v
~~~

## Algorithm

The default data flow is:

~~~text
verified document fixture
→ normalization and 5-token shingles
→ 64-value MinHash signature
→ 8 × 8 LSH bands
→ guarded global candidate self-join
→ exact Jaccard acceptance
→ recursive connected components
→ stable representative selection
~~~

The self-join fails before expansion when collision buckets exceed
`--max-candidate-pair-slots`. MinHash overlap remains diagnostic; only exact
shingle Jaccard at or above 0.7 creates a duplicate edge.

Documents with fewer than five tokens use one ordered whole-document shingle.
This preserves token order instead of collapsing short text into an unordered
set of words.

## Outputs

The default run writes under `output/web_text_deduplication/`:

| File | Purpose |
| --- | --- |
| `source_blocks.parquet` | Normalized input relation |
| `fingerprinted.parquet` | Text, shingles, MinHash, and LSH bands |
| `collision_buckets.csv` | Hot-bucket and pair-expansion diagnostics |
| `candidate_summary.csv` | Global pair space, budget, and reduction |
| `domain_summary.csv` | Per-domain candidate diagnostics |
| `scored_pairs.parquet` | Exact and approximate candidate scores |
| `duplicate_pairs.csv` | Accepted duplicate graph edges |
| `clusters.csv` | Stable cluster membership |
| `cluster_inspection.csv` | Multi-member cluster review view |
| `deduped_documents.parquet` | Selected representatives |
| `manifest.json` | Source classification, snapshot checks, algorithm, counts, and backends |

## Optional Common Crawl Path

Review the [Common Crawl terms](https://commoncrawl.org/terms-of-use) and the
selected source sites before enabling this networked path. The acknowledgement
flag prevents accidental ingestion.

Generate a local manifest and snapshot from the checked-in IANA example-domain
targets:

~~~bash
.venv/bin/python scripts/prepare_web_text_deduplication_data.py \
  --refresh-index \
  --acknowledge-common-crawl-terms
~~~

Run the verified local snapshot:

~~~bash
.venv/bin/python examples/web_text_deduplication.py \
  --input workspace/web_text_deduplication/common_crawl_blocks.parquet \
  --snapshot-metadata workspace/web_text_deduplication/common_crawl_snapshot.json \
  --output-dir output/web_text_deduplication_common_crawl
~~~

The custom data source reads pinned WARC byte ranges, then a Batch UDF extracts
non-overlapping HTML text blocks before deduplication.

Or replay its WARC ranges live:

~~~bash
.venv/bin/python examples/web_text_deduplication.py \
  --source common-crawl \
  --record-manifest workspace/web_text_deduplication/common_crawl_records.csv \
  --acknowledge-common-crawl-terms \
  --output-dir output/web_text_deduplication_live
~~~

Generated files stay under `workspace/` and should not be committed. The
tutorial covers snapshot verification, custom-input labels, and extraction
details.

## Documentation

- [English tutorial](docs/web_text_deduplication.en.md)
- [中文教程](docs/web_text_deduplication.zh-CN.md)

The tutorials describe the Relation APIs, LSH recall tradeoff, pair-slot guard,
exact scoring, recursive clustering, source verification, and current scope.

## Repository Layout

~~~text
web-text-deduplication/
├── data/web_text_deduplication/  # safe fixture, metadata, and target list
├── docs/                         # English and Chinese tutorials
├── examples/                     # dedupe pipeline and WARC helpers
├── scripts/                      # fixture and opt-in crawl preparation
├── tests/                        # algorithm, failure, and source tests
├── README.md
└── requirements.txt
~~~
