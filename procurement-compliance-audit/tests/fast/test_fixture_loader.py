from __future__ import annotations

from collections import defaultdict
import csv
import json
from pathlib import Path
import shutil

import pytest

from procurement_audit_sql_demo import fixture_loader
from procurement_audit_sql_demo.config import load_runtime_config
from procurement_audit_sql_demo.fixture_loader import (
    FixtureContractError,
    build_fixture,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = PROJECT_ROOT / "fixtures/expert-score-anomaly"


def _winner(rows: list[dict]) -> str:
    totals: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        totals[row["supplier_id"]].append(float(row["score"]))
    return min(
        totals,
        key=lambda supplier_id: (
            -sum(totals[supplier_id]) / len(totals[supplier_id]),
            supplier_id,
        ),
    )


def test_fixture_has_exactly_four_business_files():
    assert sorted(path.name for path in FIXTURE_DIR.iterdir()) == [
        "committee_minutes.png",
        "expert_recommendation.png",
        "expert_scores.csv",
        "project.json",
    ]


def test_fixture_contract_and_expected_rank_reversal():
    bundle = build_fixture(FIXTURE_DIR)

    assert bundle.project.num_rows == 1
    assert bundle.suppliers.num_rows == 3
    assert bundle.scores.num_rows == 12
    assert bundle.evidence.num_rows == 2
    rows = bundle.scores.to_pylist()
    assert _winner(rows) == "SUP-JW-001"
    assert _winner([row for row in rows if row["expert_id"] != "EXP-001"]) == "SUP-ZJ-002"
    assert all(row["bucket"] == "procurement-compliance-audit-fixtures" for row in bundle.evidence.to_pylist())
    assert all(row["object_key"].startswith("procurement/PRJ-2026-001/") for row in bundle.evidence.to_pylist())
    assert len(bundle.objects) == 2
    assert all(item.value.startswith(b"\x89PNG") for item in bundle.objects)


def test_fixture_rejects_duplicate_expert_supplier_key(tmp_path):
    fixture_dir = tmp_path / "fixture"
    shutil.copytree(FIXTURE_DIR, fixture_dir)
    score_path = fixture_dir / "expert_scores.csv"
    rows = list(csv.DictReader(score_path.read_text(encoding="utf-8").splitlines()))
    rows.append(dict(rows[0]))
    with score_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(FixtureContractError, match="duplicate score key"):
        build_fixture(fixture_dir)


def test_fixture_rejects_invalid_object_key(tmp_path):
    fixture_dir = tmp_path / "fixture"
    shutil.copytree(FIXTURE_DIR, fixture_dir)
    project_path = fixture_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["evidence_files"][0]["object_key"] = "../outside.png"
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FixtureContractError, match="object_key is invalid"):
        build_fixture(fixture_dir)


def test_fixture_rejects_supplier_name_or_alias_owned_by_two_suppliers(tmp_path):
    fixture_dir = tmp_path / "fixture"
    shutil.copytree(FIXTURE_DIR, fixture_dir)
    project_path = fixture_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["suppliers"][1]["aliases"].append(" 景维 ")
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FixtureContractError, match="supplier name or alias"):
        build_fixture(fixture_dir)


def test_load_fixture_seeds_postgres_and_minio_not_pipeline_files(monkeypatch):
    config = load_runtime_config(PROJECT_ROOT / "runtime.yml")
    events = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Store:
        def __init__(self, minio_config):
            assert minio_config is config.minio

        def probe(self):
            events.append("minio:probe")

        def ensure_bucket(self, bucket):
            events.append(f"minio:bucket:{bucket}")

        def remove_prefix(self, bucket, prefix):
            events.append(f"minio:remove:{bucket}/{prefix}")

        def put_bytes(self, bucket, object_key, value, content_type):
            assert value.startswith(b"\x89PNG")
            events.append(f"minio:put:{bucket}/{object_key}:{content_type}")

    monkeypatch.setattr(fixture_loader, "load_runtime_config", lambda _path: config)
    monkeypatch.setattr(fixture_loader, "connect_postgres", lambda _config: Connection())
    monkeypatch.setattr(
        fixture_loader,
        "initialize_schema",
        lambda _connection, pg_config: events.append(f"postgres:init:{pg_config.raw_schema}"),
    )

    def reset(_connection, _config, **rows):
        events.append(
            "postgres:reset:"
            f"{len(rows['projects'])}/{len(rows['suppliers'])}/"
            f"{len(rows['scores'])}/{len(rows['evidence'])}"
        )

    monkeypatch.setattr(fixture_loader, "reset_fixture_rows", reset)
    monkeypatch.setattr(fixture_loader, "MinioStore", Store)

    assert fixture_loader.load_fixture(
        PROJECT_ROOT / "runtime.yml",
        FIXTURE_DIR,
    ) == (1, 12, 2)
    assert events[:4] == [
        "postgres:init:procurement_audit_raw",
        "postgres:reset:1/3/12/2",
        "minio:probe",
        "minio:bucket:procurement-compliance-audit-fixtures",
    ]
    assert sum(event.startswith("minio:put:") for event in events) == 2
