"""Business assertions for the checked-in expert-score anomaly fixture."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


EXPECTED_RULES = {
    "EXP-001-conflict-not-recused",
    "EXP-002-score-bias",
    "EXP-003-award-impact",
}
EXPECTED_SUMMARY = {
    "project_id": "PRJ-2026-001",
    "status": "review_required",
    "finding_count": 3,
    "high_severity_count": 2,
    "original_winner_supplier_id": "SUP-JW-001",
    "winner_without_flagged_expert": "SUP-ZJ-002",
    "flagged_expert_id": "EXP-001",
}


class FixtureVerificationError(ValueError):
    """Raised when the focused fixture no longer tells its intended story."""


def verify_fixture_outputs(
    findings: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]],
) -> None:
    """Assert that the published rows still express the intended demo story."""

    errors: list[str] = []
    if len(summaries) != 1:
        raise FixtureVerificationError(
            f"summary_count: expected 1, got {len(summaries)}"
        )
    summary = summaries[0]
    if summary.get("status") == "insufficient_evidence":
        degraded_expected = {
            "project_id": EXPECTED_SUMMARY["project_id"],
            "status": "insufficient_evidence",
            "finding_count": 0,
            "high_severity_count": 0,
            "original_winner_supplier_id": EXPECTED_SUMMARY[
                "original_winner_supplier_id"
            ],
        }
        if findings:
            errors.append(
                f"finding_count: expected 0, got {len(findings)}"
            )
        for field, expected in degraded_expected.items():
            actual = summary.get(field)
            if actual != expected:
                errors.append(f"{field}: expected {expected}, got {actual}")
        if errors:
            raise FixtureVerificationError("; ".join(errors))
        return

    if len(findings) != 3:
        errors.append(f"finding_count: expected 3, got {len(findings)}")
    rule_ids = [str(row.get("rule_id", "")) for row in findings]
    if len(rule_ids) != len(set(rule_ids)):
        errors.append("rule_id: duplicate finding rule")
    if set(rule_ids) != EXPECTED_RULES:
        errors.append(
            f"rule_id: expected {sorted(EXPECTED_RULES)}, got {sorted(rule_ids)}"
        )
    for field, expected in EXPECTED_SUMMARY.items():
        actual = summary.get(field)
        if actual != expected:
            errors.append(f"{field}: expected {expected}, got {actual}")
    if errors:
        raise FixtureVerificationError("; ".join(errors))
