from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

from src._common import RunnerWorkspace
from src._common_crawl import (
    CommonCrawlRangeTask,
    ExtractHtmlBlocksBatch,
    WarcRecordSpec,
    extract_html_blocks,
    load_target_manifest,
    parse_warc_response,
)
from scripts.prepare_web_text_deduplication_data import (
    DEFAULT_METADATA_OUTPUT,
    DEFAULT_OUTPUT,
    DEFAULT_RECORD_MANIFEST,
    file_sha256,
    run as run_preparation,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class CommonCrawlSourceTest(unittest.TestCase):
    @staticmethod
    def warc_payload() -> tuple[bytes, dict[str, str]]:
        html = b"""
        <html>
          <head><title>Example reference page</title></head>
          <body>
            <script>ignore this script body completely</script>
            <article>
              <p>This paragraph is long enough to become a useful text block.</p>
            </article>
          </body>
        </html>
        """
        stream = io.BytesIO()
        writer = WARCWriter(stream, gzip=True)
        http_headers = StatusAndHeaders(
            "200 OK",
            [("Content-Type", "text/html; charset=UTF-8")],
            protocol="HTTP/1.1",
        )
        record = writer.create_warc_record(
            "https://example.com/reference",
            "response",
            payload=io.BytesIO(html),
            http_headers=http_headers,
            warc_headers_dict={
                "WARC-Date": "2025-08-13T12:00:00Z",
                "WARC-Identified-Payload-Type": "text/html",
            },
        )
        writer.write_record(record)
        payload = stream.getvalue()
        record.raw_stream.close()
        parsed = parse_warc_response(payload, max_html_bytes=1024 * 1024)
        return payload, parsed

    def test_range_task_checks_pinned_headers_and_preserves_provenance(self) -> None:
        payload, parsed = self.warc_payload()
        spec = WarcRecordSpec(
            crawl_id="CC-MAIN-2025-33",
            target_url="https://example.com/reference",
            capture_timestamp="20250813120000",
            url="https://example.com/reference",
            mime="text/html",
            status=200,
            digest=parsed["warc_payload_digest"].removeprefix("sha1:"),
            length=len(payload),
            offset=123,
            filename=(
                "crawl-data/CC-MAIN-2025-33/segments/example/warc/"
                "example.warc.gz"
            ),
        )
        with patch(
            "src._common_crawl.fetch_warc_range", return_value=payload
        ):
            batches = list(
                CommonCrawlRangeTask(
                    spec=spec,
                    timeout=5,
                    max_html_bytes=1024 * 1024,
                ).execute()
            )

        self.assertEqual(len(batches), 1)
        raw_row = pa.Table.from_batches(batches).to_pylist()[0]
        self.assertEqual(raw_row["index_url"], spec.url)
        self.assertEqual(raw_row["warc_offset"], 123)
        self.assertEqual(raw_row["warc_digest"], spec.digest)
        self.assertTrue(raw_row["warc_record_id"])

        blocks = ExtractHtmlBlocksBatch(
            min_block_chars=20,
            max_blocks_per_page=10,
        )(pa.Table.from_batches(batches)).to_pylist()
        self.assertGreaterEqual(len(blocks), 1)
        self.assertTrue(all(row["crawl_id"] == spec.crawl_id for row in blocks))
        self.assertTrue(all(row["target_url"] == spec.target_url for row in blocks))
        self.assertTrue(all(row["capture_url"] == spec.url for row in blocks))
        self.assertTrue(all(row["doc_id"].startswith("cc-") for row in blocks))

    def test_html_extraction_removes_scripts_and_is_bounded(self) -> None:
        html = b"""
        <title>Bounded extraction</title>
        <script>This script must not be extracted.</script>
        <main>
          <p>First sufficiently long paragraph for the extraction contract.</p>
          <p>Second sufficiently long paragraph that should hit the row limit.</p>
        </main>
        """
        title, blocks = extract_html_blocks(
            html,
            min_block_chars=20,
            max_blocks_per_page=2,
        )
        self.assertEqual(title, "Bounded extraction")
        self.assertEqual(
            blocks,
            [
                "First sufficiently long paragraph for the extraction contract.",
                "Second sufficiently long paragraph that should hit the row limit.",
            ],
        )
        self.assertTrue(all("script must not" not in block.lower() for block in blocks))

    def test_record_spec_rejects_non_html_or_wrong_crawl_path(self) -> None:
        base = {
            "crawl_id": "CC-MAIN-2025-33",
            "target_url": "https://example.com/",
            "capture_timestamp": "20250813120000",
            "url": "https://example.com/",
            "mime": "text/html",
            "status": 200,
            "digest": "ABC",
            "length": 100,
            "offset": 20,
            "filename": (
                "crawl-data/CC-MAIN-2025-33/segments/example/warc/"
                "example.warc.gz"
            ),
        }
        with self.assertRaisesRegex(ValueError, "HTTP 200 text/html"):
            WarcRecordSpec.from_mapping({**base, "mime": "application/pdf"})
        with self.assertRaisesRegex(ValueError, "does not belong"):
            WarcRecordSpec.from_mapping(
                {
                    **base,
                    "filename": (
                        "crawl-data/CC-MAIN-2025-30/segments/example/warc/"
                        "example.warc.gz"
                    ),
                }
            )

    def test_public_tree_does_not_bundle_common_crawl_content(self) -> None:
        data_dir = REPO_ROOT / "data" / "web_text_deduplication"
        for name in (
            "common_crawl_records.csv",
            "common_crawl_blocks.parquet",
            "common_crawl_snapshot.json",
        ):
            self.assertFalse((data_dir / name).exists(), msg=name)

        targets = load_target_manifest(data_dir / "common_crawl_targets.csv")
        self.assertEqual(
            targets,
            [
                ("http://www.example.com/", 1),
                ("https://example.org/", 1),
            ],
        )
        workspace = REPO_ROOT / "workspace" / "web_text_deduplication"
        self.assertEqual(DEFAULT_RECORD_MANIFEST, workspace / "common_crawl_records.csv")
        self.assertEqual(DEFAULT_OUTPUT, workspace / "common_crawl_blocks.parquet")
        self.assertEqual(DEFAULT_METADATA_OUTPUT, workspace / "common_crawl_snapshot.json")

    def test_snapshot_writer_replaces_a_dataset_with_one_parquet_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-web-snapshot-write-") as tmp_dir:
            root = Path(tmp_dir)
            output = root / "snapshot.parquet"
            output.mkdir()
            pq.write_table(
                pa.table({"doc_id": ["stale"]}),
                output / "part-0.parquet",
            )
            workspace = RunnerWorkspace(root / "workspace", None)

            workspace.write_parquet_table(
                pa.table({"doc_id": ["fresh-1", "fresh-2"]}),
                output,
            )

            self.assertTrue(output.is_file())
            self.assertEqual(
                pq.read_table(output).column("doc_id").to_pylist(),
                ["fresh-1", "fresh-2"],
            )
            self.assertEqual(len(file_sha256(output)), 64)

    def test_preparation_requires_explicit_terms_acknowledgement(self) -> None:
        env = os.environ.copy()
        env.pop("VANE_RUNNER", None)
        env.update(
            {
                "RAY_ADDRESS": "local",
                "VANE_PROGRESS": "0",
                "RAY_LOG_TO_DRIVER": "0",
            }
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "prepare_web_text_deduplication_data.py"),
            ],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn(
            "preparing Common Crawl data requires "
            "--acknowledge-common-crawl-terms; review "
            "https://commoncrawl.org/terms-of-use and the source-site rights",
            completed.stderr,
        )

    def test_preparation_rejects_local_runner_before_reading_inputs(self) -> None:
        with patch(
            "scripts.prepare_web_text_deduplication_data.vane.current_config",
            return_value=SimpleNamespace(runner="local"),
        ):
            with self.assertRaisesRegex(RuntimeError, "requires Vane RayRunner"):
                run_preparation(SimpleNamespace())


if __name__ == "__main__":
    unittest.main()
