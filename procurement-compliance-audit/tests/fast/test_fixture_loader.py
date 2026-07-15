from __future__ import annotations

from collections import defaultdict
import csv
import json
from pathlib import Path
import shutil

import pytest

from procurement_audit_sql_demo.fixture_loader import (
    FixtureContractError,
    load_fixture,
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
    bundle = load_fixture(FIXTURE_DIR)

    assert bundle.project.num_rows == 1
    assert bundle.suppliers.num_rows == 3
    assert bundle.scores.num_rows == 12
    assert bundle.evidence.num_rows == 2
    rows = bundle.scores.to_pylist()
    assert _winner(rows) == "SUP-JW-001"
    assert _winner([row for row in rows if row["expert_id"] != "EXP-001"]) == "SUP-ZJ-002"
    assert all(Path(row["local_path"]).is_absolute() for row in bundle.evidence.to_pylist())


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
        load_fixture(fixture_dir)


def test_fixture_rejects_path_escape(tmp_path):
    fixture_dir = tmp_path / "fixture"
    shutil.copytree(FIXTURE_DIR, fixture_dir)
    project_path = fixture_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["evidence_files"][0]["local_path"] = "../outside.png"
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FixtureContractError, match="stay inside the fixture"):
        load_fixture(fixture_dir)


def test_fixture_rejects_supplier_name_or_alias_owned_by_two_suppliers(tmp_path):
    fixture_dir = tmp_path / "fixture"
    shutil.copytree(FIXTURE_DIR, fixture_dir)
    project_path = fixture_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["suppliers"][1]["aliases"].append(" 景维 ")
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FixtureContractError, match="supplier name or alias"):
        load_fixture(fixture_dir)
