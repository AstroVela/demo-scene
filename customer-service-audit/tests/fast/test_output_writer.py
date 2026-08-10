from __future__ import annotations

import pytest

from customer_service_audit.output_writer import (
    OutputContractError,
    validate_rows,
)


def _full_row(call_id: str = "CALL-REFUND-ANGRY") -> dict:
    return {
        "call_id": call_id,
        "bucket": "customer-service-audit-fixtures",
        "object_key": f"recordings/{call_id}.wav",
        "object_sha256": "a" * 64,
        "probe_status": "success",
        "audio_usable": True,
        "duration_seconds": 12.5,
        "channels": 1,
        "sample_rate": 16000,
        "quality_reasons_json": "[]",
        "asr_status": "success",
        "transcript_text": "客户要求退款。",
        "transcript_usable": True,
        "text_length": 8,
        "language_confidence": 0.98,
        "transcript_failure_reasons_json": "[]",
        "analysis_status": "success",
        "problem_category": "refund_request",
        "customer_sentiment": "very_negative",
        "sentiment_score": -0.9,
        "urgency": "high",
        "key_issues_json": '["延迟发货", "要求退款"]',
        "customer_request": "立即退款",
        "resolution_status": "partially_resolved",
        "requires_followup": True,
        "agent_attitude": "professional",
        "summary": "客户因长时间未收到货要求退款。",
        "uncertainty_reasons_json": "[]",
        "confidence": 0.92,
        "review_disposition": "audited",
        "run_started_at": "2026-08-02T00:00:00+00:00",
        "asr_engine": "faster-whisper",
        "asr_model": "small",
        "ai_provider": "openai",
        "ai_model": "Qwen2.5-VL-3B-Instruct",
    }


def test_validate_accepts_a_complete_row() -> None:
    normalized = validate_rows([_full_row()])
    assert len(normalized) == 1
    assert normalized[0]["call_id"] == "CALL-REFUND-ANGRY"
    assert normalized[0]["analysis"]["problem_category"] == "refund_request"
    assert normalized[0]["audio"]["audio_usable"] is True
    assert normalized[0]["review_disposition"] == "audited"


def test_validate_rejects_missing_column() -> None:
    row = _full_row()
    del row["review_disposition"]
    with pytest.raises(OutputContractError):
        validate_rows([row])


def test_validate_rejects_invalid_review_disposition() -> None:
    row = _full_row()
    row["review_disposition"] = "approved_for_payment"
    with pytest.raises(OutputContractError):
        validate_rows([row])


def test_validate_rejects_out_of_range_score() -> None:
    row = _full_row()
    row["sentiment_score"] = 1.5
    with pytest.raises(OutputContractError):
        validate_rows([row])


def test_validate_rejects_duplicate_call_id() -> None:
    with pytest.raises(OutputContractError):
        validate_rows([_full_row(), _full_row()])
