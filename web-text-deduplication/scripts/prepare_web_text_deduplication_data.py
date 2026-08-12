#!/usr/bin/env python3
"""Build the pinned Common Crawl block snapshot used by web deduplication."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow as pa
import vane


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src._common import (  # noqa: E402
    PUBLIC_BACKEND_CHOICES,
    RunnerWorkspace,
    backend_metadata_entry,
    batch_udf_options,
    merge_backend_metadata,
    positive_int,
    require_ray_runner,
    write_json,
)
from src._common_crawl import (  # noqa: E402
    BLOCK_SCHEMA,
    ExtractHtmlBlocksBatch,
    HTML_BLOCK_SELECTOR,
    WarcRecordSpec,
    common_crawl_range_relation,
    discover_record_specs,
    load_record_manifest,
    load_target_manifest,
)


DATA_DIR = REPO_ROOT / "data" / "web_text_deduplication"
WORKSPACE_DIR = REPO_ROOT / "workspace" / "web_text_deduplication"
DEFAULT_TARGETS = DATA_DIR / "common_crawl_targets.csv"
DEFAULT_RECORD_MANIFEST = WORKSPACE_DIR / "common_crawl_records.csv"
DEFAULT_OUTPUT = WORKSPACE_DIR / "common_crawl_blocks.parquet"
DEFAULT_METADATA_OUTPUT = WORKSPACE_DIR / "common_crawl_snapshot.json"
DEFAULT_CRAWL_ID = "CC-MAIN-2025-33"
COMMON_CRAWL_TERMS_URL = "https://commoncrawl.org/terms-of-use"


def relation_row_count(rel) -> int:
    return int(rel.aggregate("count(*) as row_count").fetchone()[0])


def specs_table(specs: list[WarcRecordSpec]) -> pa.Table:
    rows = [spec.as_dict() for spec in specs]
    return pa.Table.from_pylist(rows)


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


def run(args: argparse.Namespace) -> None:
    runner = vane.current_config().runner
    require_ray_runner(runner)
    targets_path = Path(args.targets)
    record_manifest_path = Path(args.record_manifest)
    output_path = Path(args.output)
    metadata_output_path = Path(args.metadata_output)

    if args.refresh_index:
        specs = discover_record_specs(
            crawl_id=args.crawl_id,
            targets=load_target_manifest(targets_path),
            timeout=args.timeout,
        )
    else:
        specs = load_record_manifest(record_manifest_path)

    with TemporaryDirectory(prefix="vane-web-snapshot-ray-") as workspace_root:
        _run_with_workspace(
            args,
            runner,
            specs,
            targets_path,
            record_manifest_path,
            output_path,
            metadata_output_path,
            Path(workspace_root),
        )


def _run_with_workspace(
    args: argparse.Namespace,
    runner: str,
    specs: list[WarcRecordSpec],
    targets_path: Path,
    record_manifest_path: Path,
    output_path: Path,
    metadata_output_path: Path,
    workspace_root: Path,
) -> None:
    conn = vane.connect()
    workspace = RunnerWorkspace(workspace_root, conn)
    if args.refresh_index:
        record_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        workspace.write_csv(
            workspace.stage_table("record-specs", specs_table(specs)),
            record_manifest_path,
        )

    raw_records_rel = common_crawl_range_relation(
        conn,
        specs,
        timeout=args.timeout,
        max_html_bytes=args.max_html_bytes,
    )
    raw_records = workspace.materialize_view(
        "raw_warc_records",
        raw_records_rel.order("capture_timestamp, index_url"),
    )

    blocks_rel = raw_records.map_batches(
        ExtractHtmlBlocksBatch(
            min_block_chars=args.min_block_chars,
            max_blocks_per_page=args.max_blocks_per_page,
        ),
        schema=BLOCK_SCHEMA,
        batch_size=args.batch_size,
        **batch_udf_options(args.execution_backend),
    )
    blocks = workspace.materialize_view(
        "common_crawl_blocks",
        blocks_rel.order("target_url, capture_timestamp, block_index"),
    )
    block_rows = relation_row_count(blocks)
    if block_rows == 0:
        raise RuntimeError("Common Crawl extraction produced zero text blocks")

    workspace.write_parquet_table(
        blocks.project("*").to_arrow_table(),
        output_path,
    )

    source_rows = relation_row_count(raw_records)
    source_rows_with_blocks = int(
        blocks.aggregate("count(distinct warc_record_id)").fetchone()[0]
    )
    if source_rows_with_blocks != source_rows:
        raise RuntimeError(
            "Common Crawl snapshot lost source records during HTML extraction: "
            f"{source_rows_with_blocks} of {source_rows} records produced blocks"
        )
    domain_rows = int(blocks.aggregate("count(distinct domain)").fetchone()[0])
    target_rows = int(
        blocks.aggregate("count(distinct target_url)").fetchone()[0]
    )
    capture_url_rows = int(
        blocks.aggregate("count(distinct capture_url)").fetchone()[0]
    )
    earliest_capture_date, latest_capture_date = blocks.aggregate(
        "min(crawled_at), max(crawled_at)"
    ).fetchone()
    record_manifest_sha256 = file_sha256(record_manifest_path)
    snapshot_sha256 = file_sha256(output_path)
    backend_metadata = {
        "extract_html_blocks": backend_metadata_entry(args.execution_backend)
    }
    metadata_output_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        metadata_output_path,
        {
            "dataset": "common_crawl_web_text_blocks",
            "crawl_ids": sorted({spec.crawl_id for spec in specs}),
            "source": "Common Crawl WARC byte ranges",
            "data_classification": "third_party_crawled_content",
            "third_party_crawled_content": True,
            "redistribution_status": "local_only_rights_review_required",
            "common_crawl_terms": COMMON_CRAWL_TERMS_URL,
            "terms_acknowledged": args.acknowledge_common_crawl_terms,
            "index_endpoints": [
                f"https://index.commoncrawl.org/{crawl_id}-index"
                for crawl_id in sorted({spec.crawl_id for spec in specs})
            ],
            "data_endpoint": "https://data.commoncrawl.org/",
            "selection": (
                "Pinned repeated captures, site templates, independent domains, "
                "and a cross-domain duplicate pair."
            ),
            "targets": display_path(targets_path),
            "record_manifest": display_path(record_manifest_path),
            "record_manifest_sha256": record_manifest_sha256,
            "snapshot": display_path(output_path),
            "snapshot_sha256": snapshot_sha256,
            "snapshot_bytes": output_path.stat().st_size,
            "source_record_rows": source_rows,
            "block_rows": block_rows,
            "target_url_rows": target_rows,
            "capture_url_rows": capture_url_rows,
            "domain_rows": domain_rows,
            "earliest_capture_date": earliest_capture_date,
            "latest_capture_date": latest_capture_date,
            "min_block_chars": args.min_block_chars,
            "max_blocks_per_page": args.max_blocks_per_page,
            "html_parser": "selectolax",
            "html_parser_version": importlib.metadata.version("selectolax"),
            "html_block_selector": HTML_BLOCK_SELECTOR,
            "generation_command": (
                ".venv/bin/python scripts/prepare_web_text_deduplication_data.py "
                "--refresh-index --acknowledge-common-crawl-terms"
            ),
            "runner": runner,
            **merge_backend_metadata(backend_metadata),
        },
    )

    print(f"Common Crawl records: {source_rows}")
    print(f"Extracted text blocks: {block_rows}")
    print(f"Domains: {domain_rows}")
    print(f"Snapshot: {output_path}")
    print(f"Snapshot SHA-256: {snapshot_sha256}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a reproducible Common Crawl snapshot for Vane dedupe."
    )
    parser.add_argument("--crawl-id", default=DEFAULT_CRAWL_ID)
    parser.add_argument("--targets", default=str(DEFAULT_TARGETS))
    parser.add_argument("--record-manifest", default=str(DEFAULT_RECORD_MANIFEST))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--metadata-output", default=str(DEFAULT_METADATA_OUTPUT))
    parser.add_argument("--refresh-index", action="store_true")
    parser.add_argument(
        "--acknowledge-common-crawl-terms",
        action="store_true",
        help=(
            "Confirm that you reviewed Common Crawl's terms and the source "
            "sites' rights before downloading third-party crawled content."
        ),
    )
    parser.add_argument("--timeout", type=positive_int, default=60)
    parser.add_argument(
        "--max-html-bytes", type=positive_int, default=5 * 1024 * 1024
    )
    parser.add_argument("--min-block-chars", type=positive_int, default=80)
    parser.add_argument("--max-blocks-per-page", type=positive_int, default=200)
    parser.add_argument("--batch-size", type=positive_int, default=8)
    parser.add_argument(
        "--execution-backend",
        choices=PUBLIC_BACKEND_CHOICES,
        default="auto",
        help="Use RayRunner's ray_task default, or pin a task backend explicitly.",
    )
    args = parser.parse_args()
    try:
        if not args.acknowledge_common_crawl_terms:
            raise ValueError(
                "preparing Common Crawl data requires "
                "--acknowledge-common-crawl-terms; review "
                f"{COMMON_CRAWL_TERMS_URL} and the source-site rights"
            )
        if not Path(args.targets).is_file():
            raise FileNotFoundError(f"target manifest does not exist: {args.targets}")
        if not args.refresh_index and not Path(args.record_manifest).is_file():
            raise FileNotFoundError(
                f"record manifest does not exist: {args.record_manifest}"
            )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    return args


def main() -> None:
    try:
        run(parse_args())
    finally:
        vane.teardown_runner()


if __name__ == "__main__":
    main()
