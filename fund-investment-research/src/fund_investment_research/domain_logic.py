"""Ordinary Python business logic; Vane adapters delegate to this module."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .contracts import stable_json


_NUMBER_TOKEN = re.compile(
    r"(?<![A-Za-z0-9-])\d+(?:\.\d+)?(?![A-Za-z0-9-])"
    r"|(?<![A-Za-z])(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
    r"nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)


def glossary_fingerprint(terms: Sequence[Mapping[str, Any]]) -> str:
    normalized = [
        {
            "term_id": str(row["term_id"]),
            "canonical_term": str(row["canonical_term"]),
            "alias": str(row["alias"]),
            "category": str(row["category"]),
        }
        for row in terms
    ]
    normalized.sort(key=lambda row: (row["term_id"], row["alias"]))
    return hashlib.sha256(stable_json(normalized).encode("utf-8")).hexdigest()


def extract_number_tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in _NUMBER_TOKEN.finditer(text)]


def apply_domain_glossary(
    raw_text: str,
    terms: Sequence[Mapping[str, Any]],
    *,
    source_id: str,
    segment_id: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Apply data-driven aliases while preserving every original span."""

    corrected = raw_text
    events: list[dict[str, Any]] = []
    ordered = sorted(
        terms,
        key=lambda row: (-len(str(row["alias"])), str(row["term_id"])),
    )
    for row in ordered:
        alias = str(row["alias"]).strip()
        canonical = str(row["canonical_term"]).strip()
        if not alias or alias.casefold() == canonical.casefold():
            continue
        pattern = re.compile(re.escape(alias), re.IGNORECASE)
        matches = list(pattern.finditer(corrected))
        if not matches:
            continue
        for match in matches:
            events.append(
                {
                    "correction_id": (
                        f"CORR-{source_id}-{segment_id}-{len(events) + 1:03d}"
                    ),
                    "source_id": source_id,
                    "segment_id": segment_id,
                    "original_span": match.group(0),
                    "canonical_term": canonical,
                    "term_id": str(row["term_id"]),
                    "reason": "domain_glossary_alias",
                    "confidence": 1.0,
                }
            )
        corrected = pattern.sub(canonical, corrected)
    if extract_number_tokens(raw_text) != extract_number_tokens(corrected):
        raise ValueError("domain glossary must not modify numeric tokens")
    return corrected, events


def transcript_knowledge_status(corrected_text: str) -> str:
    required = ("Lanxing Biotech", "Nectin-4")
    return "accepted" if all(term.casefold() in corrected_text.casefold() for term in required) else "review_required"


def has_uncertain_number(text: str) -> bool:
    lowered = text.casefold()
    uncertainty_words = ("unclear", "uncertain", "either", "approximately", "about")
    return any(word in lowered for word in uncertainty_words) and len(extract_number_tokens(text)) >= 2


def bind_ai_facts(
    response: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    signal_ids: Sequence[str],
    model_version: str,
    pipeline_version: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Bind model observations back to trusted PostgreSQL source identity."""

    facts: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    signal_id = signal_ids[0] if len(signal_ids) == 1 else None
    source_id = str(source["source_id"])
    fact_by_metric: dict[str, list[str]] = {}
    for index, observation in enumerate(response["observations"], start=1):
        fact_id = f"FACT-{source_id}-{index:03d}"
        metric = str(observation["metric_code"])
        locator = f"minio://{source['bucket']}/{source['object_key']}#page={observation['page']}"
        facts.append(
            {
                "fact_id": fact_id,
                "company_id": str(source["company_id"]),
                "signal_id": signal_id,
                "source_id": source_id,
                "fact_type": str(observation["fact_type"]),
                "entity_id": str(source["company_id"]),
                "metric_code": metric,
                "value_numeric": observation["value_numeric"],
                "value_text": observation["value_text"],
                "unit": observation["unit"],
                "period_start": None,
                "period_end": None,
                "source_quote": str(observation["source_quote"]),
                "source_locator": locator,
                "knowledge_kind": str(observation["knowledge_kind"]),
                "trust_tier": int(source["trust_tier"]),
                "confidence": float(observation["confidence"]),
                "extraction_method": "vane_ai_prompt_multimodal",
                "model_version": model_version,
                "pipeline_version": pipeline_version,
                "review_required": bool(observation["review_required"]),
            }
        )
        fact_by_metric.setdefault(metric, []).append(fact_id)

    for index, impact in enumerate(response["impact_hypotheses"], start=1):
        metric = str(impact["metric_code"])
        referenced = fact_by_metric.get(metric, [])
        if not referenced:
            raise ValueError(f"impact metric has no bound fact: {metric}")
        for fact_id in referenced:
            edges.append(
                {
                    "edge_id": f"EDGE-{source_id}-{index:03d}-{fact_id[-3:]}",
                    "signal_id": signal_id,
                    "source_id": source_id,
                    "fact_id": fact_id,
                    "metric_code": metric,
                    "condition_id": str(impact["condition_id"]),
                    "thesis_id": "THESIS-LANXING-001",
                    "relationship": (
                        "supports"
                        if impact["evidence_status"] == "supported"
                        else "violates"
                        if impact["evidence_status"] == "contradicted"
                        else "requires_review"
                    ),
                    "evidence_status": str(impact["evidence_status"]),
                    "knowledge_kind": "model_hypothesis",
                    "rationale": str(impact["rationale"]),
                    "confidence": float(impact["confidence"]),
                    "trust_tier": int(source["trust_tier"]),
                    "rule_version": "ai-candidate-bound-v1",
                }
            )
    return facts, edges


def audio_fact_candidates(
    transcript: Mapping[str, Any],
    *,
    source: Mapping[str, Any],
    pipeline_version: str,
) -> list[dict[str, Any]]:
    """Extract only explicit, non-ambiguous audio metrics using bounded rules."""

    text = str(transcript["corrected_text"])
    patterns = (
        ("ORR", r"objective response rate of\s+(\d+(?:\.\d+)?)%", "percent"),
        (
            "TRAE_G3_PLUS",
            r"adverse events of\s+(\d+(?:\.\d+)?)%",
            "percent",
        ),
        ("CASH_RUNWAY", r"cash runway is\s+(\d+(?:\.\d+)?)\s+months", "months"),
    )
    rows: list[dict[str, Any]] = []
    for metric, pattern, unit in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match is None:
            continue
        value = float(match.group(1))
        fact_id = f"FACT-{source['source_id']}-AUDIO-{len(rows) + 1:03d}"
        rows.append(
            {
                "fact_id": fact_id,
                "company_id": str(source["company_id"]),
                "signal_id": None,
                "source_id": str(source["source_id"]),
                "fact_type": "metric",
                "entity_id": str(source["company_id"]),
                "metric_code": metric,
                "value_numeric": value,
                "value_text": None,
                "unit": unit,
                "period_start": None,
                "period_end": None,
                "source_quote": match.group(0),
                "source_locator": str(transcript["source_locator"]),
                "knowledge_kind": "source_fact",
                "trust_tier": int(source["trust_tier"]),
                "confidence": 0.9,
                "extraction_method": "bounded_audio_metric_parser",
                "model_version": "whisper-small",
                "pipeline_version": pipeline_version,
                "review_required": False,
            }
        )
    return rows


def sorted_unique(values: Iterable[str]) -> list[str]:
    return sorted(set(values))
