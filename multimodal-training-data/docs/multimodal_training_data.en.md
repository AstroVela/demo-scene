# Multimodal Training Data Release

Before training assets enter a training job, they usually need decoding, quality checks, license checks, and a consistent format. Documents, images, audio, and text require different validation logic, but downstream consumers still need one stable contract that says which records can ship, which were rejected, and why.

Putting every modality inside one UDF couples processing logic to the output schema. This example uses four separate Arrow batch UDF branches and combines them with Relation `union`. The release gate consumes shared fields without needing to understand how each media processor produced them.

## Scope

The input is a prepared training-asset manifest. The example reads it as a Relation, runs one typed Batch UDF branch per modality, applies a shared release rule, and writes release, rejection, and modality-summary artifacts.

The default input contains five public files from Apache Arrow and Wikimedia Commons. Before opening the Relation, the executable verifies the catalog, source manifest, and file hashes against the checked-in snapshot. The current processors cover UTF-8/Markdown, SVG, and PCM WAV; OCR, ASR, semantic matching, embeddings, and model-generated labels are outside the example.

### Example data

The preparation script downloads the five files from public URIs and pins their bytes with expected SHA-256 values. Normal runs read the checked-in snapshot without network access while retaining the original URI, source page, version, and license metadata. `manifest.json` records the verification status and the catalog, source-manifest, and snapshot-metadata hashes.

The snapshot makes file handling and release decisions reproducible. Its five records are example inputs, not a representative training dataset.

```text
public_sources.csv
→ prepare_multimodal_training_data.py downloads and verifies
→ assets/ + training_assets.csv + public_snapshot.json
→ multimodal_training_data.py parses, gates, and summarizes
→ training_release + rejected_records + manifest
```

## Initialize

The following values match the complete script's defaults. An empty `UDF_OPTIONS` lets Vane choose the Batch UDF backend. To pin it, pass `{"execution_backend": "subprocess_task"}` or the corresponding Ray task configuration.

```python
from pathlib import Path

import vane

from src.multimodal_training_data import (
    SUPPORTED_MODALITIES,
    TRAINING_FEATURE_SCHEMA,
    importable_batch_function,
    project_raw_assets,
    validate_input_path,
    validate_modalities,
)

INPUT_PATH = Path("data/multimodal_training_data/training_assets.csv")
OUTPUT_DIR = Path("output/multimodal_training_data")
BATCH_SIZE = 2
UDF_OPTIONS = {}

conn = vane.connect()
```

## Input Data

The default manifest is `data/multimodal_training_data/training_assets.csv`. Each row describes one training asset:

| Column | Meaning |
| --- | --- |
| `record_id` | Stable asset identifier |
| `modality` | `document`, `image`, `audio`, or `text` |
| `source_uri` | Original asset location |
| `license_id` | License identifier required by the release policy |
| `split` | Dataset split |
| `mime_type` | Media type |
| `text` | Text body or media description |
| `content_path` | Repository path to a hash-verified public asset snapshot |
| `expected_sha256` | Expected SHA-256 of the downloaded source bytes |
| `content_base64` | Optional embedded payload for custom or synthetic fixtures |
| `metadata_json` | Modality-specific supplemental metadata |

The pipeline begins with a Relation reader and normalizes nullable strings to empty strings or empty JSON at the input boundary:

```python
input_path = validate_input_path(INPUT_PATH)
input_relation = conn.read_csv(str(input_path), header=True)
raw_assets_rel = project_raw_assets(input_relation)

raw_assets_rel.order("record_id").to_table("raw_assets")
raw_assets = conn.sql("select * from raw_assets")
modalities = validate_modalities(raw_assets)
```

`validate_modalities` collects only the distinct modality labels and rejects values outside `audio`, `document`, `image`, and `text`; it does not collect the full asset Relation into Python. The default manifest contains five real public records across all four modalities.

`data/multimodal_training_data/public_sources.csv` records each public URI, fixed source version, license page, and expected hash. Files live under `data/multimodal_training_data/assets/`, while `public_snapshot.json` audits the checked-in snapshot. Before processing, the executable verifies the catalog, source manifest, asset count, modalities, paths, byte sizes, and file hashes. Each processor checks its file hash again and preserves the original public `source_uri`.

| Record | Source | License | Purpose |
| --- | --- | --- | --- |
| `arrow-project-readme` | [Apache Arrow `apache-arrow-18.1.0` README](https://github.com/apache/arrow/blob/apache-arrow-18.1.0/README.md) | [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) | Document decoding and line counts |
| `arrow-python-readme` | [Apache Arrow Python README](https://github.com/apache/arrow/blob/apache-arrow-18.1.0/python/README.md) | [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) | Text normalization and token counts |
| `wikimedia-generic-file` | [Wikimedia Commons 512×512 SVG](https://commons.wikimedia.org/w/index.php?title=File:Generic_File.svg&oldid=1132223570) | [CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/) | Accepted image path |
| `wikimedia-download-icon` | [Wikimedia Commons 136×168 SVG](https://commons.wikimedia.org/w/index.php?title=File:Download.svg&oldid=1012581370) | [CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/) | Low-resolution rejection path |
| `wikimedia-audio` | [Wikimedia Commons 2.4-second WAV](https://commons.wikimedia.org/w/index.php?title=File:Audio.wav&oldid=1135746966) | [CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/) | WAV metric extraction |

Re-download and verify the public sources with:

```bash
.venv/bin/python scripts/prepare_multimodal_training_data.py --refresh
```

The preparation script caps each asset at 5 MiB and requires downloaded bytes to match the pinned SHA-256. Upstream drift fails the refresh instead of replacing the snapshot.

## 1. Process Assets by Modality

The four modalities share source columns but apply different checks:

| Modality | Default parser | Risk flags |
| --- | --- | --- |
| `document` | Decode the payload as UTF-8 text and count lines | Invalid UTF-8 or empty content produces `invalid_utf8` or `empty_document` |
| `image` | Parse SVG XML and read explicit dimensions or `viewBox` | Invalid format, missing dimensions, or dimensions below 512×512 |
| `audio` | Read sample rate and frame count from a WAV header | Invalid WAV, a non-positive sample rate, or duration below 0.005 seconds |
| `text` | Decode UTF-8, normalize whitespace, and count tokens | Invalid UTF-8 or fewer than four tokens |

### Read and Verify Payloads

Records prefer the file referenced by `content_path`. Custom manifests can still provide Base64, and only records with neither source use the UTF-8 bytes of `text`. When `expected_sha256` is present, the processor verifies the payload before parsing:

```python
def decode_payload(row):
    content_path = str(row.get("content_path") or "").strip()
    if content_path:
        path = Path(content_path)
        if not path.is_absolute():
            path = REPO_ROOT / path
        payload = path.read_bytes()
    else:
        encoded = str(row.get("content_base64") or "").strip()
        payload = (
            base64.b64decode(encoded, validate=True)
            if encoded
            else str(row.get("text") or "").encode("utf-8")
        )

    expected = str(row.get("expected_sha256") or "").strip()
    if expected and hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError("payload SHA-256 mismatch")
    return payload
```

Missing files, invalid Base64, and hash mismatches fail the batch because they indicate broken snapshot integrity. Parser and quality failures remain row-level risk flags.

### Apply Modality Rules

Each processor fills only the metrics relevant to its modality. These fragments show the key conditions in all four branches:

```python
def decode_utf8(payload, flags):
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        flags.append("invalid_utf8")
        return payload.decode("utf-8", errors="replace")

# document
text = decode_utf8(payload, flags).strip()
if not text:
    flags.append("empty_document")

# image: parse SVG XML and read dimensions
if mime_type == "image/svg+xml":
    metrics["width"], metrics["height"] = svg_dimensions(payload)
    if not metrics["width"] or not metrics["height"]:
        flags.append("missing_dimensions")
    elif metrics["width"] < 512 or metrics["height"] < 512:
        flags.append("low_resolution")
else:
    flags.append("invalid_image")

# audio
with wave.open(io.BytesIO(payload), "rb") as audio_file:
    sample_rate = audio_file.getframerate()
    if sample_rate <= 0:
        raise wave.Error("sample rate must be positive")
    duration = audio_file.getnframes() / float(sample_rate)
    if duration < 0.005:
        flags.append("audio_too_short")

# text
payload = decode_payload(row)
text = " ".join(decode_utf8(payload, flags).split())
if len(tokenize(text)) < 4:
    flags.append("text_too_short")
```

Invalid UTF-8 remains available as replacement-decoded review text, but `invalid_utf8` rejects the record. The audio function catches `EOFError` and `wave.Error`, including a non-positive sample rate, and emits `invalid_audio`. The image function validates the SVG root with the standard-library XML parser and supports integer or decimal pixel dimensions plus a `viewBox` fallback. PNG and JPEG decoding are outside the current scope.

### Apply the Shared Release Rule

All four processors finish with `build_feature_row`. It adds the content hash, byte and token counts, then applies the same license and quality policy:

```python
if not str(row.get("license_id") or "").strip():
    flags.append("missing_license")

quality_score = round(
    max(0.0, quality_score - 0.25 * ("missing_license" in flags)),
    3,
)
decision = (
    "accepted"
    if not flags and quality_score >= 0.8
    else "rejected"
)

result = {
    "record_id": row["record_id"],
    "modality": row["modality"],
    "content_sha256": hashlib.sha256(payload).hexdigest(),
    "byte_size": len(payload),
    "token_count": len(tokenize(content_text)),
    "quality_score": quality_score,
    "decision": decision,
    "risk_flags": flags,
    "media_metrics": metrics,
    "feature_json": json.dumps(features, sort_keys=True),
}
```

This is a strict gate: any risk flag rejects the record, and an unflagged record must still reach a quality score of `0.8`. A missing license also subtracts `0.25` from the score.

## 2. Combine Typed Results

The script selects one batch function per modality, filters its source Relation, and calls `map_batches`:

```python
stage_functions = {
    "document": "process_document_batch",
    "image": "process_image_batch",
    "audio": "process_audio_batch",
    "text": "process_text_batch",
}

relations = []
for modality in SUPPORTED_MODALITIES:
    source = raw_assets.filter(
        f"modality = '{modality}'"
    ).order("record_id")
    relations.append(
        source.map_batches(
            importable_batch_function(stage_functions[modality]),
            schema=TRAINING_FEATURE_SCHEMA,
            batch_size=BATCH_SIZE,
            **UDF_OPTIONS,
        )
    )

features_rel = relations[0]
for relation in relations[1:]:
    features_rel = features_rel.union(relation)

features_rel.order(
    "modality, record_id"
).to_table("feature_records")
```

Separate branches allow each processor to be tested or replaced independently. Every branch declares the same `TRAINING_FEATURE_SCHEMA`, so `union` produces one type-stable Relation.

Shared columns include source information, license, content, SHA-256, byte and token counts, quality score, decision, and risk flags. Two columns retain complex types:

- `risk_flags` is `VARCHAR[]`, allowing one record to retain several issues.
- `media_metrics` is `STRUCT(width, height, duration_seconds, sample_rate)`. Each modality fills only the applicable fields.

Image dimensions and audio sample rates therefore remain directly queryable from SQL. `feature_json` holds modality-specific details that have not stabilized as shared schema fields.

## 3. Build Release and Rejection Products

The processors have already stored the shared gate result in `decision`. Relation filters create two mutually exclusive products:

```python
feature_records = conn.sql("select * from feature_records")
training_release_rel = feature_records.filter(
    "decision = 'accepted'"
).order("split, modality, record_id")
rejected_records_rel = feature_records.filter(
    "decision = 'rejected'"
).order("quality_score, modality, record_id")

conn.sql("drop table if exists training_release")
training_release_rel.to_table("training_release")
training_release = conn.sql("select * from training_release")

conn.sql("drop table if exists rejected_records")
rejected_records_rel.to_table("rejected_records")
rejected_records = conn.sql("select * from rejected_records")
```

The modality summary remains a Relation aggregation:

```python
modality_summary_rel = feature_records.aggregate(
    """
    modality,
    count(*) as records,
    sum(byte_size) as total_bytes,
    round(avg(quality_score), 3) as avg_quality_score,
    sum(case when decision = 'accepted' then 1 else 0 end) as accepted,
    sum(case when decision = 'rejected' then 1 else 0 end) as rejected
    """
).order("modality")

conn.sql("drop table if exists modality_summary")
modality_summary_rel.to_table("modality_summary")
modality_summary = conn.sql("select * from modality_summary")
```

The default summary is:

| Modality | Input | Average quality | Released | Rejected |
| --- | ---: | ---: | ---: | ---: |
| `audio` | 1 | 1.000 | 1 | 0 |
| `document` | 1 | 1.000 | 1 | 0 |
| `image` | 2 | 0.750 | 1 | 1 |
| `text` | 1 | 1.000 | 1 | 0 |

The rejected record is:

| `record_id` | Quality | Risk flag | Reason |
| --- | ---: | --- | --- |
| `wikimedia-download-icon` | 0.500 | `low_resolution` | The real SVG is 136×168, below the 512×512 gate |

The five real public records produce four releases and one rejection. These values verify source lineage, parsing, and release flow; they are not a training-data quality benchmark.

## Artifacts

Relation writers produce every tabular artifact:

```python
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
feature_records.write_parquet(str(OUTPUT_DIR / "feature_records.parquet"))
training_release.write_parquet(str(OUTPUT_DIR / "training_release.parquet"))
rejected_records.write_csv(str(OUTPUT_DIR / "rejected_records.csv"))
modality_summary.write_csv(str(OUTPUT_DIR / "modality_summary.csv"))
```

| File | Purpose |
| --- | --- |
| `feature_records.parquet` | Complete typed features, risk flags, and decisions |
| `training_release.parquet` | Records ready for a training job |
| `rejected_records.csv` | Rejected records and specific risk reasons |
| `modality_summary.csv` | Per-modality counts, bytes, average quality, and decisions |
| `manifest.json` | Input, `source_mode`, snapshot verification status and hashes, schema version, release policy, row counts, backend, and artifact list |

Parquet preserves the `risk_flags` list and `media_metrics` struct. Rejected records and modality totals use CSV for direct review of gate behavior.

## Run the Complete Script

The executable script contains every Relation and Batch UDF stage shown above:

```bash
VANE_RUNNER=local-fast .venv/bin/python src/multimodal_training_data.py
```

The default run prints:

```text
Raw assets: 5
Released records: 4
Rejected records: 1
Output directory: output/multimodal_training_data
```

Run the preserved synthetic error fixture with:

```bash
VANE_RUNNER=local-fast .venv/bin/python src/multimodal_training_data.py \
  --input data/multimodal_training_data/synthetic_training_assets.csv
```

Use `--input` to replace the manifest, `--batch-size` to control input batches for each modality UDF, `--execution-backend` to select `subprocess_task` or `ray_task`, and `--output-dir` to change the artifact directory. The script requires the `local-fast` relation runner because its named tables live in the client connection; task backends affect only the four Python UDF branches.

The three commands have different purposes:

| Command | Network | Purpose |
| --- | --- | --- |
| `VANE_RUNNER=local-fast .venv/bin/python src/multimodal_training_data.py` | No | Process and release the default public snapshot |
| `.venv/bin/python scripts/prepare_multimodal_training_data.py --refresh` | Yes | Re-download sources and verify hashes |
| `VANE_RUNNER=local-fast .venv/bin/python src/multimodal_training_data.py --input .../synthetic_training_assets.csv` | No | Regress invalid-image and missing-license paths |

Run the focused checks with:

```bash
VANE_RUNNER=local-fast .venv/bin/python -m unittest -v tests.test_multimodal_training_data
```

## Current scope

- Document and text branches decode UTF-8; they do not parse PDF or Office files or run OCR.
- The image branch parses SVG dimensions, and the audio branch reads PCM WAV metadata. Raster-image decoding, ASR, and broader media-quality checks are not included.
- Missing files, invalid Base64, and SHA-256 mismatches fail a batch. Invalid UTF-8 and malformed media become structured risk flags.
- The `0.8` quality threshold and 512×512 image threshold are deterministic example rules rather than calibrated training-quality metrics.
