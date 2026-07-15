from __future__ import annotations

import json

import pytest

from procurement_audit_sql_demo.output_writer import (
    OutputContractError,
    write_outputs,
)


VALID_FINDING = {
    "finding_id": "PRJ-2026-001:EXP-001-conflict-not-recused",
    "project_id": "PRJ-2026-001",
    "rule_id": "EXP-001-conflict-not-recused",
    "severity": "high",
    "subject_type": "expert",
    "subject_id": "EXP-001",
    "supplier_id": "SUP-JW-001",
    "metric_name": "recused",
    "metric_value": 0.0,
    "threshold_value": 1.0,
    "finding_summary": "专家曾推荐相关供应商，参加评审且未回避。",
    "evidence_file_ids_json": '["EVD-REC-001","EVD-MIN-001"]',
    "recommended_action": "暂停定标并复核专家回避义务。",
    "confidence": 0.96,
}
VALID_SUMMARY = {
    "project_id": "PRJ-2026-001",
    "title": "智能产线升级项目",
    "status": "review_required",
    "finding_count": 1,
    "high_severity_count": 1,
    "original_winner_supplier_id": "SUP-JW-001",
    "winner_without_flagged_expert": "SUP-ZJ-002",
    "flagged_expert_id": "EXP-001",
}
EVIDENCE_IDS = {"EVD-REC-001", "EVD-MIN-001"}


def test_output_writer_rejects_extra_columns(tmp_path):
    row = dict(VALID_SUMMARY, unexpected=True)

    with pytest.raises(OutputContractError, match="wrong columns"):
        write_outputs([VALID_FINDING], [row], tmp_path, EVIDENCE_IDS)


def test_output_writer_rejects_unknown_evidence_reference(tmp_path):
    finding = dict(VALID_FINDING, evidence_file_ids_json='["EVD-UNKNOWN"]')

    with pytest.raises(OutputContractError, match="unknown evidence"):
        write_outputs([finding], [VALID_SUMMARY], tmp_path, EVIDENCE_IDS)


def test_output_writer_validates_then_atomically_writes_two_jsonl_files(tmp_path):
    published = write_outputs(
        [VALID_FINDING],
        [VALID_SUMMARY],
        tmp_path,
        EVIDENCE_IDS,
    )

    assert published.finding_count == 1
    assert published.summary_count == 1
    assert published.findings_path.name == "audit_findings.jsonl"
    assert published.summary_path.name == "audit_summary.jsonl"
    assert json.loads(published.findings_path.read_text(encoding="utf-8")) == VALID_FINDING
    assert json.loads(published.summary_path.read_text(encoding="utf-8")) == VALID_SUMMARY
    assert not list(tmp_path.glob("*.tmp"))


def test_output_counts_must_match_summary(tmp_path):
    summary = dict(VALID_SUMMARY, finding_count=2)

    with pytest.raises(OutputContractError, match="finding_count"):
        write_outputs([VALID_FINDING], [summary], tmp_path, EVIDENCE_IDS)
