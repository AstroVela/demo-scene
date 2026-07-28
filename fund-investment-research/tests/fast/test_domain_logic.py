from fund_investment_research.domain_logic import (
    apply_domain_glossary,
    bind_ai_facts,
    extract_number_tokens,
    glossary_fingerprint,
    transcript_knowledge_status,
)


TERMS = [
    {
        "term_id": "TERM-COMPANY-001",
        "canonical_term": "Lanxing Biotech",
        "alias": "Lengthing Biotech",
        "category": "company",
    },
    {
        "term_id": "TERM-TARGET-001",
        "canonical_term": "Nectin-4",
        "alias": "actin-4",
        "category": "target",
    },
]


def test_glossary_changes_terms_but_not_numbers():
    raw = "Lengthing Biotech reported actin-4 ORR of 29% and TRAE of 43%."
    corrected, events = apply_domain_glossary(
        raw, TERMS, source_id="SRC-AUDIO", segment_id="SEG-1"
    )
    assert corrected == (
        "Lanxing Biotech reported Nectin-4 ORR of 29% and TRAE of 43%."
    )
    assert [row["term_id"] for row in events] == [
        "TERM-COMPANY-001",
        "TERM-TARGET-001",
    ]
    assert extract_number_tokens(raw) == extract_number_tokens(corrected)
    assert transcript_knowledge_status(corrected) == "accepted"


def test_glossary_fingerprint_is_data_driven():
    before = glossary_fingerprint(TERMS[:1])
    after = glossary_fingerprint(TERMS)
    assert before != after


def test_ai_facts_are_rebound_to_trusted_source_identity():
    response = {
        "observations": [
            {
                "fact_type": "metric",
                "metric_code": "CASH_RUNWAY",
                "value_numeric": 24.0,
                "value_text": None,
                "unit": "months",
                "source_quote": "cash runway of 24 months",
                "page": 1,
                "confidence": 0.96,
                "knowledge_kind": "source_fact",
                "review_required": False,
            }
        ],
        "impact_hypotheses": [
            {
                "metric_code": "CASH_RUNWAY",
                "condition_id": "COND-RUNWAY",
                "evidence_status": "supported",
                "rationale": "24 months exceeds 18 months.",
                "confidence": 0.95,
            }
        ],
    }
    source = {
        "source_id": "SRC-FINANCIAL",
        "company_id": "SYN-BIO-001",
        "bucket": "trusted",
        "object_key": "financial.pdf",
        "trust_tier": 1,
    }
    facts, edges = bind_ai_facts(
        response,
        source=source,
        signal_ids=["SIG-RUNWAY"],
        model_version="local-qwen",
        pipeline_version="v1",
    )
    assert facts[0]["company_id"] == "SYN-BIO-001"
    assert facts[0]["source_id"] == "SRC-FINANCIAL"
    assert facts[0]["source_locator"].startswith("minio://trusted/")
    assert edges[0]["knowledge_kind"] == "model_hypothesis"
