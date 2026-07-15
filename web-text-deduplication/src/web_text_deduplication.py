#!/usr/bin/env python3
"""Detect near-duplicate web text with MinHash-style fingerprints."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import pyarrow as pa
import vane

SOURCE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SOURCE_DIR.parent
for import_path in (REPO_ROOT, SOURCE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))
existing_pythonpath = os.environ.get("PYTHONPATH")
pythonpath_entries = [str(REPO_ROOT), str(SOURCE_DIR)]
if existing_pythonpath:
    paths = existing_pythonpath.split(os.pathsep)
    missing_paths = [path for path in pythonpath_entries if path not in paths]
    if missing_paths:
        os.environ["PYTHONPATH"] = os.pathsep.join(missing_paths + paths)
else:
    os.environ["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

from _common import (
    PUBLIC_BACKEND_CHOICES,
    backend_metadata_entry,
    batch_udf_options,
    merge_backend_metadata,
    positive_int,
    require_local_relation_runner,
    table_from_rows,
    write_json,
)
from _common_crawl import (
    BLOCK_SCHEMA,
    ExtractHtmlBlocksBatch,
    common_crawl_range_relation,
    load_record_manifest,
)


DATA_DIR = REPO_ROOT / "data" / "web_text_deduplication"
WORKSPACE_DIR = REPO_ROOT / "workspace" / "web_text_deduplication"
DEFAULT_INPUT = DATA_DIR / "documents.csv"
DEFAULT_RECORD_MANIFEST = (
    WORKSPACE_DIR / "common_crawl_records.csv"
)
DEFAULT_SNAPSHOT_METADATA = DATA_DIR / "documents_snapshot.json"
DEFAULT_OUTPUT_DIR = Path("output/web_text_deduplication")
MODULE_NAME = "src.web_text_deduplication"
COMMON_CRAWL_TERMS_URL = "https://commoncrawl.org/terms-of-use"
SHINGLE_SIZE = 5
MINHASH_VALUES = 64
MINHASH_SEED = 42
LSH_ROWS_PER_BAND = 8
LSH_BANDS = MINHASH_VALUES // LSH_ROWS_PER_BAND
SHINGLE_JACCARD_THRESHOLD = 0.7
SIGNATURE_OVERLAP_THRESHOLD = 0.7
PAIR_SCORE_BATCH_SIZE = 100
DEFAULT_MAX_CANDIDATE_PAIR_SLOTS = 1_000_000
FINGERPRINT_SCHEMA = {
    "doc_id": "VARCHAR",
    "normalized_text": "VARCHAR",
    "token_count": "BIGINT",
    "unique_tokens": "BIGINT",
    "token_set": "VARCHAR[]",
    "shingle_count": "BIGINT",
    "shingle_set": "VARCHAR[]",
    "signature": "UBIGINT[]",
    "lsh_bands": "VARCHAR[]",
}
SCORED_PAIR_SCHEMA = {
    "left_doc_id": "VARCHAR",
    "right_doc_id": "VARCHAR",
    "left_domain": "VARCHAR",
    "right_domain": "VARCHAR",
    "shared_bands": "BIGINT",
    "shingle_jaccard": "DOUBLE",
    "signature_overlap": "DOUBLE",
    "is_duplicate": "BOOLEAN",
    "reason": "VARCHAR",
}


def validate_input_path(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"web-text input file does not exist: {path}")
    return path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def metadata_resource_path(value: str, *, metadata_path: Path) -> Path:
    resource = Path(value)
    if resource.is_absolute():
        return resource
    if resource.parts and resource.parts[0] in {"data", "workspace"}:
        return REPO_ROOT / resource
    return metadata_path.parent / resource


def validate_snapshot_integrity(
    input_path: Path,
    metadata_path: Path,
) -> dict[str, Any]:
    snapshot = validate_input_path(input_path).resolve()
    metadata_file = validate_input_path(metadata_path).resolve()
    try:
        metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"snapshot metadata is not valid JSON: {metadata_file}"
        ) from exc
    if not isinstance(metadata, dict):
        raise ValueError("snapshot metadata must be a JSON object")

    required_fields = {
        "snapshot",
        "snapshot_sha256",
        "snapshot_bytes",
        "block_rows",
    }
    missing = sorted(required_fields - metadata.keys())
    if missing:
        raise ValueError(
            "snapshot metadata is missing required fields: " + ", ".join(missing)
        )

    declared_snapshot = metadata_resource_path(
        str(metadata["snapshot"]), metadata_path=metadata_file
    ).resolve()
    if declared_snapshot != snapshot:
        raise ValueError(
            "snapshot metadata describes a different input file: "
            f"{declared_snapshot} != {snapshot}"
        )

    expected_bytes = int(metadata["snapshot_bytes"])
    actual_bytes = snapshot.stat().st_size
    if actual_bytes != expected_bytes:
        raise ValueError(
            "snapshot byte-size mismatch: "
            f"expected {expected_bytes}, got {actual_bytes} for {snapshot}"
        )

    expected_sha256 = str(metadata["snapshot_sha256"]).lower()
    actual_sha256 = file_sha256(snapshot)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            "snapshot SHA-256 mismatch: "
            f"expected {expected_sha256}, got {actual_sha256} for {snapshot}"
        )

    verification: dict[str, Any] = {
        "status": "verified",
        "metadata": display_path(metadata_file),
        "metadata_sha256": file_sha256(metadata_file),
        "snapshot_sha256": actual_sha256,
        "snapshot_bytes": actual_bytes,
        "expected_block_rows": int(metadata["block_rows"]),
        "data_classification": metadata.get(
            "data_classification", "unspecified"
        ),
        "license_id": metadata.get("license_id", ""),
        "license_uri": metadata.get("license_uri", ""),
        "third_party_crawled_content": bool(
            metadata.get(
                "third_party_crawled_content",
                metadata.get("data_classification")
                == "third_party_crawled_content",
            )
        ),
        "expected_results": metadata.get("expected_results", {}),
    }
    record_fields = {
        "record_manifest",
        "record_manifest_sha256",
        "source_record_rows",
    }
    present_record_fields = record_fields & metadata.keys()
    if present_record_fields:
        missing_record_fields = sorted(record_fields - metadata.keys())
        if missing_record_fields:
            raise ValueError(
                "snapshot metadata must provide all source-record fields: "
                + ", ".join(missing_record_fields)
            )
        record_manifest = metadata_resource_path(
            str(metadata["record_manifest"]), metadata_path=metadata_file
        ).resolve()
        validate_input_path(record_manifest)
        expected_record_sha256 = str(metadata["record_manifest_sha256"]).lower()
        actual_record_sha256 = file_sha256(record_manifest)
        if actual_record_sha256 != expected_record_sha256:
            raise ValueError(
                "record manifest SHA-256 mismatch: "
                f"expected {expected_record_sha256}, got {actual_record_sha256} "
                f"for {record_manifest}"
            )
        verification.update(
            {
                "record_manifest": display_path(record_manifest),
                "record_manifest_sha256": actual_record_sha256,
                "expected_source_record_rows": int(
                    metadata["source_record_rows"]
                ),
            }
        )
    return verification


def snapshot_metadata_path_for_run(args: argparse.Namespace) -> Path | None:
    if args.source != "file":
        return None
    if args.snapshot_metadata:
        return Path(args.snapshot_metadata)
    if Path(args.input).resolve() == DEFAULT_INPUT.resolve():
        return DEFAULT_SNAPSHOT_METADATA
    return None


DOCUMENT_BASE_COLUMNS = {
    "doc_id",
    "source",
    "domain",
    "crawled_at",
    "title",
    "body",
}
DOCUMENT_PROVENANCE_COLUMNS = {
    "block_index": "cast(0 as bigint)",
    "block_chars": "cast(length(coalesce(body, '')) as bigint)",
    "crawl_id": "''",
    "target_url": "''",
    "capture_url": "''",
    "capture_timestamp": "''",
    "warc_record_id": "''",
    "warc_date": "''",
    "warc_filename": "''",
    "warc_offset": "cast(null as bigint)",
    "warc_length": "cast(null as bigint)",
    "warc_digest": "''",
    "http_content_type": "''",
}


def input_file_relation(conn: Any, path: Path) -> Any:
    validated = validate_input_path(path)
    if validated.suffix.lower() == ".parquet":
        source = conn.read_parquet(str(validated))
    else:
        source = conn.read_csv(str(validated), header=True)

    columns = set(source.columns)
    missing = sorted(DOCUMENT_BASE_COLUMNS - columns)
    if missing:
        raise ValueError(
            "web-text input is missing required columns: " + ", ".join(missing)
        )
    provenance = [
        (
            f"cast({name} as bigint) as {name}"
            if name in columns
            and name
            in {"block_index", "block_chars", "warc_offset", "warc_length"}
            else f"cast({name} as varchar) as {name}"
            if name in columns
            else f"{default_expression} as {name}"
        )
        for name, default_expression in DOCUMENT_PROVENANCE_COLUMNS.items()
    ]
    return source.project(
        "doc_id, source, domain, cast(crawled_at as date) as crawled_at, "
        "coalesce(title, '') as title, coalesce(body, '') as body, "
        + ", ".join(provenance)
    )


def load_source_documents(
    conn: Any,
    args: argparse.Namespace,
    udf_options: dict[str, str],
) -> tuple[Any, dict[str, dict[str, Any]]]:
    if args.source == "file":
        return input_file_relation(conn, Path(args.input)), {}

    specs = load_record_manifest(Path(args.record_manifest))
    raw_records_rel = common_crawl_range_relation(
        conn,
        specs,
        timeout=args.source_timeout,
        max_html_bytes=args.max_html_bytes,
    )
    conn.sql("drop table if exists raw_warc_records")
    raw_records_rel.order("capture_timestamp, index_url").to_table(
        "raw_warc_records"
    )
    raw_records = conn.sql("select * from raw_warc_records")
    blocks_rel = raw_records.map_batches(
        ExtractHtmlBlocksBatch(
            min_block_chars=args.min_block_chars,
            max_blocks_per_page=args.max_blocks_per_page,
        ),
        schema=BLOCK_SCHEMA,
        batch_size=args.source_batch_size,
        **udf_options,
    )
    return blocks_rel.project(
        "doc_id, source, domain, cast(crawled_at as date) as crawled_at, "
        "title, body, block_index, block_chars, crawl_id, target_url, "
        "capture_url, capture_timestamp, warc_record_id, warc_date, warc_filename, "
        "warc_offset, warc_length, warc_digest, http_content_type"
    ), {
        "extract_html_blocks": backend_metadata_entry(args.execution_backend)
    }


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    text = text.replace("_", " ")
    return " ".join(text.split())


def token_shingles(tokens: list[str], *, size: int = SHINGLE_SIZE) -> set[str]:
    if not tokens:
        return set()
    if len(tokens) < size:
        return {" ".join(tokens)}
    return {" ".join(tokens[idx : idx + size]) for idx in range(len(tokens) - size + 1)}


def shingles(text: str, *, size: int = SHINGLE_SIZE) -> set[str]:
    return token_shingles(normalize_text(text).split(), size=size)


def hash_int(value: str, *, seed: int) -> int:
    digest = hashlib.blake2b(f"{seed}:{value}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "little")


def minhash_signature(
    values: set[str],
    *,
    hashes: int = MINHASH_VALUES,
    seed: int = MINHASH_SEED,
) -> list[int]:
    if not values:
        return [0] * hashes
    return [
        min(hash_int(value, seed=seed + hash_index) for value in values)
        for hash_index in range(hashes)
    ]


def lsh_band_keys(
    signature: list[int], *, rows_per_band: int = LSH_ROWS_PER_BAND
) -> list[str]:
    if rows_per_band <= 0 or len(signature) % rows_per_band != 0:
        raise ValueError("signature length must be divisible by rows_per_band")

    keys: list[str] = []
    for offset in range(0, len(signature), rows_per_band):
        band_index = offset // rows_per_band
        band_values = signature[offset : offset + rows_per_band]
        payload = f"{band_index}:" + ",".join(str(value) for value in band_values)
        digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()
        keys.append(f"{band_index:03d}:{digest}")
    return keys


def lsh_candidate_probability(
    similarity: float,
    *,
    bands: int = LSH_BANDS,
    rows_per_band: int = LSH_ROWS_PER_BAND,
) -> float:
    if not 0.0 <= similarity <= 1.0:
        raise ValueError("similarity must be between 0 and 1")
    if bands <= 0 or rows_per_band <= 0:
        raise ValueError("bands and rows_per_band must be positive")
    return 1.0 - (1.0 - similarity**rows_per_band) ** bands


def fingerprint_documents_batch(batch: pa.Table) -> pa.Table:
    rows: list[dict[str, Any]] = []
    for row in batch.to_pylist():
        text = normalize_text(row["body"] or "")
        tokens = text.split()
        token_values = sorted(set(tokens))
        shingle_values = token_shingles(tokens)
        signature = minhash_signature(shingle_values)
        rows.append(
            {
                "doc_id": row["doc_id"],
                "normalized_text": text,
                "token_count": len(tokens),
                "unique_tokens": len(token_values),
                "token_set": token_values,
                "shingle_count": len(shingle_values),
                "shingle_set": sorted(shingle_values),
                "signature": signature,
                "lsh_bands": lsh_band_keys(signature) if shingle_values else [],
            }
        )
    return table_from_rows(
        rows,
        {
            "doc_id": pa.string(),
            "normalized_text": pa.string(),
            "token_count": pa.int64(),
            "unique_tokens": pa.int64(),
            "token_set": pa.list_(pa.string()),
            "shingle_count": pa.int64(),
            "shingle_set": pa.list_(pa.string()),
            "signature": pa.list_(pa.uint64()),
            "lsh_bands": pa.list_(pa.string()),
        },
    )


def importable_fingerprint_documents_batch() -> Any:
    if __name__ != "__main__":
        return fingerprint_documents_batch

    import importlib

    module = importlib.import_module(MODULE_NAME)
    return module.fingerprint_documents_batch


def band_membership_relation_sql(conn: Any) -> Any:
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
        for band_index in range(LSH_BANDS)
    ]
    return conn.sql(" union all ".join(band_queries))


def jaccard(left: list[str], right: list[str]) -> float:
    left_tokens = set(left or [])
    right_tokens = set(right or [])
    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def signature_overlap(left: list[int], right: list[int]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(1 for a, b in zip(left, right) if a == b) / len(left)


def score_pairs_batch(batch: pa.Table) -> pa.Table:
    rows: list[dict[str, Any]] = []
    for row in batch.to_pylist():
        exact_score = jaccard(
            row["left_shingle_set"], row["right_shingle_set"]
        )
        minhash_score = signature_overlap(row["left_signature"], row["right_signature"])
        exact_match = exact_score >= SHINGLE_JACCARD_THRESHOLD
        signature_match = minhash_score >= SIGNATURE_OVERLAP_THRESHOLD
        is_duplicate = exact_match
        if exact_match and signature_match:
            reason = "jaccard_and_minhash"
        elif exact_match:
            reason = "jaccard_match"
        elif signature_match:
            reason = "minhash_only_rejected"
        else:
            reason = "below_jaccard_threshold"
        rows.append(
            {
                "left_doc_id": row["left_doc_id"],
                "right_doc_id": row["right_doc_id"],
                "left_domain": row["left_domain"],
                "right_domain": row["right_domain"],
                "shared_bands": int(row["shared_bands"]),
                "shingle_jaccard": exact_score,
                "signature_overlap": minhash_score,
                "is_duplicate": is_duplicate,
                "reason": reason,
            }
        )
    return table_from_rows(
        rows,
        {
            "left_doc_id": pa.string(),
            "right_doc_id": pa.string(),
            "left_domain": pa.string(),
            "right_domain": pa.string(),
            "shared_bands": pa.int64(),
            "shingle_jaccard": pa.float64(),
            "signature_overlap": pa.float64(),
            "is_duplicate": pa.bool_(),
            "reason": pa.string(),
        },
    )


def importable_score_pairs_batch() -> Any:
    if __name__ != "__main__":
        return score_pairs_batch

    import importlib

    module = importlib.import_module(MODULE_NAME)
    return module.score_pairs_batch


def relation_row_count(rel: Any) -> int:
    row = rel.aggregate("count(*) as row_count").fetchone()
    return int(row[0])


def validate_candidate_pair_budget(
    collision_buckets: Any,
    *,
    max_candidate_pair_slots: int,
) -> int:
    candidate_pair_slots, largest_bucket_size, largest_bucket_pair_slots = (
        collision_buckets.aggregate(
            "coalesce(sum(pair_slots), 0) as candidate_pair_slots, "
            "coalesce(max(member_count), 0) as largest_bucket_size, "
            "coalesce(max(pair_slots), 0) as largest_bucket_pair_slots"
        ).fetchone()
    )
    candidate_pair_slots = int(candidate_pair_slots)
    if candidate_pair_slots > max_candidate_pair_slots:
        raise RuntimeError(
            "LSH candidate expansion would create "
            f"{candidate_pair_slots} band-level pair slots, exceeding "
            f"--max-candidate-pair-slots={max_candidate_pair_slots}. "
            f"The largest bucket has {int(largest_bucket_size)} members and "
            f"{int(largest_bucket_pair_slots)} pair slots. Review the collision "
            "buckets or raise the budget explicitly."
        )
    return candidate_pair_slots


def validate_document_ids(documents: Any) -> None:
    total_rows, non_null_ids, distinct_ids = documents.aggregate(
        """
        count(*) as total_rows,
        count(doc_id) as non_null_ids,
        count(distinct doc_id) as distinct_ids
        """
    ).fetchone()
    if total_rows != non_null_ids:
        raise ValueError("documents.doc_id must not contain null values")
    if total_rows != distinct_ids:
        raise ValueError("documents.doc_id must be unique")


def cluster_relation_sql(conn: Any) -> Any:
    return conn.sql(
        """
        with recursive
        edges as (
          select doc_id as src_doc_id, doc_id as dst_doc_id from documents
          union
          select left_doc_id as src_doc_id, right_doc_id as dst_doc_id from duplicate_pairs
          union
          select right_doc_id as src_doc_id, left_doc_id as dst_doc_id from duplicate_pairs
        ),
        reach(src_doc_id, dst_doc_id) as (
          select src_doc_id, dst_doc_id from edges
          union
          select r.src_doc_id, e.dst_doc_id
          from reach r
          join edges e on e.src_doc_id = r.dst_doc_id
        ),
        components as (
          select src_doc_id as doc_id, min(dst_doc_id) as root_doc_id
          from reach
          group by src_doc_id
        ),
        cluster_sizes as (
          select root_doc_id, count(*) as cluster_size
          from components
          group by root_doc_id
        )
        select
          'cluster-' || c.root_doc_id as cluster_id,
          c.doc_id,
          cs.cluster_size
        from components c
        join cluster_sizes cs using (root_doc_id)
        order by cluster_id, c.doc_id
        """
    )


def write_artifacts(
    *,
    output_dir: Path,
    documents: Any,
    fingerprinted: Any,
    band_memberships: Any,
    collision_buckets: Any,
    candidate_pairs: Any,
    scored_pairs: Any,
    duplicate_pairs: Any,
    duplicate_summary: Any,
    clusters: Any,
    representatives: Any,
    cluster_inspection: Any,
    domain_summary: Any,
    candidate_summary: Any,
    source_records: Any,
    source_summary: Any,
    snapshot_verification: dict[str, Any],
    backend_metadata: dict[str, dict[str, Any]],
    runner: str,
    args: argparse.Namespace,
) -> dict[str, int | float]:
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_metadata_path = snapshot_metadata_path_for_run(args)
    repository_fixture = (
        args.source == "file"
        and snapshot_verification.get("status") == "verified"
        and Path(args.input).resolve() == DEFAULT_INPUT.resolve()
        and snapshot_metadata_path is not None
        and snapshot_metadata_path.resolve() == DEFAULT_SNAPSHOT_METADATA.resolve()
    )
    third_party_content = args.source == "common-crawl" or bool(
        snapshot_verification.get("third_party_crawled_content", False)
    )
    redistribution_status = (
        "local_only_rights_review_required"
        if third_party_content
        else "repository_fixture"
        if repository_fixture
        else "user_managed"
    )
    counts = {
        "document_rows": relation_row_count(documents),
        "fingerprinted_rows": relation_row_count(fingerprinted),
        "band_membership_rows": relation_row_count(band_memberships),
        "collision_bucket_rows": relation_row_count(collision_buckets),
        "candidate_pair_rows": relation_row_count(candidate_pairs),
        "scored_pair_rows": relation_row_count(scored_pairs),
        "duplicate_pair_rows": relation_row_count(duplicate_pairs),
        "cluster_rows": relation_row_count(clusters),
        "representative_rows": relation_row_count(representatives),
        "inspection_rows": relation_row_count(cluster_inspection),
        "domain_summary_rows": relation_row_count(domain_summary),
        "source_record_rows": relation_row_count(source_records),
    }
    candidate_metrics = candidate_summary.fetchone()
    counts["possible_pair_rows"] = int(candidate_metrics[1])
    counts["candidate_reduction_ratio"] = float(candidate_metrics[4])
    counts["same_domain_candidate_pair_rows"] = int(candidate_metrics[5])
    counts["cross_domain_candidate_pair_rows"] = int(candidate_metrics[6])
    counts["same_domain_duplicate_pair_rows"] = int(candidate_metrics[7])
    counts["cross_domain_duplicate_pair_rows"] = int(candidate_metrics[8])
    counts["candidate_pair_slots"] = int(candidate_metrics[9])
    source_metrics = source_summary.fetchone()
    counts["source_url_rows"] = int(source_metrics[2])
    counts["domain_rows"] = int(source_metrics[3])
    counts["source_target_rows"] = int(source_metrics[4])
    duplicate_metrics = duplicate_summary.fetchone()
    counts["exact_duplicate_pair_rows"] = int(duplicate_metrics[1])
    counts["near_duplicate_pair_rows"] = int(duplicate_metrics[2])
    counts["within_warc_duplicate_pair_rows"] = int(duplicate_metrics[3])
    counts["cross_warc_duplicate_pair_rows"] = int(duplicate_metrics[4])
    counts["cross_url_duplicate_pair_rows"] = int(duplicate_metrics[5])
    counts["max_collision_bucket_size"] = int(
        collision_buckets.aggregate(
            "coalesce(max(member_count), 0) as max_collision_bucket_size"
        ).fetchone()[0]
    )
    counts["cluster_count"] = counts["representative_rows"]
    cluster_metrics = representatives.aggregate(
        "coalesce(max(cluster_size), 0), "
        "count(*) filter (where cluster_size = 1), "
        "count(*) filter (where cluster_size > 1)"
    ).fetchone()
    counts["max_cluster_size"] = int(cluster_metrics[0])
    counts["singleton_cluster_rows"] = int(cluster_metrics[1])
    counts["duplicate_cluster_rows"] = int(cluster_metrics[2])
    counts["removed_document_rows"] = (
        counts["document_rows"] - counts["representative_rows"]
    )
    counts["retained_ratio"] = (
        round(counts["representative_rows"] / counts["document_rows"], 4)
        if counts["document_rows"]
        else 0.0
    )

    expected_results = snapshot_verification.get("expected_results", {})
    result_mismatches = [
        f"{name}: expected {expected!r}, got {counts.get(name)!r}"
        for name, expected in expected_results.items()
        if counts.get(name) != expected
    ]
    if result_mismatches:
        raise RuntimeError(
            "fixture result contract mismatch: " + "; ".join(result_mismatches)
        )

    duplicate_pairs.write_csv(str(output_dir / "duplicate_pairs.csv"))
    duplicate_summary.write_csv(str(output_dir / "duplicate_summary.csv"))
    clusters.write_csv(str(output_dir / "clusters.csv"))
    cluster_inspection.write_csv(str(output_dir / "cluster_inspection.csv"))
    collision_buckets.write_csv(str(output_dir / "collision_buckets.csv"))
    domain_summary.write_csv(str(output_dir / "domain_summary.csv"))
    candidate_summary.write_csv(str(output_dir / "candidate_summary.csv"))
    source_records.write_csv(str(output_dir / "source_records.csv"))
    source_summary.write_csv(str(output_dir / "source_summary.csv"))
    documents.write_parquet(str(output_dir / "source_blocks.parquet"))
    fingerprinted.write_parquet(str(output_dir / "fingerprinted.parquet"))
    scored_pairs.write_parquet(str(output_dir / "scored_pairs.parquet"))
    representatives.write_parquet(str(output_dir / "deduped_documents.parquet"))
    write_json(
        output_dir / "manifest.json",
        {
            "example": "web_text_deduplication",
            "vane_version": vane.__version__,
            "runner": runner,
            "input": display_path(
                Path(args.input)
                if args.source == "file"
                else Path(args.record_manifest)
            ),
            "source": {
                "mode": args.source,
                "grain": (
                    "html_text_block"
                    if args.source == "common-crawl"
                    or counts["source_record_rows"] > 0
                    else "document"
                ),
                "data_classification": (
                    "third_party_crawled_content"
                    if args.source == "common-crawl"
                    else snapshot_verification.get(
                        "data_classification", "user_supplied"
                    )
                ),
                "redistribution_status": (
                    redistribution_status
                ),
                "common_crawl_terms": (
                    COMMON_CRAWL_TERMS_URL
                    if third_party_content
                    else None
                ),
                "snapshot": (
                    display_path(Path(args.input))
                    if args.source == "file"
                    else None
                ),
                "record_manifest": (
                    display_path(Path(args.record_manifest))
                    if args.source == "common-crawl"
                    else snapshot_verification.get("record_manifest")
                    if snapshot_verification["status"] == "verified"
                    else None
                ),
                "live_warc_range_read": args.source == "common-crawl",
                "terms_acknowledged": (
                    args.acknowledge_common_crawl_terms
                    if args.source == "common-crawl"
                    else None
                ),
                "snapshot_verification": snapshot_verification,
                "crawl_ids": sorted(
                    {
                        row[0]
                        for row in source_records.project("crawl_id").fetchall()
                    }
                ),
            },
            "batch_size": args.batch_size,
            "pair_score_batch_size": PAIR_SCORE_BATCH_SIZE,
            "algorithm": {
                "tokenizer": "unicode_word_lowercase",
                "normalization": [
                    "nfd",
                    "strip_combining_marks",
                    "punctuation_to_space",
                    "collapse_whitespace",
                ],
                "shingle_size": SHINGLE_SIZE,
                "minhash_values": MINHASH_VALUES,
                "minhash_hash": "blake2b-64",
                "lsh_bands": LSH_BANDS,
                "lsh_rows_per_band": LSH_ROWS_PER_BAND,
                "lsh_band_hash": "blake2b-64",
                "lsh_candidate_probability_at_threshold": round(
                    lsh_candidate_probability(SHINGLE_JACCARD_THRESHOLD), 6
                ),
                "candidate_scope": "global",
                "candidate_baseline": "all_global_pairs",
                "candidate_pair_slot_budget": args.max_candidate_pair_slots,
                "candidate_pair_slot_guard": "fail_before_self_join",
                "acceptance_rule": "exact_shingle_jaccard",
                "shingle_jaccard_threshold": SHINGLE_JACCARD_THRESHOLD,
                "signature_overlap_role": "diagnostic_only",
                "signature_overlap_threshold": SIGNATURE_OVERLAP_THRESHOLD,
                "cluster_id": "prefixed_root_doc_id",
            },
            **counts,
            "output_files": [
                "duplicate_pairs.csv",
                "duplicate_summary.csv",
                "clusters.csv",
                "cluster_inspection.csv",
                "collision_buckets.csv",
                "domain_summary.csv",
                "candidate_summary.csv",
                "source_records.csv",
                "source_summary.csv",
                "source_blocks.parquet",
                "fingerprinted.parquet",
                "scored_pairs.parquet",
                "deduped_documents.parquet",
            ],
            **merge_backend_metadata(backend_metadata),
        },
    )
    return counts


def run(args: argparse.Namespace) -> None:
    runner = require_local_relation_runner(vane.current_config().runner)

    snapshot_metadata_path = snapshot_metadata_path_for_run(args)
    if snapshot_metadata_path is None:
        snapshot_verification: dict[str, Any] = {
            "status": (
                "not_applicable"
                if args.source == "common-crawl"
                else "not_requested"
            )
        }
    else:
        snapshot_verification = validate_snapshot_integrity(
            Path(args.input), snapshot_metadata_path
        )

    conn = vane.connect()
    udf_options = batch_udf_options(args.execution_backend)
    documents_rel, backend_metadata = load_source_documents(
        conn, args, udf_options
    )
    conn.sql("drop table if exists documents")
    documents_rel.order("doc_id").to_table("documents")
    documents = conn.sql("select * from documents")
    validate_document_ids(documents)
    if snapshot_verification["status"] == "verified":
        actual_document_rows = relation_row_count(documents)
        expected_document_rows = int(
            snapshot_verification["expected_block_rows"]
        )
        if actual_document_rows != expected_document_rows:
            raise RuntimeError(
                "snapshot row-count mismatch: "
                f"expected {expected_document_rows}, got {actual_document_rows}"
            )

    source_records_rel = conn.sql(
        """
        select
          crawl_id,
          target_url,
          capture_url,
          capture_timestamp,
          domain,
          warc_record_id,
          warc_date,
          warc_filename,
          warc_offset,
          warc_length,
          warc_digest,
          http_content_type,
          count(*) as text_block_rows
        from documents
        where nullif(warc_record_id, '') is not null
        group by all
        order by capture_timestamp, capture_url
        """
    )
    conn.sql("drop table if exists source_records")
    source_records_rel.to_table("source_records")
    source_records = conn.sql("select * from source_records")
    if "expected_source_record_rows" in snapshot_verification:
        actual_source_records = relation_row_count(source_records)
        expected_source_records = int(
            snapshot_verification["expected_source_record_rows"]
        )
        if actual_source_records != expected_source_records:
            raise RuntimeError(
                "snapshot source-record mismatch: "
                f"expected {expected_source_records}, got {actual_source_records}"
            )
    if args.source == "common-crawl":
        expected_source_records = len(
            load_record_manifest(Path(args.record_manifest))
        )
        actual_source_records = relation_row_count(source_records)
        if actual_source_records != expected_source_records:
            raise RuntimeError(
                "HTML extraction lost pinned Common Crawl records: "
                f"expected {expected_source_records}, got {actual_source_records}"
            )

    source_summary_rel = conn.sql(
        """
        select
          count(*) as text_block_rows,
          count(distinct nullif(warc_record_id, '')) as warc_record_rows,
          count(distinct nullif(capture_url, '')) as capture_url_rows,
          count(distinct domain) as domain_rows,
          count(distinct nullif(target_url, '')) as target_url_rows,
          min(crawled_at) as earliest_capture_date,
          max(crawled_at) as latest_capture_date
        from documents
        """
    )
    conn.sql("drop table if exists source_summary")
    source_summary_rel.to_table("source_summary")
    source_summary = conn.sql("select * from source_summary")

    fingerprinted_rel = conn.sql("select * from documents order by doc_id").map_batches(
        importable_fingerprint_documents_batch(),
        schema=FINGERPRINT_SCHEMA,
        batch_size=args.batch_size,
        **udf_options,
    )
    backend_metadata["fingerprint_documents"] = backend_metadata_entry(
        args.execution_backend
    )
    conn.sql("drop table if exists fingerprinted")
    fingerprinted_rel.order("doc_id").to_table("fingerprinted")
    fingerprinted = conn.sql("select * from fingerprinted")

    band_memberships_rel = band_membership_relation_sql(conn)
    conn.sql("drop table if exists band_memberships")
    band_memberships_rel.to_table("band_memberships")
    band_memberships = conn.sql("select * from band_memberships")
    expected_band_rows = int(
        fingerprinted.aggregate(
            f"count(*) filter (where shingle_count > 0) * {LSH_BANDS}"
        ).fetchone()[0]
    )
    actual_band_rows = relation_row_count(band_memberships)
    if actual_band_rows != expected_band_rows:
        raise RuntimeError(
            "LSH band expansion violated its row-count invariant: "
            f"expected {expected_band_rows}, got {actual_band_rows}"
        )

    collision_buckets_rel = conn.sql(
        """
        select
          band_index,
          lsh_band,
          count(*) as member_count,
          list(doc_id order by doc_id) as member_doc_ids,
          list(distinct domain order by domain) as member_domains,
          cast(count(*) * (count(*) - 1) / 2 as bigint) as pair_slots
        from band_memberships
        group by band_index, lsh_band
        having count(*) > 1
        order by member_count desc, band_index, lsh_band
        """
    )
    conn.sql("drop table if exists collision_buckets")
    collision_buckets_rel.to_table("collision_buckets")
    collision_buckets = conn.sql("select * from collision_buckets")
    validate_candidate_pair_budget(
        collision_buckets,
        max_candidate_pair_slots=args.max_candidate_pair_slots,
    )

    candidate_pairs_rel = conn.sql(
        """
        with candidate_ids as (
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
        )
        select
          c.left_doc_id,
          c.right_doc_id,
          c.left_domain,
          c.right_domain,
          c.shared_bands,
          l.shingle_set as left_shingle_set,
          r.shingle_set as right_shingle_set,
          l.signature as left_signature,
          r.signature as right_signature
        from candidate_ids c
        join fingerprinted l on l.doc_id = c.left_doc_id
        join fingerprinted r on r.doc_id = c.right_doc_id
        order by c.left_doc_id, c.right_doc_id
        """
    )
    conn.sql("drop table if exists candidate_pairs")
    candidate_pairs_rel.to_table("candidate_pairs")
    candidate_pairs = conn.sql("select * from candidate_pairs")
    scored_pairs_rel = candidate_pairs.map_batches(
        importable_score_pairs_batch(),
        schema=SCORED_PAIR_SCHEMA,
        batch_size=PAIR_SCORE_BATCH_SIZE,
        **udf_options,
    )
    backend_metadata["score_pairs"] = backend_metadata_entry(
        args.execution_backend
    )
    conn.sql("drop table if exists scored_pairs")
    scored_pairs_rel.order(
        "shingle_jaccard desc, signature_overlap desc, left_doc_id, right_doc_id"
    ).to_table("scored_pairs")
    scored_pairs = conn.sql("select * from scored_pairs")

    duplicate_pairs_rel = conn.sql(
        """
        select *
        from scored_pairs
        where is_duplicate
        order by shingle_jaccard desc, signature_overlap desc, left_doc_id, right_doc_id
        """
    )
    conn.sql("drop table if exists duplicate_pairs")
    duplicate_pairs_rel.to_table("duplicate_pairs")
    duplicate_pairs = conn.sql("select * from duplicate_pairs")

    duplicate_summary_rel = conn.sql(
        """
        select
          count(*) as duplicate_pair_rows,
          count(*) filter (where p.shingle_jaccard = 1.0)
            as exact_duplicate_pair_rows,
          count(*) filter (where p.shingle_jaccard < 1.0)
            as near_duplicate_pair_rows,
          count(*) filter (
            where nullif(l.warc_record_id, '') is not null
              and l.warc_record_id = r.warc_record_id
          ) as within_warc_duplicate_pair_rows,
          count(*) filter (
            where nullif(l.warc_record_id, '') is not null
              and nullif(r.warc_record_id, '') is not null
              and l.warc_record_id <> r.warc_record_id
          ) as cross_warc_duplicate_pair_rows,
          count(*) filter (
            where nullif(l.capture_url, '') is not null
              and nullif(r.capture_url, '') is not null
              and l.capture_url <> r.capture_url
          ) as cross_url_duplicate_pair_rows
        from duplicate_pairs p
        join documents l on l.doc_id = p.left_doc_id
        join documents r on r.doc_id = p.right_doc_id
        """
    )
    conn.sql("drop table if exists duplicate_summary")
    duplicate_summary_rel.to_table("duplicate_summary")
    duplicate_summary = conn.sql("select * from duplicate_summary")

    domain_summary_rel = conn.sql(
        """
        with document_counts as (
          select
            domain,
            count(*) as document_rows,
            cast(count(*) * (count(*) - 1) / 2 as bigint) as possible_pair_rows
          from documents
          group by domain
        ),
        candidate_counts as (
          select left_domain as domain, count(*) as candidate_pair_rows
          from candidate_pairs
          where left_domain = right_domain
          group by left_domain
        ),
        duplicate_counts as (
          select left_domain as domain, count(*) as duplicate_pair_rows
          from duplicate_pairs
          where left_domain = right_domain
          group by left_domain
        )
        select
          d.domain,
          d.document_rows,
          d.possible_pair_rows,
          coalesce(c.candidate_pair_rows, 0) as candidate_pair_rows,
          coalesce(p.duplicate_pair_rows, 0) as duplicate_pair_rows,
          case
            when d.possible_pair_rows = 0 then 0.0
            else round(
              1.0 - coalesce(c.candidate_pair_rows, 0) / d.possible_pair_rows,
              4
            )
          end as candidate_reduction_ratio
        from document_counts d
        left join candidate_counts c using (domain)
        left join duplicate_counts p using (domain)
        order by d.domain
        """
    )
    conn.sql("drop table if exists domain_summary")
    domain_summary_rel.to_table("domain_summary")
    domain_summary = conn.sql("select * from domain_summary")

    candidate_summary_rel = conn.sql(
        f"""
        with document_count as (
          select count(*) as document_rows from documents
        ),
        candidate_count as (
          select
            count(*) as candidate_pair_rows,
            count(*) filter (where left_domain = right_domain)
              as same_domain_candidate_pair_rows,
            count(*) filter (where left_domain <> right_domain)
              as cross_domain_candidate_pair_rows
          from candidate_pairs
        ),
        duplicate_count as (
          select
            count(*) as duplicate_pair_rows,
            count(*) filter (where left_domain = right_domain)
              as same_domain_duplicate_pair_rows,
            count(*) filter (where left_domain <> right_domain)
              as cross_domain_duplicate_pair_rows
          from duplicate_pairs
        ),
        bucket_expansion as (
          select coalesce(sum(pair_slots), 0) as candidate_pair_slots
          from collision_buckets
        )
        select
          d.document_rows,
          cast(d.document_rows * (d.document_rows - 1) / 2 as bigint)
            as possible_pair_rows,
          c.candidate_pair_rows,
          p.duplicate_pair_rows,
          case
            when d.document_rows < 2 then 0.0
            else round(
              1.0 - c.candidate_pair_rows /
                (d.document_rows * (d.document_rows - 1) / 2),
              4
            )
          end as candidate_reduction_ratio,
          c.same_domain_candidate_pair_rows,
          c.cross_domain_candidate_pair_rows,
          p.same_domain_duplicate_pair_rows,
          p.cross_domain_duplicate_pair_rows,
          b.candidate_pair_slots,
          {args.max_candidate_pair_slots} as candidate_pair_slot_budget
        from document_count d
        cross join candidate_count c
        cross join duplicate_count p
        cross join bucket_expansion b
        """
    )
    conn.sql("drop table if exists candidate_summary")
    candidate_summary_rel.to_table("candidate_summary")
    candidate_summary = conn.sql("select * from candidate_summary")

    conn.sql("drop table if exists clusters")
    cluster_relation_sql(conn).to_table("clusters")
    clusters = conn.sql("select * from clusters")

    representatives_rel = conn.sql(
        """
        with ranked as (
          select
            c.cluster_id,
            c.cluster_size,
            d.doc_id,
            d.source,
            d.domain,
            d.crawled_at,
            d.title,
            d.body,
            d.block_index,
            d.block_chars,
            d.crawl_id,
            d.target_url,
            d.capture_url,
            d.capture_timestamp,
            d.warc_record_id,
            d.warc_date,
            d.warc_filename,
            d.warc_offset,
            d.warc_length,
            d.warc_digest,
            d.http_content_type,
            f.token_count,
            row_number() over (
              partition by c.cluster_id
              order by d.crawled_at desc, f.token_count desc, d.doc_id
            ) as rank
          from clusters c
          join documents d using (doc_id)
          join fingerprinted f using (doc_id)
        )
        select
          cluster_id,
          cluster_size,
          doc_id as representative_doc_id,
          source,
          domain,
          crawled_at,
          title,
          body,
          block_index,
          block_chars,
          crawl_id,
          target_url,
          capture_url,
          capture_timestamp,
          warc_record_id,
          warc_date,
          warc_filename,
          warc_offset,
          warc_length,
          warc_digest,
          http_content_type,
          token_count
        from ranked
        where rank = 1
        order by cluster_id
        """
    )
    conn.sql("drop table if exists representatives")
    representatives_rel.to_table("representatives")
    representatives = conn.sql("select * from representatives")

    cluster_inspection_rel = conn.sql(
        """
        with members as (
          select
            c.cluster_id,
            c.cluster_size,
            count(distinct d.source) as source_count,
            count(distinct d.domain) as domain_count,
            count(distinct nullif(d.warc_record_id, '')) as warc_record_count,
            string_agg(
              distinct nullif(d.capture_url, ''),
              ', ' order by nullif(d.capture_url, '')
            ) as member_capture_urls,
            string_agg(c.doc_id, ', ' order by c.doc_id) as member_doc_ids
          from clusters c
          join documents d using (doc_id)
          group by c.cluster_id, c.cluster_size
        ),
        edge_quality as (
          select
            c.cluster_id,
            count(*) as duplicate_edge_rows,
            min(p.shingle_jaccard) as weakest_shingle_jaccard,
            min(p.signature_overlap) as weakest_signature_overlap
          from duplicate_pairs p
          join clusters c on c.doc_id = p.left_doc_id
          group by c.cluster_id
        )
        select
          m.cluster_id,
          m.cluster_size,
          r.representative_doc_id,
          m.source_count,
          m.domain_count,
          m.warc_record_count,
          m.member_capture_urls,
          m.member_doc_ids,
          q.duplicate_edge_rows,
          q.weakest_shingle_jaccard,
          q.weakest_signature_overlap
        from members m
        join representatives r using (cluster_id)
        left join edge_quality q using (cluster_id)
        where m.cluster_size > 1
        order by m.cluster_size desc, m.cluster_id
        """
    )
    conn.sql("drop table if exists cluster_inspection")
    cluster_inspection_rel.to_table("cluster_inspection")
    cluster_inspection = conn.sql("select * from cluster_inspection")

    artifact_counts = write_artifacts(
        output_dir=Path(args.output_dir),
        documents=documents,
        fingerprinted=fingerprinted,
        band_memberships=band_memberships,
        collision_buckets=collision_buckets,
        candidate_pairs=candidate_pairs,
        scored_pairs=scored_pairs,
        duplicate_pairs=duplicate_pairs,
        duplicate_summary=duplicate_summary,
        clusters=clusters,
        representatives=representatives,
        cluster_inspection=cluster_inspection,
        domain_summary=domain_summary,
        candidate_summary=candidate_summary,
        source_records=source_records,
        source_summary=source_summary,
        snapshot_verification=snapshot_verification,
        backend_metadata=backend_metadata,
        runner=runner,
        args=args,
    )

    print(f"Source WARC records: {artifact_counts['source_record_rows']}")
    print(f"Text blocks: {artifact_counts['document_rows']}")
    print(f"Global pair space: {artifact_counts['possible_pair_rows']}")
    print(
        "Candidate pair slots: "
        f"{artifact_counts['candidate_pair_slots']} / "
        f"{args.max_candidate_pair_slots}"
    )
    print(f"LSH candidate pairs: {artifact_counts['candidate_pair_rows']}")
    print(
        "Candidate reduction: "
        f"{artifact_counts['candidate_reduction_ratio']:.2%}"
    )
    print(f"Duplicate pairs: {artifact_counts['duplicate_pair_rows']}")
    print(f"Clusters: {artifact_counts['representative_rows']}")
    print(f"Output directory: {args.output_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect near-duplicate web-text records with Vane.",
    )
    parser.add_argument(
        "--source",
        choices=("file", "common-crawl"),
        default="file",
        help=(
            "Read the redistribution-safe offline fixture, or fetch a locally "
            "prepared Common Crawl WARC manifest live."
        ),
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument(
        "--snapshot-metadata",
        help=(
            "Optional JSON integrity contract for --input. The checked-in "
            "default snapshot is verified automatically."
        ),
    )
    parser.add_argument(
        "--record-manifest",
        default=str(DEFAULT_RECORD_MANIFEST),
        help="Local Common Crawl CDX records used by the live source.",
    )
    parser.add_argument(
        "--acknowledge-common-crawl-terms",
        action="store_true",
        help=(
            "Confirm that you reviewed Common Crawl's terms and the source "
            "sites' rights before fetching third-party crawled content."
        ),
    )
    parser.add_argument("--source-timeout", type=positive_int, default=60)
    parser.add_argument(
        "--max-html-bytes", type=positive_int, default=5 * 1024 * 1024
    )
    parser.add_argument("--min-block-chars", type=positive_int, default=80)
    parser.add_argument("--max-blocks-per-page", type=positive_int, default=200)
    parser.add_argument("--source-batch-size", type=positive_int, default=8)
    parser.add_argument("--batch-size", type=positive_int, default=128)
    parser.add_argument(
        "--max-candidate-pair-slots",
        type=positive_int,
        default=DEFAULT_MAX_CANDIDATE_PAIR_SLOTS,
        help=(
            "Fail before the LSH self-join when colliding buckets would expand "
            "past this many band-level pair rows."
        ),
    )
    parser.add_argument(
        "--execution-backend",
        choices=PUBLIC_BACKEND_CHOICES,
        default="auto",
        help="Let Vane infer the backend, or pin a task backend explicitly.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()
    try:
        require_local_relation_runner(vane.current_config().runner)
        if args.source == "file":
            validate_input_path(Path(args.input))
            if args.snapshot_metadata:
                validate_input_path(Path(args.snapshot_metadata))
        else:
            if not args.acknowledge_common_crawl_terms:
                raise ValueError(
                    "--source common-crawl requires "
                    "--acknowledge-common-crawl-terms; review "
                    f"{COMMON_CRAWL_TERMS_URL} and the source-site rights"
                )
            validate_input_path(Path(args.record_manifest))
            load_record_manifest(Path(args.record_manifest))
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    return args


def main() -> int:
    try:
        run(parse_args())
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
