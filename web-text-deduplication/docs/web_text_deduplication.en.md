# Web Text Deduplication

Web corpora accumulate exact copies, lightly edited revisions, mirrored pages,
and repeated templates. Comparing every pair gives quadratic work, while a
content hash only finds byte-identical records. This example uses Vane
Relations, deterministic MinHash fingerprints, global LSH candidate generation,
exact Jaccard verification, and graph clustering to produce an auditable
deduplicated release.

## Example data

The default `documents.csv` contains 24 repository-authored documents under
Apache-2.0. It uses reserved `.example` domains, and
`scripts/prepare_web_text_deduplication_fixture.py` generates the text in the
repository. `documents_snapshot.json` pins the CSV hash, row count, schema, and
expected pipeline results.

The file contains:

- six topics with a canonical document, an exact cross-domain mirror, and a
  one-token revision;
- six unrelated singleton documents;
- four reserved synthetic domains.

The fixture is small enough for fast CI but exercises exact duplicates, near
duplicates, cross-domain candidates, singleton clusters, representative
selection, and the global pair-space diagnostics.

An optional Common Crawl mode reads prepared WARC byte ranges into the same
pipeline. It is networked, opt-in, and keeps generated text under `workspace/`.

The input contract is:

~~~text
doc_id, source, domain, crawled_at, title, body
~~~

Vane reads it as a Relation:

~~~python
documents = conn.read_csv(str(DEFAULT_INPUT), header=True).project(
    "doc_id, source, domain, cast(crawled_at as date) as crawled_at, "
    "coalesce(title, '') as title, coalesce(body, '') as body"
)
~~~

`doc_id` must be non-null and unique. Custom CSV and Parquet inputs use the same
base columns; optional WARC lineage columns are preserved when present.

## 1. Normalize and fingerprint

The first Batch UDF normalizes `body`, creates 5-token shingles, calculates 64
deterministic BLAKE2b-64 MinHash values with seed 42, and divides the signature
into eight bands of eight rows:

~~~python
fingerprinted_rel = documents.map_batches(
    importable_fingerprint_documents_batch(),
    schema=FINGERPRINT_SCHEMA,
    batch_size=args.batch_size,
    **udf_options,
)
~~~

The normalization sequence is Unicode NFD, combining-mark removal, lowercase,
punctuation-to-space, and whitespace collapse. Empty text receives an empty
band list and never enters an LSH bucket. A document with fewer than five
tokens receives one ordered whole-document shingle, so `alpha beta` and
`beta alpha` remain different.

For eight bands with eight rows each, the theoretical candidate probability is
`1-(1-s^8)^8`. At the exact acceptance threshold `s=0.7`, that probability is
`0.378122`. This is a recall tradeoff under the MinHash independence
assumption, not a measured accuracy claim.

## 2. Expand bands and guard candidate growth

The fixed eight-band signature is expanded using SQL `UNION ALL` and
`list_extract(f.lsh_bands, ...)`:

~~~python
band_queries = [
    f"""
    select
      f.doc_id,
      d.domain,
      {band_index} as band_index,
      list_extract(f.lsh_bands, {band_index + 1}) as lsh_band
    from fingerprinted f
    join documents d using (doc_id)
    where f.shingle_count > 0
    """
    for band_index in range(8)
]
~~~

The pipeline verifies that every non-empty fingerprint produces exactly eight
membership rows. `collision_buckets.csv` exposes member counts, domains, and
the potential pair expansion for each colliding bucket.

Before the self-join, the script sums
`member_count * (member_count - 1) / 2` across buckets. If
`candidate_pair_slots` exceeds `--max-candidate-pair-slots`, execution fails
before creating the pair relation. The default budget is 1,000,000.

## 3. Generate global candidates

Candidates are joined globally rather than partitioned by domain, allowing the
pipeline to find mirrored content:

~~~sql
select
  l.doc_id as left_doc_id,
  r.doc_id as right_doc_id,
  l.domain as left_domain,
  r.domain as right_domain,
  count(*) as shared_bands
from band_memberships l
join band_memberships r
  on l.band_index = r.band_index
 and l.lsh_band = r.lsh_band
 and l.doc_id < r.doc_id
group by l.doc_id, r.doc_id, l.domain, r.domain
~~~

`l.doc_id < r.doc_id` removes self-pairs and symmetric duplicates. Grouping
merges pairs that collide in several bands.

`candidate_summary.csv` compares the unique candidates with the global
`n(n-1)/2` pair space and records `candidate_reduction_ratio`.
`domain_summary.csv` provides a per-domain diagnostic but does not replace the
global baseline.

## 4. Verify with exact Jaccard

The second Batch UDF computes:

- exact Jaccard over the full 5-token shingle sets, or the single ordered
  whole-document shingle used for shorter non-empty text;
- MinHash signature overlap as a diagnostic.

Only exact Jaccard at or above 0.7 creates a duplicate edge. A high approximate
score alone is recorded as `minhash_only_rejected`.

~~~python
scored_pairs_rel = candidate_pairs.map_batches(
    importable_score_pairs_batch(),
    schema=SCORED_PAIR_SCHEMA,
    batch_size=100,
    **udf_options,
)
~~~

This separates candidate recall from final acceptance and leaves an auditable
reason on every scored pair.

## 5. Build stable clusters

Duplicate edges form an undirected graph. A recursive CTE finds connected
components:

~~~sql
with recursive
edges as (
  select doc_id as src_doc_id, doc_id as dst_doc_id from documents
  union
  select left_doc_id, right_doc_id from duplicate_pairs
  union
  select right_doc_id, left_doc_id from duplicate_pairs
),
reach(src_doc_id, dst_doc_id) as (
  select src_doc_id, dst_doc_id from edges
  union
  select r.src_doc_id, e.dst_doc_id
  from reach r
  join edges e on e.src_doc_id = r.dst_doc_id
)
~~~

The smallest member `doc_id` becomes the stable cluster root. Representative
selection is separate: newer crawl date wins, then higher token count, then
`doc_id`. `cluster_inspection.csv` shows every multi-member cluster, its chosen
representative, members, and weakest accepted edge.

## Default results

The pinned fixture produces:

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

The 18 duplicate edges contain six exact pairs and twelve near-duplicate pairs.
The 24 input documents form six three-member duplicate clusters and six
singletons, leaving 12 representatives.

These numbers are a deterministic contract for code and CI. They are not a
precision, recall, throughput, or web-scale benchmark.

## Outputs

The Relation writers produce:

~~~python
duplicate_pairs.write_csv(str(output_dir / "duplicate_pairs.csv"))
cluster_inspection.write_csv(str(output_dir / "cluster_inspection.csv"))
collision_buckets.write_csv(str(output_dir / "collision_buckets.csv"))
domain_summary.write_csv(str(output_dir / "domain_summary.csv"))
fingerprinted.write_parquet(str(output_dir / "fingerprinted.parquet"))
representatives.write_parquet(str(output_dir / "deduped_documents.parquet"))
~~~

| File | Purpose |
| --- | --- |
| `source_blocks.parquet` | Normalized input relation used by this run |
| `fingerprinted.parquet` | Normalized text, shingles, MinHash, and bands |
| `collision_buckets.csv` | LSH hot-bucket and pair-slot diagnostics |
| `candidate_summary.csv` | Global pair space, budget, candidates, and reduction |
| `domain_summary.csv` | Per-domain candidate diagnostics |
| `scored_pairs.parquet` | Exact and approximate scores for every candidate |
| `duplicate_pairs.csv` | Accepted duplicate graph edges |
| `duplicate_summary.csv` | Exact/near and WARC relationship counts |
| `clusters.csv` | Stable cluster membership |
| `cluster_inspection.csv` | Multi-member cluster review view |
| `deduped_documents.parquet` | Selected representatives |
| `source_records.csv` | WARC records when a local Common Crawl source is used |
| `source_summary.csv` | Input/source totals |
| `manifest.json` | Source classification, snapshot_verification, algorithm, counts, and backends |

## Run

Install dependencies and run the offline fixture:

The requirements file resolves the validated Vane release from public PyPI; `pip install vane-ai` is the equivalent direct Vane installation command.

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
VANE_RUNNER=local-fast .venv/bin/python src/web_text_deduplication.py
~~~

Focused tests:

~~~bash
VANE_RUNNER=local-fast .venv/bin/python -m unittest -v \
  tests.test_web_text_deduplication \
  tests.test_common_crawl_source
~~~

Regenerate the repository-authored fixture:

~~~bash
.venv/bin/python scripts/prepare_web_text_deduplication_fixture.py
~~~

The fixture generator must reproduce the pinned hash and expected results.

## Optional Common Crawl run

This path is opt-in and networked. Review the
[Common Crawl Terms of Use](https://commoncrawl.org/terms-of-use) and the
selected source sites before running it.

The checked-in target list uses only IANA example domains. Generate the record
manifest and extracted snapshot locally:

~~~bash
VANE_RUNNER=local-fast .venv/bin/python scripts/prepare_web_text_deduplication_data.py \
  --refresh-index \
  --acknowledge-common-crawl-terms
~~~

Generated files live under `workspace/web_text_deduplication/`, which is
gitignored and has `redistribution_status=local_only_rights_review_required`.
They should not be committed.

Run the verified local snapshot:

~~~bash
VANE_RUNNER=local-fast .venv/bin/python src/web_text_deduplication.py \
  --input workspace/web_text_deduplication/common_crawl_blocks.parquet \
  --snapshot-metadata workspace/web_text_deduplication/common_crawl_snapshot.json \
  --output-dir output/web_text_deduplication_common_crawl
~~~

Or read the prepared WARC ranges live:

~~~bash
VANE_RUNNER=local-fast .venv/bin/python src/web_text_deduplication.py \
  --source common-crawl \
  --record-manifest workspace/web_text_deduplication/common_crawl_records.csv \
  --acknowledge-common-crawl-terms \
  --output-dir output/web_text_deduplication_live
~~~

The WARC path checks HTTP 206, requested byte length, and target URI, and
cross-checks the WARC payload-digest header against the pinned index digest. It
also checks the HTML type, maximum payload size, and extraction row limits. When
nested HTML elements both qualify as text blocks, the extractor prefers the
deepest block and retains only direct text from a qualifying ancestor. It
removes duplicate block text before applying the per-page row limit.

## Custom inputs

Supply any CSV or Parquet with the six base columns:

~~~bash
VANE_RUNNER=local-fast .venv/bin/python src/web_text_deduplication.py \
  --input path/to/documents.parquet \
  --output-dir output/custom_dedup
~~~

Custom input is marked `user_managed`. Add `--snapshot-metadata` to verify its
path, byte size, SHA-256, row count, and optional WARC record manifest. Passing
that verification does not reclassify custom data as `repository_fixture`;
that label is reserved for the checked-in default input and metadata pair.

## Current scope

- The offline fixture makes behavior reproducible; it is not a representative web distribution.
- LSH is probabilistic. Exact scoring removes false positives among candidates but cannot recover pairs that never share a band.
- Hot buckets and recursive graph closure can become expensive. The pair-slot guard bounds this example; it is not a distributed-scale benchmark.
- The HTML extractor removes parent-child overlap but does not evaluate page safety, privacy, robots policy, or content rights.
