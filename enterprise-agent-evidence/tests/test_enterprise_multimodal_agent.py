from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq
import vane

from src._common import batch_udf_options
from src.enterprise_multimodal_agent import (
    DEFAULT_SCENARIO_SNAPSHOT,
    REVIEW_QUEUE_ORDER,
    parse_args,
    verify_scenario_snapshot,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data" / "enterprise_multimodal_agent"
ASSET_DIR = DATA_DIR


def example_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("VANE_RUNNER", None)
    env["RAY_ADDRESS"] = "local"
    env["VANE_PROGRESS"] = "0"
    env["RAY_LOG_TO_DRIVER"] = "0"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return env


class EnterpriseMultimodalAgentTest(unittest.TestCase):
    def run_command(
        self,
        output_dir: Path,
        *extra_args: str,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "src" / "enterprise_multimodal_agent.py"),
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
        completed = self.run_command(output_dir, *extra_args)
        self.assertEqual(completed.returncode, 0, msg=completed.stdout + completed.stderr)

    def test_local_runner_override_is_rejected_before_writing_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-enterprise-local-") as tmp_dir:
            output_dir = Path(tmp_dir) / "enterprise_multimodal_agent"
            env = example_subprocess_env()
            env["VANE_RUNNER"] = "local"

            completed = self.run_command(output_dir, env=env)

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("requires Vane RayRunner", completed.stderr)
            self.assertFalse((output_dir / "manifest.json").exists())

    def test_default_artifact_contract_uses_real_multimodal_assets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-enterprise-context-") as tmp_dir:
            output_dir = Path(tmp_dir) / "enterprise_multimodal_agent"
            self.run_example(output_dir)

            manifest = json.loads((output_dir / "manifest.json").read_text())
            self.assertEqual(manifest["example"], "enterprise_multimodal_agent")
            self.assertEqual(manifest["source_mode"], "public_snapshot")
            self.assertEqual(manifest["scenario_mode"], "pinned_fixture")
            self.assertEqual(manifest["asset_source_mode"], "public_snapshot")
            self.assertTrue(manifest["scenario_snapshot_verified"])
            self.assertTrue(manifest["asset_snapshot_verified"])
            self.assertEqual(manifest["scenario_schema_version"], 1)
            self.assertEqual(manifest["output_schema_version"], 1)
            self.assertEqual(manifest["case_rows"], 4)
            self.assertEqual(manifest["requirement_rows"], 8)
            self.assertEqual(manifest["asset_rows"], 5)
            self.assertEqual(manifest["asset_feature_rows"], 5)
            self.assertEqual(manifest["input_rows"], 8)
            self.assertEqual(manifest["evidence_feature_rows"], 8)
            self.assertEqual(manifest["context_rows"], 4)
            self.assertEqual(manifest["gap_rows"], 1)
            self.assertEqual(manifest["conflict_rows"], 1)
            self.assertEqual(manifest["review_rows"], 3)
            self.assertEqual(manifest["runner"], "ray")
            self.assertEqual(manifest["requested_execution_backend"], "auto")
            self.assertEqual(manifest["execution_backend"], "ray_task")
            self.assertEqual(
                manifest["modalities"],
                ["audio", "document", "image", "text"],
            )
            self.assertEqual(
                manifest["source_files"],
                ["cases.csv", "evidence_links.csv", "requirements.csv"],
            )
            self.assertEqual(
                manifest["input_dir"],
                "data/enterprise_multimodal_agent",
            )
            self.assertEqual(
                manifest["asset_catalog"],
                "data/enterprise_multimodal_agent/asset_catalog.csv",
            )
            self.assertEqual(
                manifest["scenario_snapshot_metadata"],
                "data/enterprise_multimodal_agent/scenario_snapshot.json",
            )
            self.assertEqual(
                manifest["asset_snapshot_metadata"],
                "data/enterprise_multimodal_agent/asset_snapshot.json",
            )
            self.assertEqual(len(manifest["scenario_snapshot_metadata_sha256"]), 64)
            self.assertEqual(len(manifest["asset_snapshot_metadata_sha256"]), 64)
            self.assertNotIn(str(REPO_ROOT), json.dumps(manifest))

            snapshot = json.loads(DEFAULT_SCENARIO_SNAPSHOT.read_text())
            expected_files = {
                row["file"]: row for row in snapshot["files"]
            }
            self.assertEqual(
                [row["file"] for row in manifest["input_files"]],
                ["cases.csv", "evidence_links.csv", "requirements.csv"],
            )
            for row in manifest["input_files"]:
                expected = expected_files[row["file"]]
                self.assertEqual(row["sha256"], expected["sha256"])
                self.assertEqual(row["records"], expected["records"])
                self.assertEqual(row["columns"], expected["columns"])
            self.assertEqual(
                manifest["execution_backends"],
                {
                    "process_audio_asset": "ray_task",
                    "process_document_asset": "ray_task",
                    "process_image_asset": "ray_task",
                    "process_text_asset": "ray_task",
                },
            )

            context_table = pq.read_table(output_dir / "agent_context.parquet")
            asset_table = pq.read_table(output_dir / "asset_features.parquet")
            feature_table = pq.read_table(output_dir / "evidence_features.parquet")
            self.assertEqual(context_table.num_rows, 4)
            self.assertEqual(asset_table.num_rows, 5)
            self.assertEqual(feature_table.num_rows, 8)
            self.assertEqual(
                len({row["asset_id"] for row in asset_table.to_pylist()}),
                5,
            )
            self.assertTrue(
                pa.types.is_list(context_table.schema.field("modalities").type)
            )
            self.assertTrue(
                pa.types.is_list(context_table.schema.field("asset_ids").type)
            )
            self.assertTrue(
                pa.types.is_list(feature_table.schema.field("risk_flags").type)
            )
            self.assertTrue(
                pa.types.is_struct(feature_table.schema.field("media_metrics").type)
            )
            self.assertNotIn("context_embedding", context_table.column_names)

            contexts = {row["case_id"]: row for row in context_table.to_pylist()}
            self.assertEqual(contexts["case-arrow-docs"]["review_state"], "ready")
            self.assertEqual(
                contexts["case-arrow-docs"]["modalities"],
                ["document", "text"],
            )
            self.assertEqual(
                contexts["case-wikimedia-media"]["review_state"],
                "blocked",
            )
            self.assertEqual(contexts["case-wikimedia-media"]["conflict_count"], 1)
            self.assertEqual(
                contexts["case-wikimedia-media"]["rejected_asset_count"],
                1,
            )
            self.assertEqual(
                contexts["case-incomplete-bundle"]["missing_evidence_count"],
                1,
            )
            self.assertEqual(
                contexts["case-stale-docs"]["review_state"],
                "needs_review",
            )
            self.assertEqual(contexts["case-stale-docs"]["stale_evidence_count"], 1)

            features = {row["record_id"]: row for row in feature_table.to_pylist()}
            audio = features["wikimedia-audio-sample"]
            self.assertEqual(audio["media_metrics"]["sample_rate"], 48000)
            self.assertAlmostEqual(audio["media_metrics"]["duration_seconds"], 2.4)
            small_image = features["wikimedia-small-image"]
            self.assertEqual(small_image["media_metrics"]["width"], 136)
            self.assertEqual(small_image["media_metrics"]["height"], 168)
            self.assertEqual(small_image["asset_decision"], "rejected")
            self.assertEqual(
                small_image["risk_flags"],
                ["low_resolution", "asserted_blocker"],
            )
            self.assertIn("# Apache Arrow", features["arrow-docs-project"]["evidence_text"])
            for row in features.values():
                self.assertTrue(row["source_uri"].startswith("https://"))
                self.assertTrue(row["source_page_uri"].startswith("https://"))
                self.assertTrue(row["license_id"])
                self.assertTrue(row["license_uri"].startswith("https://"))
                self.assertEqual(len(row["content_sha256"]), 64)

            with (output_dir / "evidence_gaps.csv").open(
                newline="", encoding="utf-8"
            ) as file:
                gaps = list(csv.DictReader(file))
            with (output_dir / "evidence_conflicts.csv").open(
                newline="", encoding="utf-8"
            ) as file:
                conflicts = list(csv.DictReader(file))
            self.assertEqual(gaps[0]["case_id"], "case-incomplete-bundle")
            self.assertEqual(gaps[0]["missing_evidence_type"], "audio")
            self.assertEqual(conflicts[0]["case_id"], "case-wikimedia-media")
            self.assertEqual(conflicts[0]["claim_key"], "media_readiness")
            self.assertEqual(conflicts[0]["claim_values"], "blocked, ready")

            for output_file in manifest["output_files"]:
                self.assertTrue((output_dir / output_file).exists(), msg=output_file)

    def test_public_snapshot_links_and_hashes_are_auditable(self) -> None:
        snapshot = json.loads((ASSET_DIR / "asset_snapshot.json").read_text())
        snapshot_assets = {row["record_id"]: row for row in snapshot["assets"]}
        with (ASSET_DIR / "asset_catalog.csv").open(
            newline="", encoding="utf-8"
        ) as file:
            catalog = {row["record_id"]: row for row in csv.DictReader(file)}
        with (DATA_DIR / "evidence_links.csv").open(
            newline="", encoding="utf-8"
        ) as file:
            links = list(csv.DictReader(file))

        self.assertEqual(
            {row["asset_id"] for row in links},
            {
                "arrow-project-readme",
                "arrow-python-readme",
                "wikimedia-audio",
                "wikimedia-download-icon",
                "wikimedia-generic-file",
            },
        )
        for asset_id in {row["asset_id"] for row in links}:
            catalog_row = catalog[asset_id]
            asset_path = REPO_ROOT / catalog_row["content_path"]
            self.assertTrue(asset_path.is_file())
            actual_sha256 = hashlib.sha256(asset_path.read_bytes()).hexdigest()
            self.assertEqual(actual_sha256, catalog_row["expected_sha256"])
            self.assertEqual(actual_sha256, snapshot_assets[asset_id]["sha256"])

    def test_enterprise_assets_do_not_embed_the_training_data_example(self) -> None:
        self.assertFalse((REPO_ROOT / "data" / "multimodal_training_data").exists())
        self.assertFalse(
            (REPO_ROOT / "src" / "multimodal_training_data.py").exists()
        )
        self.assertTrue((REPO_ROOT / "src" / "_media.py").is_file())
        self.assertTrue((DATA_DIR / "assets").is_dir())
        self.assertTrue(
            (REPO_ROOT / "scripts" / "prepare_enterprise_agent_assets.py").is_file()
        )

    def test_scenario_snapshot_rejects_tampered_csv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-enterprise-snapshot-") as tmp_dir:
            input_dir = Path(tmp_dir) / "input"
            input_dir.mkdir()
            for source in DATA_DIR.iterdir():
                if source.is_file():
                    shutil.copy2(source, input_dir / source.name)

            cases_path = input_dir / "cases.csv"
            cases_path.write_text(
                cases_path.read_text(encoding="utf-8").replace(
                    "PUBLIC-ARROW",
                    "TAMPERED-ARROW",
                    1,
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "cases.csv sha256 mismatch"):
                verify_scenario_snapshot(
                    input_dir,
                    input_dir / "scenario_snapshot.json",
                )

    def test_invalid_asset_link_is_rejected_before_processing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-enterprise-invalid-") as tmp_dir:
            input_dir = Path(tmp_dir) / "input"
            input_dir.mkdir()
            shutil.copy2(DATA_DIR / "cases.csv", input_dir / "cases.csv")
            shutil.copy2(DATA_DIR / "requirements.csv", input_dir / "requirements.csv")
            links = (DATA_DIR / "evidence_links.csv").read_text(encoding="utf-8")
            (input_dir / "evidence_links.csv").write_text(
                links
                + "unknown-asset-link,case-arrow-docs,missing-asset,unknown,"
                + "2026-07-19,Unknown asset,source_readiness,ready\n",
                encoding="utf-8",
            )

            completed = self.run_command(
                Path(tmp_dir) / "output",
                "--input-dir",
                str(input_dir),
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(
                "evidence with unknown asset (1)",
                completed.stdout + completed.stderr,
            )

    def test_invalid_governance_fields_are_rejected_before_processing(self) -> None:
        variants = [
            (
                "unsupported requirement",
                "requirements.csv",
                "case-stale-docs,text",
                "case-stale-docs,video",
                "unsupported requirement modality (1)",
            ),
            (
                "missing review date",
                "cases.csv",
                "case-stale-docs,PUBLIC-ARROW,Does the documentation evidence need a freshness review?,2026-07-20",
                "case-stale-docs,PUBLIC-ARROW,Does the documentation evidence need a freshness review?,",
                "case with invalid review date (1)",
            ),
            (
                "future evidence",
                "evidence_links.csv",
                "stale-python-doc,case-stale-docs,arrow-python-readme,github_release,2026-05-01",
                "stale-python-doc,case-stale-docs,arrow-python-readme,github_release,2026-07-21",
                "evidence observed after review date (1)",
            ),
        ]
        for label, file_name, old, new, expected_error in variants:
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                prefix="vane-enterprise-invalid-governance-"
            ) as tmp_dir:
                input_dir = Path(tmp_dir) / "input"
                input_dir.mkdir()
                for name in ("cases.csv", "requirements.csv", "evidence_links.csv"):
                    shutil.copy2(DATA_DIR / name, input_dir / name)
                target = input_dir / file_name
                target.write_text(
                    target.read_text(encoding="utf-8").replace(old, new, 1),
                    encoding="utf-8",
                )

                completed = self.run_command(
                    Path(tmp_dir) / "output",
                    "--input-dir",
                    str(input_dir),
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn(expected_error, completed.stdout + completed.stderr)

    def test_unreferenced_catalog_asset_is_not_processed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-enterprise-catalog-") as tmp_dir:
            custom_catalog = Path(tmp_dir) / "asset_catalog.csv"
            with (ASSET_DIR / "asset_catalog.csv").open(
                newline="", encoding="utf-8"
            ) as input_file:
                reader = csv.DictReader(input_file)
                rows = list(reader)
                fieldnames = list(reader.fieldnames or [])
            unused = dict(rows[0])
            unused["record_id"] = "unused-public-asset"
            with custom_catalog.open("w", newline="", encoding="utf-8") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows([*rows, unused])

            output_dir = Path(tmp_dir) / "output"
            self.run_example(
                output_dir,
                "--asset-catalog",
                str(custom_catalog),
            )
            manifest = json.loads((output_dir / "manifest.json").read_text())
            self.assertEqual(manifest["source_mode"], "custom_inputs")
            self.assertEqual(manifest["asset_source_mode"], "custom_asset_catalog")
            self.assertFalse(manifest["asset_snapshot_verified"])
            self.assertEqual(manifest["asset_rows"], 5)
            self.assertEqual(manifest["asset_feature_rows"], 5)
            asset_ids = {
                row["asset_id"]
                for row in pq.read_table(output_dir / "asset_features.parquet").to_pylist()
            }
            self.assertNotIn("unused-public-asset", asset_ids)

    def test_blocked_context_is_ordered_before_needs_review(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-review-order-") as tmp_dir:
            source = Path(tmp_dir) / "review-order.parquet"
            pq.write_table(
                pa.table(
                    {
                        "case_id": ["review", "blocked"],
                        "review_state": ["needs_review", "blocked"],
                    }
                ),
                source,
            )
            ordered = (
                vane.connect()
                .read_parquet(str(source))
                .order(REVIEW_QUEUE_ORDER)
                .project("case_id")
                .fetchall()
            )
            self.assertEqual(ordered, [("blocked",), ("review",)])

    def test_subprocess_backend_records_all_modality_stages(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-enterprise-context-") as tmp_dir:
            output_dir = Path(tmp_dir) / "enterprise_multimodal_agent"
            self.run_example(output_dir, "--execution-backend", "subprocess_task")
            manifest = json.loads((output_dir / "manifest.json").read_text())
            self.assertEqual(manifest["execution_backend"], "subprocess_task")
            self.assertEqual(
                manifest["execution_backends"],
                {
                    "process_audio_asset": "subprocess_task",
                    "process_document_asset": "subprocess_task",
                    "process_image_asset": "subprocess_task",
                    "process_text_asset": "subprocess_task",
                },
            )

    def test_ray_task_backend_is_accepted_and_forwarded(self) -> None:
        with patch.object(
            sys,
            "argv",
            ["enterprise_multimodal_agent.py", "--execution-backend", "ray_task"],
        ):
            args = parse_args()

        self.assertEqual(args.execution_backend, "ray_task")
        self.assertEqual(
            batch_udf_options("ray_task"),
            {"execution_backend": "ray_task"},
        )

    def test_readmes_link_languages_and_current_entrypoint(self) -> None:
        english = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (REPO_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn("[简体中文](README.zh-CN.md)", english)
        self.assertIn("[English](README.md)", chinese)
        for text in (english, chinese):
            self.assertIn("src/enterprise_multimodal_agent.py", text)
            self.assertNotIn("examples/enterprise_multimodal_agent.py", text)


if __name__ == "__main__":
    unittest.main()
