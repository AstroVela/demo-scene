from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from examples.multimodal_training_data import (
    decode_payload,
    process_audio,
    process_document,
    process_image,
    process_text,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class MultimodalTrainingDataTest(unittest.TestCase):
    def run_example(self, output_dir: Path, *extra_args: str) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "examples" / "multimodal_training_data.py"),
                "--output-dir",
                str(output_dir),
                *extra_args,
            ],
            cwd=REPO_ROOT,
            env={**os.environ, "VANE_RUNNER": ""},
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stdout + completed.stderr)

    def test_default_run_writes_release_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-multimodal-") as tmp_dir:
            output_dir = Path(tmp_dir) / "multimodal_training_data"
            self.run_example(output_dir)

            manifest = json.loads((output_dir / "manifest.json").read_text())
            self.assertEqual(manifest["raw_record_rows"], 5)
            self.assertEqual(manifest["feature_record_rows"], 5)
            self.assertEqual(manifest["release_rows"], 4)
            self.assertEqual(manifest["rejected_rows"], 1)
            self.assertEqual(manifest["modality_summary_rows"], 4)
            self.assertEqual(manifest["source_mode"], "public_snapshot")
            self.assertTrue(manifest["public_source_manifest"].endswith("public_sources.csv"))
            self.assertTrue(
                manifest["public_snapshot_metadata"].endswith("public_snapshot.json")
            )
            self.assertTrue(manifest["public_snapshot_verified"])
            verification = manifest["snapshot_verification"]
            self.assertEqual(verification["status"], "verified")
            self.assertEqual(verification["asset_rows"], 5)
            self.assertEqual(len(verification["metadata_sha256"]), 64)
            self.assertEqual(len(verification["training_manifest_sha256"]), 64)
            self.assertEqual(len(verification["source_manifest_sha256"]), 64)
            self.assertEqual(
                verification["training_manifest"],
                "data/multimodal_training_data/training_assets.csv",
            )
            self.assertNotIn(str(REPO_ROOT), json.dumps(manifest, sort_keys=True))
            self.assertEqual(
                manifest["modalities"], ["audio", "document", "image", "text"]
            )
            self.assertEqual(manifest["feature_schema_version"], 2)
            self.assertEqual(
                manifest["execution_backends"],
                {
                    "process_audio": None,
                    "process_document": None,
                    "process_image": None,
                    "process_text": None,
                },
            )
            self.assertNotIn("embedding", manifest)
            self.assertNotIn("query", manifest)

            feature_table = pq.read_table(output_dir / "feature_records.parquet")
            release_table = pq.read_table(output_dir / "training_release.parquet")
            self.assertEqual(feature_table.num_rows, 5)
            self.assertEqual(release_table.num_rows, 4)
            self.assertTrue(pa.types.is_list(feature_table.schema.field("risk_flags").type))
            self.assertTrue(pa.types.is_struct(feature_table.schema.field("media_metrics").type))

            release_ids = {row["record_id"] for row in release_table.to_pylist()}
            self.assertEqual(
                release_ids,
                {
                    "arrow-project-readme",
                    "arrow-python-readme",
                    "wikimedia-audio",
                    "wikimedia-generic-file",
                },
            )
            feature_by_id = {row["record_id"]: row for row in feature_table.to_pylist()}
            self.assertEqual(
                feature_by_id["wikimedia-generic-file"]["media_metrics"]["width"],
                512,
            )
            self.assertEqual(
                feature_by_id["wikimedia-audio"]["media_metrics"]["sample_rate"],
                48000,
            )
            self.assertEqual(
                feature_by_id["wikimedia-audio"]["media_metrics"]["duration_seconds"],
                2.4,
            )
            self.assertEqual(
                feature_by_id["arrow-project-readme"]["content_sha256"],
                "8a7a539f7d2162b3bd344326f8f69288659dab8cd09c1bac0c469ff9fc27e1a4",
            )

            with (output_dir / "rejected_records.csv").open(
                newline="", encoding="utf-8"
            ) as rejected_file:
                rejected = {row["record_id"]: row for row in csv.DictReader(rejected_file)}
            self.assertEqual(set(rejected), {"wikimedia-download-icon"})
            self.assertIn(
                "low_resolution",
                rejected["wikimedia-download-icon"]["risk_flags"],
            )

            for output_file in manifest["output_files"]:
                self.assertTrue((output_dir / output_file).exists(), output_file)

    def test_synthetic_fixture_preserves_failure_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-multimodal-") as tmp_dir:
            output_dir = Path(tmp_dir) / "multimodal_training_data"
            fixture = (
                REPO_ROOT
                / "data"
                / "multimodal_training_data"
                / "synthetic_training_assets.csv"
            )
            self.run_example(output_dir, "--input", str(fixture))

            manifest = json.loads((output_dir / "manifest.json").read_text())
            self.assertEqual(manifest["source_mode"], "synthetic_fixture")
            self.assertEqual(manifest["raw_record_rows"], 6)
            self.assertEqual(manifest["release_rows"], 4)
            self.assertEqual(manifest["rejected_rows"], 2)
            self.assertFalse(manifest["public_snapshot_verified"])
            self.assertEqual(
                manifest["snapshot_verification"], {"status": "not_applicable"}
            )

    def test_public_snapshot_sources_and_hashes_are_auditable(self) -> None:
        data_dir = REPO_ROOT / "data" / "multimodal_training_data"
        source_manifest = data_dir / "public_sources.csv"
        training_manifest = data_dir / "training_assets.csv"
        metadata = json.loads((data_dir / "public_snapshot.json").read_text())

        self.assertEqual(metadata["records"], 5)
        self.assertEqual(metadata["modalities"], ["audio", "document", "image", "text"])
        self.assertEqual(
            metadata["source_manifest_sha256"],
            hashlib.sha256(source_manifest.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            metadata["training_manifest_sha256"],
            hashlib.sha256(training_manifest.read_bytes()).hexdigest(),
        )

        with source_manifest.open(newline="", encoding="utf-8") as source_file:
            sources = {row["record_id"]: row for row in csv.DictReader(source_file)}
        with training_manifest.open(newline="", encoding="utf-8") as input_file:
            training_rows = {row["record_id"]: row for row in csv.DictReader(input_file)}

        self.assertEqual(set(sources), set(training_rows))
        for asset in metadata["assets"]:
            with self.subTest(record_id=asset["record_id"]):
                source = sources[asset["record_id"]]
                training_row = training_rows[asset["record_id"]]
                snapshot_path = REPO_ROOT / asset["snapshot_path"]
                self.assertTrue(source["source_uri"].startswith("https://"))
                self.assertTrue(source["source_page_uri"].startswith("https://"))
                self.assertTrue(source["license_uri"].startswith("https://"))
                self.assertTrue(source["license_id"])
                self.assertTrue(snapshot_path.is_file())
                self.assertEqual(snapshot_path.stat().st_size, asset["byte_size"])
                self.assertEqual(
                    hashlib.sha256(snapshot_path.read_bytes()).hexdigest(),
                    asset["sha256"],
                )
                self.assertEqual(training_row["expected_sha256"], asset["sha256"])
                self.assertEqual(training_row["content_path"], asset["snapshot_path"])

    def test_payload_hash_mismatch_fails_before_feature_extraction(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-multimodal-") as tmp_dir:
            payload_path = Path(tmp_dir) / "payload.txt"
            payload_path.write_text("real public payload", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "payload SHA-256 mismatch"):
                decode_payload(
                    {
                        "record_id": "hash-mismatch",
                        "content_path": str(payload_path),
                        "expected_sha256": "0" * 64,
                    }
                )

    def test_default_run_rejects_tampered_catalog_lineage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-multimodal-snapshot-") as tmp_dir:
            copied_root = Path(tmp_dir) / "multimodal-training-data"
            shutil.copytree(REPO_ROOT / "examples", copied_root / "examples")
            shutil.copytree(REPO_ROOT / "data", copied_root / "data")
            catalog_path = (
                copied_root
                / "data"
                / "multimodal_training_data"
                / "training_assets.csv"
            )
            with catalog_path.open(newline="", encoding="utf-8") as input_file:
                reader = csv.DictReader(input_file)
                rows = list(reader)
                fieldnames = list(reader.fieldnames or [])
            rows[0]["source_uri"] = "https://forged.example/not-the-recorded-source"
            rows[0]["license_id"] = "FORGED-LICENSE"
            with catalog_path.open("w", newline="", encoding="utf-8") as output_file:
                writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(copied_root / "examples" / "multimodal_training_data.py"),
                    "--output-dir",
                    str(copied_root / "output"),
                ],
                cwd=copied_root,
                env={**os.environ, "VANE_RUNNER": ""},
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(
                "training manifest SHA-256 mismatch",
                completed.stdout + completed.stderr,
            )

    def test_invalid_utf8_is_rejected_by_text_processors(self) -> None:
        base = {
            "record_id": "invalid-utf8",
            "source_uri": "sample://invalid-utf8",
            "license_id": "CC0-1.0",
            "split": "train",
            "mime_type": "text/plain",
            "text": "",
            "content_path": "",
            "expected_sha256": "",
            "metadata_json": "{}",
        }
        cases = [
            (process_document, b"\xff\xfe valid looking document body"),
            (process_text, b"\xff alpha beta gamma delta epsilon"),
        ]
        for processor, payload in cases:
            with self.subTest(processor=processor.__name__):
                result = processor(
                    {
                        **base,
                        "modality": "document"
                        if processor is process_document
                        else "text",
                        "content_base64": base64.b64encode(payload).decode("ascii"),
                    }
                )
                self.assertEqual(result["decision"], "rejected")
                self.assertIn("invalid_utf8", result["risk_flags"])

    def test_zero_sample_rate_wav_is_rejected_as_invalid_audio(self) -> None:
        fmt_chunk = struct.pack("<HHIIHH", 1, 1, 0, 0, 2, 16)
        payload = (
            b"RIFF"
            + struct.pack("<I", 4 + 8 + len(fmt_chunk) + 8)
            + b"WAVEfmt "
            + struct.pack("<I", len(fmt_chunk))
            + fmt_chunk
            + b"data"
            + struct.pack("<I", 0)
        )
        result = process_audio(
            {
                "record_id": "zero-sample-rate",
                "modality": "audio",
                "source_uri": "sample://zero-sample-rate",
                "license_id": "CC0-1.0",
                "split": "train",
                "mime_type": "audio/wav",
                "text": "",
                "content_path": "",
                "expected_sha256": "",
                "content_base64": base64.b64encode(payload).decode("ascii"),
                "metadata_json": "{}",
            }
        )
        self.assertEqual(result["decision"], "rejected")
        self.assertEqual(result["risk_flags"], ["invalid_audio"])

    def test_modality_processors_apply_different_quality_rules(self) -> None:
        base = {
            "record_id": "record",
            "source_uri": "sample://record",
            "license_id": "CC-BY-4.0",
            "split": "train",
            "metadata_json": "{}",
            "content_base64": "",
        }
        image = process_image(
            {
                **base,
                "modality": "image",
                "mime_type": "image/png",
                "text": "broken image",
                "content_base64": "bm90LWFuLWltYWdl",
            }
        )
        text = process_text(
            {
                **base,
                "modality": "text",
                "mime_type": "text/plain",
                "text": "too short",
            }
        )

        self.assertEqual(image["decision"], "rejected")
        self.assertEqual(image["risk_flags"], ["invalid_image"])
        self.assertEqual(text["decision"], "rejected")
        self.assertEqual(text["risk_flags"], ["text_too_short"])

    def test_subprocess_backend_is_recorded_for_each_modality(self) -> None:
        with tempfile.TemporaryDirectory(prefix="vane-multimodal-") as tmp_dir:
            output_dir = Path(tmp_dir) / "multimodal_training_data"
            self.run_example(output_dir, "--execution-backend", "subprocess_task")
            manifest = json.loads((output_dir / "manifest.json").read_text())
            self.assertEqual(manifest["execution_backend"], "subprocess_task")
            self.assertEqual(
                set(manifest["execution_backends"].values()), {"subprocess_task"}
            )

    def test_cli_rejects_missing_input_and_invalid_batch_size(self) -> None:
        cases = [
            (["--input", "/missing/training-assets.csv"], "does not exist"),
            (["--batch-size", "0"], "value must be greater than zero"),
        ]
        for extra_args, expected_error in cases:
            with self.subTest(arguments=extra_args):
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(REPO_ROOT / "examples" / "multimodal_training_data.py"),
                        *extra_args,
                    ],
                    cwd=REPO_ROOT,
                    env={**os.environ, "VANE_RUNNER": ""},
                    text=True,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(completed.returncode, 2)
                self.assertIn(expected_error, completed.stderr)

    def test_script_and_docs_keep_the_training_release_boundary(self) -> None:
        paths = [
            REPO_ROOT / "examples" / "multimodal_training_data.py",
            REPO_ROOT / "docs" / "multimodal_training_data.en.md",
            REPO_ROOT / "docs" / "multimodal_training_data.zh-CN.md",
        ]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertIn(".read_csv(", text, msg=str(path))
            self.assertIn(".map_batches(", text, msg=str(path))
            self.assertIn(".union(", text, msg=str(path))
            self.assertIn(".write_csv(", text, msg=str(path))
            self.assertIn(".write_parquet(", text, msg=str(path))
            self.assertIn("training_release", text, msg=str(path))
            self.assertIn("rejected_records", text, msg=str(path))
            self.assertNotIn("stable_embedding", text, msg=str(path))
            self.assertNotIn("semantic_matches", text, msg=str(path))
            self.assertNotIn("query_embedding", text, msg=str(path))
            self.assertNotIn('conn.register("raw_records"', text, msg=str(path))

    def test_docs_explain_public_snapshot_and_synthetic_modes(self) -> None:
        docs = [
            REPO_ROOT / "docs" / "multimodal_training_data.en.md",
            REPO_ROOT / "docs" / "multimodal_training_data.zh-CN.md",
        ]
        required_terms = [
            "public_sources.csv",
            "public_snapshot.json",
            "prepare_multimodal_training_data.py --refresh",
            "synthetic_training_assets.csv",
            "expected_sha256",
            "source_mode",
        ]
        for path in docs:
            text = path.read_text(encoding="utf-8")
            for term in required_terms:
                self.assertIn(term, text, msg=f"{path}: {term}")


if __name__ == "__main__":
    unittest.main()
