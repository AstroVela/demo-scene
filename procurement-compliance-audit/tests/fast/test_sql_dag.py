from __future__ import annotations

import json
from pathlib import Path
import re

import pyarrow as pa
import vane

from procurement_audit_sql_demo.fixture_loader import build_fixture
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
INTERNAL_RELATIONS = {
    "int_evidence_ocr_udf",
    "int_conflict_validation_inputs",
    "int_conflict_validation_udf",
}
SQL_ORDER = (
    "staging/stg_scores.sql",
    "staging/stg_evidence_images.sql",
    "intermediate/int_evidence_ocr_udf.sql",
    "intermediate/int_evidence_ocr.sql",
    "intermediate/int_conflict_validation_inputs.sql",
    "intermediate/int_conflict_validation_udf.sql",
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
    fixture = build_fixture(FIXTURE_DIR)
    connection = vane.connect()
    _register_table(connection, "input_project", fixture.project)
    _register_table(connection, "input_suppliers", fixture.suppliers)
    _register_table(connection, "input_scores", fixture.scores)
    _register_table(connection, "input_evidence", fixture.evidence)
    vane.attach_function(
        lambda _bucket, _object_key: stable_json(
            {
                "status": "success",
                "full_text": "fixture OCR text",
                "mean_confidence": 0.95,
                "text_line_count": 1,
                "error": None,
            }
        ),
        alias="evidence_ocr_json",
        connection=connection,
        parameters=["VARCHAR", "VARCHAR"],
        return_dtype="VARCHAR",
    )
    vane.attach_function(
        validate_audit_fact_json,
        alias="validate_audit_fact_json",
        connection=connection,
        parameters=["VARCHAR"],
        return_dtype="VARCHAR",
    )
    for relative_path in SQL_ORDER[:4]:
        connection.execute((SQL_ROOT / relative_path).read_text(encoding="utf-8"))
    _register_table(
        connection,
        "int_evidence_ai",
        _raw_ai_rows(confidence, swap_document_roles=swap_document_roles),
    )
    for relative_path in SQL_ORDER[4:]:
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

    assert len(sql_files) == 10
    assert discovered == (CORE_RELATIONS - {"int_evidence_ai"}) | INTERNAL_RELATIONS


def test_evidence_ocr_udf_stage_is_a_direct_runner_projection():
    udf_statement = (SQL_ROOT / "intermediate/int_evidence_ocr_udf.sql").read_text(
        encoding="utf-8"
    )
    normalized_statement = (SQL_ROOT / "intermediate/int_evidence_ocr.sql").read_text(
        encoding="utf-8"
    )

    assert udf_statement.count("evidence_ocr_json(") == 1
    assert "json_extract" not in udf_statement
    assert "from int_evidence_ocr_udf" in normalized_statement
    assert "evidence_ocr_json(" not in normalized_statement


def test_conflict_fact_stage_owns_validation_and_role_filtering():
    input_statement = (
        SQL_ROOT / "intermediate/int_conflict_validation_inputs.sql"
    ).read_text(encoding="utf-8")
    udf_statement = (
        SQL_ROOT / "intermediate/int_conflict_validation_udf.sql"
    ).read_text(encoding="utf-8")
    fact_statement = (SQL_ROOT / "intermediate/int_conflict_facts.sql").read_text(
        encoding="utf-8"
    )

    assert "inner join stg_evidence_images" in input_statement
    assert udf_statement.count("validate_audit_fact_json(raw_response)") == 1
    assert "json_extract" not in udf_statement
    assert "from int_conflict_validation_udf" in fact_statement
    assert "role = 'expert_recommendation'" in fact_statement
    assert "role = 'committee_minutes'" in fact_statement


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
