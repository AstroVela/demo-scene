from __future__ import annotations

import pytest

from procurement_audit_sql_demo.verify_outputs import (
    FixtureVerificationError,
    verify_fixture_outputs,
)


def _findings():
    return [
        {"rule_id": "EXP-001-conflict-not-recused"},
        {"rule_id": "EXP-002-score-bias"},
        {"rule_id": "EXP-003-award-impact"},
    ]


def _summary():
    return {
        "project_id": "PRJ-2026-001",
        "status": "review_required",
        "finding_count": 3,
        "high_severity_count": 2,
        "original_winner_supplier_id": "SUP-JW-001",
        "winner_without_flagged_expert": "SUP-ZJ-002",
        "flagged_expert_id": "EXP-001",
    }


def test_expected_fixture_outputs_pass_verification():
    verify_fixture_outputs(_findings(), [_summary()])


def test_wrong_recomputed_winner_fails_verification():
    summary = {**_summary(), "winner_without_flagged_expert": "SUP-JW-001"}

    with pytest.raises(FixtureVerificationError, match="winner_without_flagged_expert"):
        verify_fixture_outputs(_findings(), [summary])


def test_insufficient_evidence_is_a_valid_degraded_fixture_outcome():
    summary = {
        **_summary(),
        "status": "insufficient_evidence",
        "finding_count": 0,
        "high_severity_count": 0,
        "winner_without_flagged_expert": None,
        "flagged_expert_id": None,
    }

    verify_fixture_outputs([], [summary])
