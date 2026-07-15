from __future__ import annotations

import json
from pathlib import Path
import re

import duckdb
import pyarrow as pa

from procurement_audit_sql_demo.fixture_loader import load_fixture
from procurement_audit_sql_demo.vane_functions import (
    stable_json,
    validate_audit_fact_json,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = PROJECT_ROOT / "fixtures/expert-score-anomaly"
SQL_ROOT = PROJECT_ROOT / "src/procurement_audit_sql_demo/sql"
CORE_RELATIONS = {
    "stg_scores",
    "stg_evidence_images",
    "int_evidence_ocr",
    "int_evidence_ai",
    "int_conflict_facts",
    "int_score_metrics",
    "audit_findings",
    "audit_summary",
}
SQL_ORDER = (
    "staging/stg_scores.sql",
    "staging/stg_evidence_images.sql",
    "intermediate/int_evidence_ocr.sql",
    "intermediate/int_conflict_facts.sql",
    "intermediate/int_score_metrics.sql",
    "marts/audit_findings.sql",
    "marts/audit_summary.sql",
)


def _register_table(connection, name: str, table: pa.Table) -> None:
    temporary = f"__{name}_arrow"
    connection.register(temporary, table)
    try:
        connection.execute(f"create or replace table {name} as select * from {temporary}")
    finally:
        connection.unregister(temporary)


def _raw_ai_rows(
    confidence: float = 0.96,
    *,
    swap_document_roles: bool = False,
) -> pa.Table:
    recommendation_file_id = "EVD-MIN-001" if swap_document_roles else "EVD-REC-001"
    minutes_file_id = "EVD-REC-001" if swap_document_roles else "EVD-MIN-001"
    return pa.Table.from_pylist(
        [
            {
                "project_id": "PRJ-2026-001",
                "file_id": recommendation_file_id,
                "raw_response": stable_json(
                    {
                        "document_type": "recommendation_record",
                        "expert_id": "EXP-001",
                        "supplier_name": "景维自动化有限公司",
                        "recommended": True,
                        "participated": None,
                        "recused": None,
                        "evidence_quote": "推荐供应商：景维自动化有限公司",
                        "confidence": confidence,
                    }
                ),
            },
            {
                "project_id": "PRJ-2026-001",
                "file_id": minutes_file_id,
                "raw_response": stable_json(
                    {
                        "document_type": "committee_minutes",
                        "expert_id": "EXP-001",
                        "supplier_name": None,
                        "recommended": None,
                        "participated": True,
                        "recused": False,
                        "evidence_quote": "参加评审：是；是否回避：否",
                        "confidence": confidence,
                    }
                ),
            },
        ]
    )


def _relation_rows(connection, relation_name: str, order_by: str = "") -> list[dict]:
    suffix = f" order by {order_by}" if order_by else ""
    relation = connection.sql(f"select * from {relation_name}{suffix}")
    return [dict(zip(relation.columns, row)) for row in relation.fetchall()]


def _run_dag(
    confidence: float = 0.96,
    *,
    swap_document_roles: bool = False,
):
    fixture = load_fixture(FIXTURE_DIR)
    connection = duckdb.connect()
    _register_table(connection, "input_project", fixture.project)
    _register_table(connection, "input_suppliers", fixture.suppliers)
    _register_table(connection, "input_scores", fixture.scores)
    _register_table(connection, "input_evidence", fixture.evidence)
    connection.create_function(
        "evidence_ocr_json",
        lambda _path: stable_json(
            {
                "status": "success",
                "full_text": "fixture OCR text",
                "mean_confidence": 0.95,
                "text_line_count": 1,
                "error": None,
            }
        ),
        ["VARCHAR"],
        "VARCHAR",
    )
    connection.create_function(
        "validate_audit_fact_json",
        validate_audit_fact_json,
        ["VARCHAR"],
        "VARCHAR",
    )
    for relative_path in SQL_ORDER[:3]:
        connection.execute((SQL_ROOT / relative_path).read_text(encoding="utf-8"))
    _register_table(
        connection,
        "int_evidence_ai",
        _raw_ai_rows(confidence, swap_document_roles=swap_document_roles),
    )
    for relative_path in SQL_ORDER[3:]:
        connection.execute((SQL_ROOT / relative_path).read_text(encoding="utf-8"))
    return connection


def test_sql_dag_has_exactly_eight_core_relations():
    relation_pattern = re.compile(
        r"create\s+or\s+replace\s+(?:table|view)\s+([a-z_]+)",
        re.IGNORECASE,
    )
    sql_files = sorted(SQL_ROOT.rglob("*.sql"))
    discovered = {
        match.group(1).lower()
        for path in sql_files
        for match in relation_pattern.finditer(path.read_text(encoding="utf-8"))
    }

    assert len(sql_files) == 7
    assert discovered | {"int_evidence_ai"} == CORE_RELATIONS


def test_fixed_ai_facts_produce_three_linked_findings():
    connection = _run_dag()
    try:
        findings = _relation_rows(connection, "audit_findings", "rule_id")
        summary = _relation_rows(connection, "audit_summary")
        metrics = _relation_rows(connection, "int_score_metrics")
    finally:
        connection.close()

    assert [row["rule_id"] for row in findings] == [
        "EXP-001-conflict-not-recused",
        "EXP-002-score-bias",
        "EXP-003-award-impact",
    ]
    assert [row["severity"] for row in findings] == ["high", "medium", "high"]
    assert len({row["finding_id"] for row in findings}) == len(findings)
    assert len(metrics) == 1
    assert metrics[0]["score_delta"] == 18.0
    assert metrics[0]["computed_original_winner_supplier_id"] == "SUP-JW-001"
    assert metrics[0]["winner_without_flagged_expert"] == "SUP-ZJ-002"
    assert metrics[0]["award_changed"] is True
    assert summary == [
        {
            "project_id": "PRJ-2026-001",
            "title": "智能产线升级项目",
            "status": "review_required",
            "finding_count": 3,
            "high_severity_count": 2,
            "original_winner_supplier_id": "SUP-JW-001",
            "winner_without_flagged_expert": "SUP-ZJ-002",
            "flagged_expert_id": "EXP-001",
        }
    ]
    assert all(
        set(json.loads(row["evidence_file_ids_json"]))
        <= {"EVD-REC-001", "EVD-MIN-001"}
        for row in findings
    )


def test_low_ai_confidence_yields_insufficient_evidence_not_false_pass():
    connection = _run_dag(confidence=0.60)
    try:
        findings = _relation_rows(connection, "audit_findings")
        summary = _relation_rows(connection, "audit_summary")
    finally:
        connection.close()

    assert findings == []
    assert summary[0]["status"] == "insufficient_evidence"
    assert summary[0]["finding_count"] == 0


def test_swapped_valid_ai_documents_are_rejected_by_trusted_file_role():
    connection = _run_dag(swap_document_roles=True)
    try:
        conflict_facts = _relation_rows(connection, "int_conflict_facts")
        metrics = _relation_rows(connection, "int_score_metrics")
        findings = _relation_rows(connection, "audit_findings")
        summary = _relation_rows(connection, "audit_summary")
    finally:
        connection.close()

    assert conflict_facts == []
    assert metrics == []
    assert findings == []
    assert summary[0]["status"] == "insufficient_evidence"
