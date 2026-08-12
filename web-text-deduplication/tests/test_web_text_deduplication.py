from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import vane

from src._common import (
    RunnerWorkspace,
    batch_udf_options,
)
from src.web_text_deduplication import (
    DEFAULT_INPUT,
    DEFAULT_SNAPSHOT_METADATA,
    LSH_BANDS,
    MINHASH_VALUES,
    band_membership_relations,
    build_cluster_relation,
    colliding_band_memberships_relation,
    file_sha256,
    fingerprint_documents_batch,
    jaccard,
    lsh_candidate_probability,
    lsh_band_keys,
    normalize_text,
    parse_args,
    score_pairs_batch,
    shingles,
    validate_candidate_pair_budget,
    validate_document_ids,
    validate_snapshot_integrity,
)
from scripts.prepare_web_text_deduplication_fixture import fixture_rows


REPO_ROOT = Path(__file__).resolve().parents[1]


def example_subprocess_env() -> dict[str, str]:
    env = {
        **os.environ,
        "RAY_ADDRESS": "local",
        "VANE_PROGRESS": "0",
        "RAY_LOG_TO_DRIVER": "0",
    }
    env.pop("VANE_RUNNER", None)
    return env


class WebTextDeduplicationTest(unittest.TestCase):
    _relation_connection = None

    @classmethod
    def relation_connection(cls):
        if cls._relation_connection is None:
            cls._relation_connection = vane.connect()
        return cls._relation_connection

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._relation_connection is not None:
            cls._relation_connection.close()

    def invoke_example(
        self,
        output_dir: Path,
        *extra_args: str,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "src" / "web_text_deduplication.py"),
                "--output-dir",
                str(output_dir),
                *extra_args,
            ],
            cwd=REPO_ROOT,
            env=env or example_subprocess_env(),
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )

    def run_example(self, output_dir: Path, *extra_args: str) -> None:
        completed = self.invoke_example(output_dir, *extra_args)
        self.assertEqual(completed.returncode, 0, msg=completed.stdout + completed.stderr)

    def test_local_runner_override_is_rejected_before_writing_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-web-dedup-local-") as tmp_dir:
            output_dir = Path(tmp_dir) / "web_text_deduplication"
            env = example_subprocess_env()
            env["VANE_RUNNER"] = "local"

            completed = self.invoke_example(output_dir, env=env)

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("requires Vane RayRunner", completed.stderr)
            self.assertFalse((output_dir / "manifest.json").exists())

    def test_default_run_writes_deduplication_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-web-dedup-") as tmp_dir:
            output_dir = Path(tmp_dir) / "web_text_deduplication"
            output_dir.mkdir(parents=True)
            pq.write_table(
                pa.table({"stale": [True]}),
                output_dir / "fingerprinted.parquet",
            )
            self.run_example(output_dir)
            self.run_example(output_dir)

            manifest = json.loads((output_dir / "manifest.json").read_text())
            expected_counts = {
                "document_rows": 24,
                "fingerprinted_rows": 24,
                "band_membership_rows": 192,
                "collision_bucket_rows": 48,
                "candidate_pair_rows": 18,
                "candidate_pair_slots": 80,
                "scored_pair_rows": 18,
                "duplicate_pair_rows": 18,
                "exact_duplicate_pair_rows": 6,
                "near_duplicate_pair_rows": 12,
                "within_warc_duplicate_pair_rows": 0,
                "cross_warc_duplicate_pair_rows": 0,
                "cross_url_duplicate_pair_rows": 0,
                "cluster_rows": 24,
                "representative_rows": 12,
                "cluster_count": 12,
                "removed_document_rows": 12,
                "inspection_rows": 6,
                "domain_summary_rows": 4,
                "source_record_rows": 0,
                "source_target_rows": 0,
                "source_url_rows": 0,
                "domain_rows": 4,
                "possible_pair_rows": 276,
                "max_collision_bucket_size": 3,
                "max_cluster_size": 3,
                "singleton_cluster_rows": 6,
                "duplicate_cluster_rows": 6,
                "same_domain_candidate_pair_rows": 0,
                "cross_domain_candidate_pair_rows": 18,
                "same_domain_duplicate_pair_rows": 0,
                "cross_domain_duplicate_pair_rows": 18,
            }
            for key, value in expected_counts.items():
                self.assertEqual(manifest[key], value, msg=key)
            self.assertEqual(manifest["retained_ratio"], 0.5)
            self.assertEqual(manifest["candidate_reduction_ratio"], 0.9348)
            self.assertEqual(manifest["algorithm"]["candidate_scope"], "global")
            self.assertEqual(
                manifest["algorithm"]["candidate_baseline"],
                "all_global_pairs",
            )
            self.assertEqual(
                manifest["algorithm"]["tokenizer"], "unicode_word_lowercase"
            )
            self.assertEqual(
                manifest["algorithm"]["candidate_pair_slot_budget"], 1_000_000
            )
            self.assertEqual(
                manifest["algorithm"]["candidate_pair_slot_guard"],
                "fail_before_self_join",
            )
            self.assertEqual(
                manifest["algorithm"]["lsh_candidate_probability_at_threshold"],
                0.378122,
            )
            snapshot_verification = manifest["source"]["snapshot_verification"]
            self.assertEqual(snapshot_verification["status"], "verified")
            self.assertEqual(
                snapshot_verification["snapshot_sha256"],
                "7a9f14e2e6cc116d1beb8e716e8bbe10fe8ece032b17aa1a7f31f77eb4e4e824",
            )
            self.assertEqual(
                manifest["source"]["data_classification"],
                "synthetic_fixture",
            )
            self.assertEqual(
                manifest["source"]["redistribution_status"],
                "repository_fixture",
            )
            self.assertFalse(
                snapshot_verification["third_party_crawled_content"]
            )
            self.assertEqual(snapshot_verification["license_id"], "Apache-2.0")
            self.assertEqual(manifest["runner"], "ray")
            self.assertEqual(manifest["execution_backend"], "ray_task")
            self.assertEqual(
                set(manifest["execution_backends"].values()),
                {"ray_task"},
            )

            with (output_dir / "duplicate_pairs.csv").open(
                newline="", encoding="utf-8"
            ) as duplicate_file:
                duplicate_rows = list(csv.DictReader(duplicate_file))
            self.assertEqual(len(duplicate_rows), 18)
            self.assertEqual(
                sum(
                    row["left_domain"] != row["right_domain"]
                    for row in duplicate_rows
                ),
                18,
            )
            self.assertTrue(
                all(float(row["shingle_jaccard"]) >= 0.7 for row in duplicate_rows)
            )

            with (output_dir / "cluster_inspection.csv").open(
                newline="", encoding="utf-8"
            ) as inspection_file:
                inspection_rows = list(csv.DictReader(inspection_file))
            self.assertEqual(len(inspection_rows), 6)
            self.assertTrue(all(row["representative_doc_id"] for row in inspection_rows))
            self.assertTrue(all(row["member_doc_ids"] for row in inspection_rows))

            with (output_dir / "collision_buckets.csv").open(
                newline="", encoding="utf-8"
            ) as collision_file:
                collision_rows = list(csv.DictReader(collision_file))
            self.assertEqual(len(collision_rows), 48)
            self.assertEqual(max(int(row["member_count"]) for row in collision_rows), 3)

            with (output_dir / "domain_summary.csv").open(
                newline="", encoding="utf-8"
            ) as summary_file:
                summary_by_domain = {
                    row["domain"]: row for row in csv.DictReader(summary_file)
                }
            self.assertEqual(
                summary_by_domain["docs.example"]["possible_pair_rows"], "15"
            )
            self.assertEqual(
                summary_by_domain["docs.example"]["candidate_pair_rows"], "0"
            )

            source_blocks = pq.read_table(output_dir / "source_blocks.parquet")
            fingerprinted = pq.read_table(output_dir / "fingerprinted.parquet")
            scored_pairs = pq.read_table(output_dir / "scored_pairs.parquet")
            deduped = pq.read_table(output_dir / "deduped_documents.parquet")
            self.assertEqual(source_blocks.num_rows, 24)
            self.assertEqual(fingerprinted.num_rows, 24)
            self.assertEqual(scored_pairs.num_rows, 18)
            self.assertEqual(deduped.num_rows, 12)
            self.assertTrue(
                pa.types.is_boolean(
                    scored_pairs.schema.field("is_duplicate").type
                )
            )
            self.assertTrue(pa.types.is_date32(deduped.schema.field("crawled_at").type))
            self.assertIn("normalized_text", fingerprinted.column_names)
            for row in fingerprinted.to_pylist():
                self.assertEqual(len(row["signature"]), 64)
                self.assertEqual(len(row["lsh_bands"]), 8)
            self.assertIn("warc_record_id", deduped.column_names)
            self.assertIn("capture_url", deduped.column_names)

            for output_file in manifest["output_files"]:
                self.assertTrue((output_dir / output_file).exists(), output_file)

    def test_auto_delegates_backend_selection_to_vane(self) -> None:
        self.assertEqual(batch_udf_options("auto"), {})
        self.assertEqual(
            batch_udf_options("subprocess_task"),
            {"execution_backend": "subprocess_task"},
        )

    def test_ray_task_backend_is_accepted_and_forwarded(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["web_text_deduplication.py", "--execution-backend", "ray_task"],
        ):
            args = parse_args()

        self.assertEqual(args.execution_backend, "ray_task")
        self.assertEqual(
            batch_udf_options("ray_task"),
            {"execution_backend": "ray_task"},
        )

    def test_no_collision_input_writes_zero_candidate_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-web-dedup-unique-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            input_path = tmp_path / "documents.csv"
            with input_path.open("w", newline="", encoding="utf-8") as input_file:
                writer = csv.DictWriter(
                    input_file,
                    fieldnames=[
                        "doc_id",
                        "source",
                        "domain",
                        "crawled_at",
                        "title",
                        "body",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "doc_id": "alpha",
                            "source": "docs",
                            "domain": "example.com",
                            "crawled_at": "2026-07-01",
                            "title": "Alpha release",
                            "body": "Document alpha covers release ownership and review.",
                        },
                        {
                            "doc_id": "beta",
                            "source": "docs",
                            "domain": "example.com",
                            "crawled_at": "2026-07-02",
                            "title": "Beta operations",
                            "body": "Document beta explains incident routing and escalation.",
                        },
                    ]
                )

            output_dir = tmp_path / "output"
            self.run_example(output_dir, "--input", str(input_path))
            manifest = json.loads((output_dir / "manifest.json").read_text())

            self.assertEqual(manifest["possible_pair_rows"], 1)
            self.assertEqual(manifest["candidate_pair_rows"], 0)
            self.assertEqual(manifest["collision_bucket_rows"], 0)
            self.assertEqual(manifest["max_collision_bucket_size"], 0)
            self.assertEqual(manifest["candidate_reduction_ratio"], 1.0)
            self.assertEqual(manifest["duplicate_pair_rows"], 0)
            self.assertEqual(manifest["representative_rows"], 2)
            self.assertIsNone(manifest["source"]["record_manifest"])
            self.assertEqual(
                manifest["source"]["snapshot_verification"]["status"],
                "not_requested",
            )

    def test_default_snapshot_integrity_contract_is_verified(self) -> None:
        verification = validate_snapshot_integrity(
            DEFAULT_INPUT,
            DEFAULT_SNAPSHOT_METADATA,
        )
        self.assertEqual(verification["status"], "verified")
        self.assertEqual(verification["expected_block_rows"], 24)
        self.assertNotIn("expected_source_record_rows", verification)
        self.assertEqual(verification["data_classification"], "synthetic_fixture")
        self.assertFalse(verification["third_party_crawled_content"])

    def test_default_fixture_matches_the_deterministic_generator(self) -> None:
        with DEFAULT_INPUT.open(newline="", encoding="utf-8") as input_file:
            rows = list(csv.DictReader(input_file))
        self.assertEqual(rows, fixture_rows())

    def test_live_common_crawl_requires_explicit_terms_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-web-terms-") as tmp_dir:
            completed = self.invoke_example(
                Path(tmp_dir) / "output",
                "--source",
                "common-crawl",
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn(
                "--source common-crawl requires "
                "--acknowledge-common-crawl-terms; review "
                "https://commoncrawl.org/terms-of-use and the source-site rights",
                completed.stderr,
            )

    def test_snapshot_integrity_rejects_a_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-web-snapshot-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            snapshot = tmp_path / "snapshot.parquet"
            record_manifest = tmp_path / "records.csv"
            metadata_path = tmp_path / "snapshot.json"
            snapshot.write_bytes(b"tampered snapshot")
            record_manifest.write_text("record_id\nrecord-1\n", encoding="utf-8")
            metadata_path.write_text(
                json.dumps(
                    {
                        "snapshot": str(snapshot),
                        "snapshot_sha256": "0" * 64,
                        "snapshot_bytes": snapshot.stat().st_size,
                        "block_rows": 1,
                        "record_manifest": str(record_manifest),
                        "record_manifest_sha256": "0" * 64,
                        "source_record_rows": 1,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "snapshot SHA-256 mismatch"):
                validate_snapshot_integrity(snapshot, metadata_path)

    def test_custom_snapshot_contract_does_not_require_warc_lineage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-web-snapshot-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            snapshot = tmp_path / "snapshot.parquet"
            metadata_path = tmp_path / "snapshot.json"
            snapshot.write_bytes(b"custom snapshot contract")
            metadata_path.write_text(
                json.dumps(
                    {
                        "snapshot": str(snapshot),
                        "snapshot_sha256": file_sha256(snapshot),
                        "snapshot_bytes": snapshot.stat().st_size,
                        "block_rows": 1,
                    }
                ),
                encoding="utf-8",
            )

            verification = validate_snapshot_integrity(snapshot, metadata_path)
            self.assertEqual(verification["status"], "verified")
            self.assertNotIn("record_manifest", verification)
            self.assertNotIn("expected_source_record_rows", verification)

    def test_verified_custom_snapshot_remains_user_managed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-web-custom-snapshot-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            snapshot = tmp_path / "documents.csv"
            snapshot.write_bytes(DEFAULT_INPUT.read_bytes())
            metadata_path = tmp_path / "snapshot.json"
            metadata_path.write_text(
                json.dumps(
                    {
                        "snapshot": str(snapshot),
                        "snapshot_sha256": file_sha256(snapshot),
                        "snapshot_bytes": snapshot.stat().st_size,
                        "block_rows": 24,
                        "data_classification": "custom_review_data",
                    }
                ),
                encoding="utf-8",
            )
            output_dir = tmp_path / "output"
            self.run_example(
                output_dir,
                "--input",
                str(snapshot),
                "--snapshot-metadata",
                str(metadata_path),
            )
            manifest = json.loads((output_dir / "manifest.json").read_text())
            self.assertEqual(
                manifest["source"]["snapshot_verification"]["status"],
                "verified",
            )
            self.assertEqual(
                manifest["source"]["redistribution_status"],
                "user_managed",
            )

    def test_workspace_relative_snapshot_paths_resolve_from_repo_root(self) -> None:
        workspace_root = REPO_ROOT / "workspace"
        workspace_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="vane-web-relative-", dir=workspace_root
        ) as tmp_dir:
            workspace_dir = Path(tmp_dir)
            input_path = workspace_dir / "snapshot.parquet"
            metadata_path = workspace_dir / "snapshot.json"
            input_path.write_bytes(b"workspace-relative snapshot")
            metadata_path.write_text(
                json.dumps(
                    {
                        "snapshot": str(input_path.relative_to(REPO_ROOT)),
                        "snapshot_sha256": file_sha256(input_path),
                        "snapshot_bytes": input_path.stat().st_size,
                        "block_rows": 1,
                        "data_classification": "third_party_crawled_content",
                    }
                ),
                encoding="utf-8",
            )

            verification = validate_snapshot_integrity(input_path, metadata_path)
            self.assertEqual(verification["status"], "verified")
            self.assertTrue(verification["third_party_crawled_content"])

    def test_snapshot_contract_rejects_a_record_manifest_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-web-snapshot-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            snapshot = tmp_path / "snapshot.parquet"
            record_manifest = tmp_path / "records.csv"
            metadata_path = tmp_path / "snapshot.json"
            snapshot.write_bytes(b"snapshot with provenance")
            record_manifest.write_text("record_id\nrecord-1\n", encoding="utf-8")
            metadata_path.write_text(
                json.dumps(
                    {
                        "snapshot": str(snapshot),
                        "snapshot_sha256": file_sha256(snapshot),
                        "snapshot_bytes": snapshot.stat().st_size,
                        "block_rows": 1,
                        "record_manifest": str(record_manifest),
                        "record_manifest_sha256": "0" * 64,
                        "source_record_rows": 1,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError, "record manifest SHA-256 mismatch"
            ):
                validate_snapshot_integrity(snapshot, metadata_path)

    def test_candidate_pair_budget_fails_before_hot_bucket_expansion(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-candidate-budget-") as tmp_dir:
            source = Path(tmp_dir) / "collision-buckets.parquet"
            pq.write_table(
                pa.table(
                    {
                        "member_count": [4, 3],
                        "pair_slots": [6, 3],
                    }
                ),
                source,
            )
            collision_buckets = self.relation_connection().read_parquet(str(source))
            self.assertEqual(
                validate_candidate_pair_budget(
                    collision_buckets,
                    max_candidate_pair_slots=9,
                ),
                9,
            )
            with self.assertRaisesRegex(RuntimeError, "exceeding.*8"):
                validate_candidate_pair_budget(
                    collision_buckets,
                    max_candidate_pair_slots=8,
                )

    def test_singleton_buckets_are_filtered_before_driver_collection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-collision-filter-") as tmp_dir:
            source = Path(tmp_dir) / "band-memberships.parquet"
            pq.write_table(
                pa.table(
                    {
                        "band_index": [0, 0, 1, 2, 3],
                        "lsh_band": ["shared", "shared", "a", "b", "c"],
                        "doc_id": ["left", "right", "one", "two", "three"],
                        "domain": [
                            "a.example",
                            "b.example",
                            "c.example",
                            "d.example",
                            "e.example",
                        ],
                    }
                ),
                source,
            )
            conn = self.relation_connection()
            conn.read_parquet(str(source)).create_view(
                "band_memberships", replace=True
            )

            rows = colliding_band_memberships_relation(conn).order(
                "band_index, lsh_band, doc_id"
            ).fetchall()

            self.assertEqual(
                rows,
                [
                    (0, "shared", "left", "a.example"),
                    (0, "shared", "right", "b.example"),
                ],
            )

    def test_cli_fails_closed_before_a_hot_bucket_self_join(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-web-hot-bucket-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            input_path = tmp_path / "documents.csv"
            with input_path.open("w", newline="", encoding="utf-8") as input_file:
                writer = csv.DictWriter(
                    input_file,
                    fieldnames=[
                        "doc_id",
                        "source",
                        "domain",
                        "crawled_at",
                        "title",
                        "body",
                    ],
                )
                writer.writeheader()
                for index in range(4):
                    writer.writerow(
                        {
                            "doc_id": f"duplicate-{index}",
                            "source": "fixture",
                            "domain": "example.com",
                            "crawled_at": "2026-07-01",
                            "title": "Repeated template",
                            "body": "The same repeated template body has enough tokens.",
                        }
                    )

            output_dir = tmp_path / "output"
            completed = self.invoke_example(
                output_dir,
                "--input",
                str(input_path),
                "--max-candidate-pair-slots",
                "10",
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn(
                "LSH candidate expansion would create 48 band-level pair slots",
                completed.stderr,
            )
            self.assertFalse(output_dir.exists())

    def test_lsh_probability_makes_the_recall_tradeoff_explicit(self) -> None:
        self.assertAlmostEqual(lsh_candidate_probability(0.7), 0.378122, places=6)
        self.assertEqual(lsh_candidate_probability(0.0), 0.0)
        self.assertEqual(lsh_candidate_probability(1.0), 1.0)
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            lsh_candidate_probability(1.1)

    def test_lsh_band_keys_include_band_position(self) -> None:
        original = lsh_band_keys([10, 20, 30, 40], rows_per_band=2)
        swapped = lsh_band_keys([30, 40, 10, 20], rows_per_band=2)
        self.assertEqual(len(original), 2)
        self.assertTrue(set(original).isdisjoint(swapped))
        with self.assertRaisesRegex(ValueError, "divisible by rows_per_band"):
            lsh_band_keys([10, 20, 30], rows_per_band=2)

    def test_band_expansion_covers_more_than_one_output_vector(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-band-expansion-") as tmp_dir:
            root = Path(tmp_dir)
            row_count = 300
            documents_path = root / "documents.parquet"
            fingerprinted_path = root / "fingerprinted.parquet"
            pq.write_table(
                pa.table(
                    {
                        "doc_id": [
                            f"doc-{index:03d}" for index in range(row_count)
                        ],
                        "domain": ["example.com"] * row_count,
                    }
                ),
                documents_path,
            )
            pq.write_table(
                pa.table(
                    {
                        "doc_id": [
                            f"doc-{index:03d}" for index in range(row_count)
                        ],
                        "shingle_count": [1] * row_count,
                        "lsh_bands": [
                            [f"{band:03d}:same" for band in range(LSH_BANDS)]
                            for _ in range(row_count)
                        ],
                    }
                ),
                fingerprinted_path,
            )
            conn = self.relation_connection()
            conn.read_parquet(str(documents_path)).create_view(
                "documents", replace=True
            )
            conn.read_parquet(str(fingerprinted_path)).create_view(
                "fingerprinted", replace=True
            )
            memberships = [
                row
                for relation in band_membership_relations(conn)
                for row in relation.project("doc_id").fetchall()
            ]
            self.assertEqual(len(memberships), row_count * LSH_BANDS)
            self.assertEqual(len({row[0] for row in memberships}), row_count)

    def test_normalize_text_is_unicode_aware_and_deterministic(self) -> None:
        self.assertEqual(
            normalize_text("  Café—RÉSUMÉ_数据  "),
            "cafe resume 数据",
        )

    def test_short_documents_preserve_token_order(self) -> None:
        left = shingles("alpha beta gamma delta")
        right = shingles("delta gamma beta alpha")
        self.assertEqual(left, {"alpha beta gamma delta"})
        self.assertEqual(right, {"delta gamma beta alpha"})
        self.assertEqual(jaccard(list(left), list(right)), 0.0)

    def test_cluster_ids_are_stable_when_unrelated_documents_are_added(self) -> None:
        def assignments(doc_ids: list[str]) -> dict[str, str]:
            with tempfile.TemporaryDirectory(prefix="vane-cluster-ids-") as tmp_dir:
                conn = self.relation_connection()
                workspace = RunnerWorkspace(Path(tmp_dir), conn)
                documents = workspace.stage_table(
                    "documents",
                    pa.table({"doc_id": doc_ids}),
                )
                documents.create_view("documents", replace=True)
                duplicate_pairs = workspace.stage_table(
                    "duplicate-pairs",
                    pa.table(
                        {
                            "left_doc_id": ["b", "c"],
                            "right_doc_id": ["c", "d"],
                        }
                    ),
                )
                duplicate_pairs.create_view("duplicate_pairs", replace=True)
                return {
                    doc_id: cluster_id
                    for cluster_id, doc_id in build_cluster_relation(
                        conn, workspace
                    ).project("cluster_id, doc_id").fetchall()
                }

        before = assignments(["b", "c", "d"])
        after = assignments(["a", "b", "c", "d"])
        self.assertEqual(before["b"], before["c"])
        self.assertEqual(before["c"], before["d"])
        self.assertEqual(after["b"], before["b"])
        self.assertEqual(after["c"], before["c"])
        self.assertEqual(after["d"], before["d"])

    def test_document_ids_must_be_unique(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-document-ids-") as tmp_dir:
            source = Path(tmp_dir) / "documents.parquet"
            pq.write_table(
                pa.table({"doc_id": ["duplicate", "duplicate"]}),
                source,
            )
            with self.assertRaisesRegex(ValueError, "must be unique"):
                validate_document_ids(
                    self.relation_connection().read_parquet(str(source))
                )

    def test_empty_text_does_not_enter_an_lsh_bucket(self) -> None:
        row = fingerprint_documents_batch(
            pa.table({"doc_id": ["empty"], "title": [None], "body": [None]})
        ).to_pylist()[0]
        self.assertEqual(row["token_count"], 0)
        self.assertEqual(row["signature"], [0] * MINHASH_VALUES)
        self.assertEqual(row["lsh_bands"], [])

    def test_pair_scoring_records_the_matching_signal(self) -> None:
        batch = pa.Table.from_pylist(
            [
                {
                    "left_doc_id": "lexical-left",
                    "right_doc_id": "lexical-right",
                    "left_domain": "docs.example.com",
                    "right_domain": "docs.example.com",
                    "shared_bands": 1,
                    "left_shingle_set": ["a", "b", "c"],
                    "right_shingle_set": ["a", "b", "c", "d"],
                    "left_signature": [1, 2, 3, 4],
                    "right_signature": [5, 6, 7, 8],
                },
                {
                    "left_doc_id": "minhash-left",
                    "right_doc_id": "minhash-right",
                    "left_domain": "docs.example.com",
                    "right_domain": "docs.example.com",
                    "shared_bands": 1,
                    "left_shingle_set": ["a"],
                    "right_shingle_set": ["z"],
                    "left_signature": [1, 2, 3, 4],
                    "right_signature": [1, 2, 3, 8],
                },
            ]
        )
        rows = score_pairs_batch(batch).to_pylist()
        self.assertEqual(
            [(row["reason"], row["is_duplicate"]) for row in rows],
            [("jaccard_match", True), ("minhash_only_rejected", False)],
        )

    def test_readmes_link_languages_and_current_entrypoint(self) -> None:
        english = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (REPO_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn("[简体中文](README.zh-CN.md)", english)
        self.assertIn("[English](README.md)", chinese)
        for text in (english, chinese):
            self.assertIn("src/web_text_deduplication.py", text)
            self.assertNotIn("examples/web_text_deduplication.py", text)


if __name__ == "__main__":
    unittest.main()
