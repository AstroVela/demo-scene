# Enterprise Multimodal Agent Evidence

Before an enterprise Agent uses a set of files as context, the pipeline needs to answer two separate questions: can the original payloads be read, and do the assertions from their sources agree? A missing modality, changed hash, parse failure, conflicting claim, or stale observation should stop the material from flowing directly into an Agent.

This example keeps both layers in one Vane data flow:

~~~text
pinned public files
→ document / text / image / audio Batch UDFs
→ typed asset_features
→ join evidence_links into evidence_features
→ requirement, conflict, and freshness SQL
→ agent_context + review_queue
~~~

## Scope

The default run reads and parses text documents, SVG images, and a WAV file. It preserves source pages, versions, licenses, content hashes, media metrics, and risk flags.

The flow ends with `agent_context` and `review_queue`; model execution and retrieval are outside this example.

## Inputs

data/enterprise_multimodal_agent/ contains three scenario tables and their audit manifest:

| File | Purpose |
| --- | --- |
| cases.csv | Four review questions and deterministic review dates |
| requirements.csv | Required evidence modalities for each case |
| evidence_links.csv | Case-to-asset links, observation dates, and claims |
| scenario_snapshot.json | Data classification, schemas, row counts, hashes, and expected demo results |

The default `--asset-catalog` is `data/enterprise_multimodal_agent/asset_catalog.csv`. An evidence link contains an asset identifier rather than copied payload text:

~~~text
record_id, case_id, asset_id, source_system, observed_at,
evidence_title, claim_key, claim_value
~~~

The executable uses Relation readers for both layers:

~~~python
from pathlib import Path

import vane

from src.enterprise_multimodal_agent import (
    DEFAULT_ASSET_CATALOG,
    DEFAULT_INPUT_DIR,
    materialize_sources,
)

conn = vane.connect()
cases, requirements, public_assets, evidence_links, modalities = (
    materialize_sources(
        conn,
        Path(DEFAULT_INPUT_DIR),
        Path(DEFAULT_ASSET_CATALOG),
    )
)
~~~

For the checked-in scenario, the executable first verifies scenario_snapshot.json. materialize_sources then calls .read_csv( and fails closed on empty required fields, invalid dates, unsupported requirement modalities, duplicate keys, incomplete claim pairs, cases without requirements, observations after their review date, unknown case references, unknown asset references, and empty source tables. Asset rows must also carry a source URI, license identifier, MIME type, and 64-character SHA-256.

After those checks, only catalog assets referenced by evidence_links enter the Batch UDFs. Payload bytes remain in files. SQL joins the parsed asset features to evidence_links afterward, so a file referenced by several cases is decoded only once and unrelated catalog rows are not processed.

## 1. Process each modality

Each branch receives the same asset-source columns but performs modality-specific work:

| Modality | Current processing | Main output or risk |
| --- | --- | --- |
| document | Decode UTF-8 and retain line structure | invalid_utf8, empty_document |
| text | Decode UTF-8 and normalize whitespace | invalid_utf8, text_too_short |
| image | Parse SVG XML and obtain dimensions or viewBox | invalid_image, missing_dimensions, low_resolution |
| audio | Read PCM WAV frames and a positive sample rate | invalid_audio, audio_too_short |

Every processor reads the real payload and checks expected_sha256 first. A hash mismatch fails closed instead of silently using changed evidence. Corrupt UTF-8 is retained as replacement-decoded review text but rejected with `invalid_utf8`; malformed WAV metadata, including a non-positive sample rate, becomes `invalid_audio`.

The script builds one Relation per present modality:

~~~python
stage_functions = {
    "document": "process_document_asset_batch",
    "image": "process_image_asset_batch",
    "audio": "process_audio_asset_batch",
    "text": "process_text_asset_batch",
}

relations = []
for modality in modalities:
    source = public_assets.filter(
        f"modality = '{modality}'"
    ).order("record_id")
    relations.append(
        source.map_batches(
            importable_batch_function(stage_functions[modality]),
            schema=ASSET_FEATURE_SCHEMA,
            batch_size=4,
        )
    )
~~~

The branches are unioned into five typed asset_features rows. SQL then joins those rows to eight evidence_links rows to produce the case-level evidence_features contract. evidence_features includes:

- record_id, case_id, and asset_id;
- source_uri, source_page_uri, and source_version;
- license_id and license_uri;
- evidence_text, content_sha256, byte_size, and token_count;
- a typed media_metrics struct;
- asset_decision, risk_flags, and blocking_risk_count.

The checked-in Generic File SVG produces 512×512 dimensions. The WAV produces a 48 kHz sample rate and 2.4-second duration. The Download SVG is actually 136×168, so it receives low_resolution and is rejected.

## 2. Detect missing modalities and conflicting claims

Requirements are left joined to the extracted evidence:

~~~sql
select
  r.case_id,
  c.account_id,
  r.evidence_type as missing_evidence_type,
  'missing_required_evidence' as reason
from case_requirements r
join business_cases c using (case_id)
left join evidence_features e
  on e.case_id = r.case_id
 and e.evidence_type = r.evidence_type
where e.record_id is null
~~~

Claims are grouped by case and key:

~~~sql
select
  case_id,
  claim_key,
  count(distinct claim_value) as distinct_values,
  string_agg(distinct claim_value, ', ' order by claim_value) as claim_values,
  string_agg(record_id, ', ' order by record_id) as evidence_ids
from evidence_features
where claim_key <> '' and claim_value <> ''
group by case_id, claim_key
having count(distinct claim_value) > 1
~~~

These Relations become evidence_gaps and evidence_conflicts. The default scenario has one missing audio requirement and one media_readiness conflict between ready and blocked.

## 3. Build Agent-ready context

The final SQL aggregates evidence, sources, modalities, rejected assets, gaps, conflicts, risks, and stale observations by case. Typed lists preserve evidence_ids, asset_ids, source_systems, modalities, and license_ids. context_text is ordered by observation date.

The policy is:

~~~sql
case
  when missing_evidence_count > 0
    or conflict_count > 0
    or blocking_risk_count > 0 then 'blocked'
  when stale_evidence_count > 0
    or risk_count > 0 then 'needs_review'
  else 'ready'
end
~~~

Default results:

| Case | Evidence | Modalities | Gaps | Conflicts | Rejected | Stale | State |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| case-arrow-docs | 2 | 2 | 0 | 0 | 0 | 0 | ready |
| case-wikimedia-media | 3 | 2 | 0 | 1 | 1 | 0 | blocked |
| case-incomplete-bundle | 2 | 2 | 1 | 0 | 0 | 0 | blocked |
| case-stale-docs | 1 | 1 | 0 | 0 | 0 | 1 | needs_review |

## Artifacts

Relation writers produce the feature, context, and review outputs:

~~~python
agent_context.write_parquet(str(OUTPUT_DIR / "agent_context.parquet"))
asset_features.write_parquet(str(OUTPUT_DIR / "asset_features.parquet"))
evidence_features.write_parquet(str(OUTPUT_DIR / "evidence_features.parquet"))
evidence_gaps.write_csv(str(OUTPUT_DIR / "evidence_gaps.csv"))
evidence_conflicts.write_csv(str(OUTPUT_DIR / "evidence_conflicts.csv"))
review_queue.write_csv(str(OUTPUT_DIR / "review_queue.csv"))
status_summary.write_csv(str(OUTPUT_DIR / "status_summary.csv"))
~~~

| File | Purpose |
| --- | --- |
| asset_features.parquet | Five unique parsed public assets; each payload is processed once |
| evidence_features.parquet | Eight typed evidence rows with media metrics, source, license, hash, and risks |
| agent_context.parquet | Four case-level Agent context rows |
| evidence_gaps.csv | Missing required modalities |
| evidence_conflicts.csv | Conflicting claims and evidence IDs |
| review_queue.csv | Three non-ready cases in handling order |
| status_summary.csv | Case and evidence totals by state |
| manifest.json | Schema versions, relative default paths, scenario and asset snapshot verification, input hashes, modalities, backends, counts, and outputs |

## Run and refresh

Run the offline example:

~~~bash
VANE_RUNNER=local-fast .venv/bin/python src/enterprise_multimodal_agent.py
~~~

Expected summary:

~~~text
Business cases: 4
Public assets: 5
Evidence records: 8
Missing requirements: 1
Conflicting claims: 1
Cases requiring review: 3
Output directory: output/enterprise_multimodal_agent
~~~

Refresh and verify the public asset snapshot:

~~~bash
.venv/bin/python scripts/prepare_enterprise_agent_assets.py --refresh
~~~

Use --input-dir for another scenario and --asset-catalog for another governed asset manifest. --execution-backend can pin subprocess_task or ray_task. The Relation SQL path requires the `local-fast` runner because its named tables live in the client connection.

The pinned default run fails before processing when a scenario CSV, asset catalog, source manifest, or payload no longer matches its snapshot. Its manifest uses repository-relative paths so results do not expose a developer workstation path. Custom input directories still receive semantic integrity checks, but are reported as custom_inputs and are not claimed as snapshot-verified.

Focused verification:

~~~bash
VANE_RUNNER=local-fast .venv/bin/python -m unittest -v tests.test_enterprise_multimodal_agent
~~~

## Example boundaries

- Cases, requirements, claims, and observation dates are fixed demo metadata rather than customer events. The 30-day freshness window, 512×512 image threshold, and three review states are example rules.
- The processors provide lightweight UTF-8, SVG, and PCM WAV handling. PDF/Office parsing, OCR, raster-image decoding, ASR, diarization, and model inference are outside this example.
- The pinned snapshot supports offline reproducibility; it does not demonstrate distributed storage, throughput, retries, or recovery.

## Data notes

- The five media files come from Apache Arrow and Wikimedia Commons. Their source versions, license metadata, and SHA-256 values are recorded in `data/enterprise_multimodal_agent/asset_sources.csv` and `asset_snapshot.json`.
- The four cases, eight requirements, and eight evidence links are fixed demo data. `scenario_snapshot.json` pins their schemas, row counts, hashes, and expected results.
