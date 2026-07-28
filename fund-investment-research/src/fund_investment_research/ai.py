"""Multimodal fact extraction through the local Vane image prompt API."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

import pyarrow as pa
import vane

from .config import AiConfig
from .contracts import ContractError
from .vane_functions import ROLE_AI_SCOPE, validate_ai_response_for_role


SYSTEM_MESSAGE = """
You extract auditable investment-research observations from one synthetic source.
Use both the attached page image and the OCR transcript. Return exactly one JSON
object and no prose. Never invent company identity, source identity, trust tier,
or final signal state. Every observation must quote the page. impact_hypotheses
are analysis candidates, not proven causality and not final decisions.
""".strip()


ROLE_INSTRUCTIONS = {
    "approved_research": """
Extract explicit baseline thresholds as observations where visible. Use metric
codes ORR, TRAE_G3_PLUS, CASH_RUNWAY, or BLA_STATUS. The approved thesis remains
read-only; do not claim the document itself is a new signal. Map ORR to
COND-EFFICACY, TRAE_G3_PLUS to COND-SAFETY, and CASH_RUNWAY to COND-RUNWAY.
""",
    "company_clinical_announcement": """
Extract overall ORR, Grade 3+ TRAE, and the exploratory subgroup ORR. Keep the
small subgroup as supporting counter-evidence while the overall ORR and safety
facts contradict their conditions. Return all three impact rows: ORR contradicted
against COND-EFFICACY, TRAE_G3_PLUS contradicted against COND-SAFETY, and
SUBGROUP_ORR supported against COND-EFFICACY.
""",
    "audited_financial_update": """
Extract CASH_RUNWAY in months and relate it to COND-RUNWAY.
""",
    "company_regulatory_update": """
Extract BLA_STATUS. Normalize the visible on-schedule Q4 2026 statement to
value_text "on_schedule_q4_2026" and relate it as supported.
""",
    "expert_interview": """
Extract the expert's reported BLA_STATUS. Normalize the visible shifted Q2 2027
statement to value_text "delayed_q2_2027" and relate it as contradicted. This is
a source statement that conflicts with another trusted source.
""",
    "chat_screenshot": """
Extract only the unverified TRIAL_STATUS claim, normalize value_text to
"halted_unverified", set review_required true, and use unresolved evidence.
Do not treat the rumor as confirmed.
""",
}

ROLE_REQUIRED_METRICS: dict[str, frozenset[str]] = {
    "approved_research": frozenset({"ORR", "TRAE_G3_PLUS", "CASH_RUNWAY"}),
    "company_clinical_announcement": frozenset(
        {"ORR", "TRAE_G3_PLUS", "SUBGROUP_ORR"}
    ),
    "audited_financial_update": frozenset({"CASH_RUNWAY"}),
    "company_regulatory_update": frozenset({"BLA_STATUS"}),
    "expert_interview": frozenset({"BLA_STATUS"}),
    "chat_screenshot": frozenset({"TRIAL_STATUS"}),
}

ROLE_REQUIRED_IMPACTS: dict[str, frozenset[tuple[str, str, str]]] = {
    "approved_research": frozenset(
        {
            ("ORR", "COND-EFFICACY", "supported"),
            ("TRAE_G3_PLUS", "COND-SAFETY", "supported"),
            ("CASH_RUNWAY", "COND-RUNWAY", "supported"),
        }
    ),
    "company_clinical_announcement": frozenset(
        {
            ("ORR", "COND-EFFICACY", "contradicted"),
            ("TRAE_G3_PLUS", "COND-SAFETY", "contradicted"),
            ("SUBGROUP_ORR", "COND-EFFICACY", "supported"),
        }
    ),
    "audited_financial_update": frozenset(
        {("CASH_RUNWAY", "COND-RUNWAY", "supported")}
    ),
    "company_regulatory_update": frozenset(
        {("BLA_STATUS", "COND-REGULATORY", "supported")}
    ),
    "expert_interview": frozenset(
        {("BLA_STATUS", "COND-REGULATORY", "contradicted")}
    ),
    "chat_screenshot": frozenset(
        {("TRIAL_STATUS", "COND-REGULATORY", "unresolved")}
    ),
}


def _schema_example(source_role: str) -> str:
    status_examples = {
        "company_regulatory_update": (
            "BLA_STATUS",
            "on_schedule_q4_2026",
            "supported",
            "source_fact",
            False,
        ),
        "expert_interview": (
            "BLA_STATUS",
            "delayed_q2_2027",
            "contradicted",
            "source_fact",
            False,
        ),
        "chat_screenshot": (
            "TRIAL_STATUS",
            "halted_unverified",
            "unresolved",
            "uncertainty",
            True,
        ),
    }
    if source_role in status_examples:
        metric, value_text, evidence_status, knowledge_kind, review_required = (
            status_examples[source_role]
        )
        observation = {
            "fact_type": "status",
            "metric_code": metric,
            "value_numeric": None,
            "value_text": value_text,
            "unit": "status",
            "source_quote": "exact visible source text",
            "page": 1,
            "confidence": 0.95,
            "knowledge_kind": knowledge_kind,
            "review_required": review_required,
        }
        condition = "COND-REGULATORY"
    else:
        metric, condition, unit = {
            "approved_research": ("ORR", "COND-EFFICACY", "percent"),
            "company_clinical_announcement": (
                "ORR",
                "COND-EFFICACY",
                "percent",
            ),
            "audited_financial_update": (
                "CASH_RUNWAY",
                "COND-RUNWAY",
                "months",
            ),
        }[source_role]
        observation = {
            "fact_type": "metric",
            "metric_code": metric,
            "value_numeric": 1.0,
            "value_text": None,
            "unit": unit,
            "source_quote": "exact visible source text",
            "page": 1,
            "confidence": 0.95,
            "knowledge_kind": "source_fact",
            "review_required": False,
        }
        evidence_status = "supported"
    return json.dumps(
        {
            "observations": [observation],
            "impact_hypotheses": [
                {
                    "metric_code": metric,
                    "condition_id": condition,
                    "evidence_status": evidence_status,
                    "rationale": "bounded explanation based on the quoted observation",
                    "confidence": 0.9,
                }
            ],
        },
        ensure_ascii=False,
    )


def build_prompt(source_role: str, ocr_text: str, *, stricter: bool = False) -> str:
    if source_role not in ROLE_AI_SCOPE:
        raise ValueError(f"unsupported AI source role: {source_role}")
    metrics, conditions = ROLE_AI_SCOPE[source_role]
    required_metrics = ", ".join(sorted(ROLE_REQUIRED_METRICS[source_role]))
    required_impacts = "; ".join(
        f"{metric}->{condition}:{status}"
        for metric, condition, status in sorted(ROLE_REQUIRED_IMPACTS[source_role])
    )
    prefix = (
        "Your previous response violated the exact JSON contract. "
        "Correct it from the source and return JSON only.\n\n"
        if stricter
        else ""
    )
    return (
        prefix
        + ROLE_INSTRUCTIONS[source_role].strip()
        + "\n\nAllowed metric_code values: "
        + ", ".join(sorted(metrics))
        + "\nAllowed condition_id values: "
        + ", ".join(sorted(conditions))
        + "\nRequired observation metric_code values: "
        + required_metrics
        + "\nRequired impact triples (metric->condition:evidence_status): "
        + required_impacts
        + "\nAllowed evidence_status values: supported, contradicted, unresolved, not_applicable."
        + "\nAllowed knowledge_kind values: source_fact, uncertainty."
        + "\nUse null for a missing numeric or text value; do not omit any field."
        + "\nFor a status observation, value_numeric must be null, value_text must contain "
        + 'the canonical status, and unit must be "status"; never use a numeric placeholder.'
        + "\nExact response shape (replace the example values and add rows as needed):\n"
        + _schema_example(source_role)
        + "\n\nOCR transcript from page 1:\n---\n"
        + ocr_text
        + "\n---"
    )


def _validate_canonical_statuses(response: Mapping[str, Any], source_role: str) -> None:
    expected = {
        "company_regulatory_update": ("BLA_STATUS", "on_schedule_q4_2026"),
        "expert_interview": ("BLA_STATUS", "delayed_q2_2027"),
        "chat_screenshot": ("TRIAL_STATUS", "halted_unverified"),
    }.get(source_role)
    if expected is None:
        return
    metric, value_text = expected
    matches = [
        row
        for row in response["observations"]
        if row["metric_code"] == metric and row["value_text"] == value_text
    ]
    if not matches:
        raise ContractError(
            f"{source_role} must return canonical {metric} value_text={value_text!r}"
        )


def _validate_role_semantics(response: Mapping[str, Any], source_role: str) -> None:
    observed_metrics = {
        str(row["metric_code"]) for row in response["observations"]
    }
    missing_metrics = ROLE_REQUIRED_METRICS[source_role] - observed_metrics
    if missing_metrics:
        raise ContractError(
            f"{source_role} is missing required observations: {sorted(missing_metrics)}"
        )
    actual_impacts = {
        (
            str(row["metric_code"]),
            str(row["condition_id"]),
            str(row["evidence_status"]),
        )
        for row in response["impact_hypotheses"]
    }
    missing_impacts = ROLE_REQUIRED_IMPACTS[source_role] - actual_impacts
    if missing_impacts:
        raise ContractError(
            f"{source_role} is missing required impact triples: "
            f"{sorted(missing_impacts)}"
        )
    if source_role == "chat_screenshot":
        rumor_rows = [
            row
            for row in response["observations"]
            if row["metric_code"] == "TRIAL_STATUS"
        ]
        if not all(
            row["knowledge_kind"] == "uncertainty" and row["review_required"]
            for row in rumor_rows
        ):
            raise ContractError(
                "chat_screenshot status must remain uncertainty and require review"
            )


def extract_document_with_vane(
    *,
    source_role: str,
    ocr_text: str,
    image_bytes: bytes,
    ai_config: AiConfig,
    relation_factory: Callable[[str, pa.Table], Any],
    materialize: Callable[[Any, str], pa.Table],
    request_name: str,
) -> dict[str, Any]:
    """Call local Vane ``ai.prompt`` with ``image_columns`` and validate output."""

    provider_options = vane.ai.OpenAIProviderOptions(
        base_url=ai_config.base_url,
        api_key=ai_config.api_key,
        timeout=ai_config.timeout_seconds,
        concurrency=ai_config.concurrency,
        max_api_concurrency=ai_config.concurrency,
    )
    prompt_options = vane.ai.OpenAIPromptOptions(
        temperature=ai_config.temperature,
        max_tokens=ai_config.max_tokens,
        on_error="raise",
    )
    last_error: Exception | None = None
    last_raw_response: str | None = None
    for attempt in range(2):
        table = pa.table(
            {
                "prompt_text": pa.array(
                    [build_prompt(source_role, ocr_text, stricter=attempt > 0)],
                    type=pa.string(),
                ),
                "image_bytes": pa.array([image_bytes], type=pa.binary()),
            }
        )
        relation = relation_factory(f"{request_name}-attempt-{attempt + 1}", table)
        prompted = vane.ai.prompt(
            relation,
            "prompt_text",
            image_columns=["image_bytes"],
            provider=ai_config.provider,
            model=ai_config.model,
            provider_options=provider_options,
            prompt_options=prompt_options,
            system_message=SYSTEM_MESSAGE,
            output_column="raw_response",
            execution_backend="ray_actor",
            num_gpus=0,
        )
        result = materialize(prompted, f"{request_name}-response-{attempt + 1}")
        if result.num_rows != 1 or result.num_columns != 1:
            raise ContractError("Vane AI prompt must return exactly one response")
        raw_response = result.column(0)[0].as_py()
        last_raw_response = raw_response if isinstance(raw_response, str) else repr(raw_response)
        try:
            canonical = validate_ai_response_for_role(raw_response, source_role)
            parsed = json.loads(canonical)
            _validate_canonical_statuses(parsed, source_role)
            _validate_role_semantics(parsed, source_role)
            return parsed
        except (ContractError, TypeError, ValueError) as exc:
            last_error = exc
    assert last_error is not None
    raw_diagnostic = " ".join((last_raw_response or "<no response>").split())
    if len(raw_diagnostic) > 1600:
        raw_diagnostic = raw_diagnostic[:1600] + "…"
    raise ContractError(
        f"AI response for {request_name} failed strict validation twice: {last_error}; "
        f"last_raw_response={raw_diagnostic}"
    ) from last_error
