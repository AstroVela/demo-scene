import json

import pyarrow as pa
import pytest

from fund_investment_research.ai import (
    _validate_role_semantics,
    build_prompt,
    extract_document_with_vane,
)
from fund_investment_research.config import AiConfig
from fund_investment_research.contracts import ContractError


def test_ai_prompt_passes_image_column_to_local_vane(monkeypatch):
    captured = {}

    def fake_prompt(relation, column, **kwargs):
        captured["relation"] = relation
        captured["column"] = column
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("fund_investment_research.ai.vane.ai.prompt", fake_prompt)
    response = {
        "observations": [
            {
                "fact_type": "metric",
                "metric_code": "CASH_RUNWAY",
                "value_numeric": 24,
                "value_text": None,
                "unit": "months",
                "source_quote": "cash runway of 24 months",
                "page": 1,
                "confidence": 0.99,
                "knowledge_kind": "source_fact",
                "review_required": False,
            }
        ],
        "impact_hypotheses": [
            {
                "metric_code": "CASH_RUNWAY",
                "condition_id": "COND-RUNWAY",
                "evidence_status": "supported",
                "rationale": "24 is above 18.",
                "confidence": 0.98,
            }
        ],
    }

    def relation_factory(name, table):
        captured["request_table"] = table
        return "relation"

    def materialize(relation, name):
        return pa.table({"raw_response": [json.dumps(response)]})

    result = extract_document_with_vane(
        source_role="audited_financial_update",
        ocr_text="Cash runway: 24 months.",
        image_bytes=b"\x89PNG\r\n\x1a\n",
        ai_config=AiConfig(
            provider="openai",
            base_url="http://127.0.0.1:8001/v1",
            health_url="http://127.0.0.1:8001/health",
            api_key="dummy",
            model="Qwen2.5-VL-3B-Instruct",
            concurrency=1,
            timeout_seconds=60,
            temperature=0,
            max_tokens=500,
        ),
        relation_factory=relation_factory,
        materialize=materialize,
        request_name="financial",
    )
    assert captured["column"] == "prompt_text"
    assert captured["image_columns"] == ["image_bytes"]
    assert captured["execution_backend"] == "ray_actor"
    assert captured["request_table"]["image_bytes"][0].as_py().startswith(b"\x89PNG")
    assert result["observations"][0]["metric_code"] == "CASH_RUNWAY"


def test_status_prompt_uses_textual_status_shape():
    prompt = build_prompt(
        "company_regulatory_update",
        "The BLA remains on schedule for Q4 2026.",
    )

    assert '"fact_type": "status"' in prompt
    assert '"value_numeric": null' in prompt
    assert '"value_text": "on_schedule_q4_2026"' in prompt
    assert '"unit": "status"' in prompt
    assert "never use a numeric placeholder" in prompt


def test_clinical_semantics_require_supporting_subgroup_edge():
    response = {
        "observations": [
            {"metric_code": "ORR"},
            {"metric_code": "TRAE_G3_PLUS"},
            {"metric_code": "SUBGROUP_ORR"},
        ],
        "impact_hypotheses": [
            {
                "metric_code": "ORR",
                "condition_id": "COND-EFFICACY",
                "evidence_status": "contradicted",
            },
            {
                "metric_code": "TRAE_G3_PLUS",
                "condition_id": "COND-SAFETY",
                "evidence_status": "contradicted",
            },
        ],
    }

    with pytest.raises(ContractError, match="SUBGROUP_ORR"):
        _validate_role_semantics(response, "company_clinical_announcement")
