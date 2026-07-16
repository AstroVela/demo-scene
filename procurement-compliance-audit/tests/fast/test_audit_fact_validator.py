from __future__ import annotations

import json

import pytest

from procurement_audit_sql_demo.vane_functions import (
    AuditFactContractError,
    validate_audit_fact_json,
)


VALID_RECOMMENDATION = {
    "document_type": "recommendation_record",
    "expert_id": " exp-001 ",
    "supplier_name": " 景维自动化有限公司 ",
    "recommended": True,
    "participated": None,
    "recused": None,
    "evidence_quote": "  推荐供应商：\n 景维自动化有限公司  ",
    "confidence": 0.96,
}
VALID_MINUTES = {
    "document_type": "committee_minutes",
    "expert_id": "EXP-001",
    "supplier_name": None,
    "recommended": None,
    "participated": True,
    "recused": False,
    "evidence_quote": "参加评审：是；是否回避：否",
    "confidence": 0.94,
}


def test_validator_returns_canonical_recommendation_json():
    result = json.loads(
        validate_audit_fact_json(json.dumps(VALID_RECOMMENDATION, ensure_ascii=False))
    )

    assert result == {
        "confidence": 0.96,
        "document_type": "recommendation_record",
        "evidence_quote": "推荐供应商： 景维自动化有限公司",
        "expert_id": "EXP-001",
        "participated": None,
        "recommended": True,
        "recused": None,
        "supplier_name": "景维自动化有限公司",
    }


def test_validator_accepts_minutes_nullable_contract():
    result = json.loads(
        validate_audit_fact_json(json.dumps(VALID_MINUTES, ensure_ascii=False))
    )

    assert result["document_type"] == "committee_minutes"
    assert result["participated"] is True
    assert result["recused"] is False
    assert result["supplier_name"] is None


def test_validator_accepts_one_complete_qwen_json_fence():
    raw = "```json\n" + json.dumps(VALID_MINUTES, ensure_ascii=False) + "\n```"

    result = json.loads(validate_audit_fact_json(raw))

    assert result["document_type"] == "committee_minutes"
    assert result["confidence"] == 0.94


def test_validator_rejects_prose_around_a_json_fence():
    raw = (
        "结果如下：\n```json\n"
        + json.dumps(VALID_MINUTES, ensure_ascii=False)
        + "\n```"
    )

    with pytest.raises(AuditFactContractError, match="valid JSON object"):
        validate_audit_fact_json(raw)


def test_validator_rejects_placeholder_evidence_quote():
    payload = json.dumps(
        {**VALID_MINUTES, "evidence_quote": "图片原文"},
        ensure_ascii=False,
    )

    with pytest.raises(AuditFactContractError, match="placeholder"):
        validate_audit_fact_json(payload)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            '{"document_type":"committee_minutes","document_type":"recommendation_record"}',
            "duplicate JSON key",
        ),
        (json.dumps({**VALID_MINUTES, "unknown": "value"}), "wrong fields"),
        (json.dumps({**VALID_MINUTES, "recused": "否"}), "recused must be boolean"),
        (json.dumps({**VALID_MINUTES, "confidence": True}), "confidence must be numeric"),
        (json.dumps({**VALID_MINUTES, "confidence": 1.1}), "between 0 and 1"),
        (json.dumps({**VALID_MINUTES, "expert_id": "expert one"}), "expert_id"),
        (json.dumps({**VALID_MINUTES, "supplier_name": "景维"}), "supplier_name must be null"),
        ("```json\n{}\n```", "wrong fields"),
    ],
)
def test_validator_rejects_non_contract_responses(payload, message):
    with pytest.raises(AuditFactContractError, match=message):
        validate_audit_fact_json(payload)


def test_validator_rejects_non_finite_json_number():
    payload = json.dumps({**VALID_MINUTES, "confidence": float("nan")})

    with pytest.raises(AuditFactContractError, match="invalid JSON constant"):
        validate_audit_fact_json(payload)
