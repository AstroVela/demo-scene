import json

import pytest

from fund_investment_research.contracts import ContractError, validate_ai_response


def _response():
    return {
        "observations": [
            {
                "fact_type": "metric",
                "metric_code": "ORR",
                "value_numeric": 29,
                "value_text": None,
                "unit": "percent",
                "source_quote": "Overall ORR: 29%.",
                "page": 1,
                "confidence": 0.98,
                "knowledge_kind": "source_fact",
                "review_required": False,
            }
        ],
        "impact_hypotheses": [
            {
                "metric_code": "ORR",
                "condition_id": "COND-EFFICACY",
                "evidence_status": "contradicted",
                "rationale": "29% is below the approved threshold.",
                "confidence": 0.95,
            }
        ],
    }


def test_ai_contract_accepts_exact_schema():
    result = validate_ai_response(
        json.dumps(_response()),
        allowed_metrics={"ORR"},
        allowed_conditions={"COND-EFFICACY"},
    )
    assert result["observations"][0]["value_numeric"] == 29.0
    assert result["impact_hypotheses"][0]["evidence_status"] == "contradicted"


def test_ai_contract_rejects_untrusted_identity_field():
    value = _response()
    value["company_id"] = "MODEL-OVERRIDE"
    with pytest.raises(ContractError, match="wrong fields"):
        validate_ai_response(
            json.dumps(value),
            allowed_metrics={"ORR"},
            allowed_conditions={"COND-EFFICACY"},
        )


def test_ai_contract_rejects_impact_without_fact():
    value = _response()
    value["impact_hypotheses"][0]["metric_code"] = "TRAE_G3_PLUS"
    with pytest.raises(ContractError, match="must reference an observation"):
        validate_ai_response(
            json.dumps(value),
            allowed_metrics={"ORR", "TRAE_G3_PLUS"},
            allowed_conditions={"COND-EFFICACY"},
        )
