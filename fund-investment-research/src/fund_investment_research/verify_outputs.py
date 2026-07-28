"""Mechanical verification of signal states and the four business-value actions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .fixture_loader import logical_scenario


EXPECTED_STATES = {
    "SIG-CLINICAL": "thesis_review_required",
    "SIG-RUNWAY": "thesis_supported",
    "SIG-REGULATORY": "manual_review",
    "SIG-RUMOR": "insufficient_evidence",
}


@dataclass(frozen=True)
class VerificationResult:
    scenario: str
    passed: bool
    checks: tuple[tuple[str, bool], ...]
    states: dict[str, str]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _snapshot(config: RuntimeConfig, scenario: str) -> tuple[Path, dict[str, Any]]:
    current = config.output_dir / scenario / "current"
    if not current.exists():
        raise FileNotFoundError(f"published snapshot does not exist: {current}")
    return current, {
        "transcripts": _read_jsonl(current / "transcript_segments.jsonl"),
        "corrections": _read_jsonl(current / "asr_corrections.jsonl"),
        "facts": _read_jsonl(current / "research_facts.jsonl"),
        "edges": _read_jsonl(current / "thesis_impact_edges.jsonl"),
        "signals": _read_jsonl(current / "research_signals.jsonl"),
        "tasks": _read_jsonl(current / "review_tasks.jsonl"),
        "statuses": _read_jsonl(current / "source_processing_status.jsonl"),
        "quality": _read_json(current / "asr_quality_metrics.json"),
        "manifest": _read_json(current / "run_manifest.json"),
        "report": (current / "signal_evidence_report.md").read_text(encoding="utf-8"),
    }


def verify(config: RuntimeConfig, scenario_variant: str = "default") -> VerificationResult:
    scenario = logical_scenario(scenario_variant)
    current, data = _snapshot(config, scenario)
    states = {row["signal_id"]: row["state"] for row in data["signals"]}
    checks: list[tuple[str, bool]] = []

    def check(label: str, value: Any) -> None:
        checks.append((label, bool(value)))

    check("exact four deterministic signal states", states == EXPECTED_STATES)
    transcript_keys = {
        (row["source_id"], row["segment_id"]) for row in data["transcripts"]
    }
    check(
        "at least one domain correction is traceable to its raw audio span",
        bool(data["corrections"])
        and all(
            (row["source_id"], row["segment_id"]) in transcript_keys
            and row["original_span"]
            and row["term_id"]
            for row in data["corrections"]
        ),
    )
    check(
        "uncertain numbers were not silently rewritten",
        not data["quality"]["numbers_silently_rewritten"]
        and any(row["task_type"] == "uncertain_number" for row in data["tasks"])
        and any(row["knowledge_kind"] == "uncertainty" for row in data["facts"]),
    )
    clinical_statuses = {
        row["evidence_status"]
        for row in data["edges"]
        if row["signal_id"] == "SIG-CLINICAL"
    }
    check(
        "the clinical signal retains supporting and opposing evidence",
        {"supported", "contradicted"} <= clinical_statuses,
    )
    check(
        "source facts, approved theses, model hypotheses and uncertainty are separated",
        any(row["knowledge_kind"] == "source_fact" for row in data["facts"])
        and any(row["knowledge_kind"] == "uncertainty" for row in data["facts"])
        and all(row["knowledge_kind"] == "model_hypothesis" for row in data["edges"])
        and "[approved_thesis]" in data["report"],
    )
    fact_ids = {row["fact_id"] for row in data["facts"]}
    check(
        "every review task points to a judgment, evidence and original locator",
        all(
            row["judgment_id"]
            and row["source_locator"]
            and row["evidence_fact_ids"]
            and set(row["evidence_fact_ids"]) <= fact_ids
            for row in data["tasks"]
        ),
    )
    rumor_facts = [
        row for row in data["facts"] if row.get("signal_id") == "SIG-RUMOR"
    ]
    check(
        "a low-trust rumor did not drive an automatic thesis state",
        states.get("SIG-RUMOR") == "insufficient_evidence"
        and rumor_facts
        and all(row["trust_tier"] == 3 for row in rumor_facts),
    )
    check(
        "final signal states were produced by deterministic SQL",
        all(row["decision_source"] == "deterministic_sql" for row in data["signals"])
        and data["manifest"]["decision_contract"] == "deterministic_sql",
    )
    output_hashes = data["manifest"]["output_hashes"]
    check(
        "published output hashes match the atomic manifest",
        all(
            (current / name).exists()
            and hashlib.sha256((current / name).read_bytes()).hexdigest() == expected
            for name, expected in output_hashes.items()
        ),
    )
    if scenario_variant == "glossary-before":
        check(
            "glossary-before leaves the target alias unresolved",
            not any(row["term_id"] == "TERM-TARGET-001" for row in data["corrections"])
            and data["quality"]["knowledge_status"] == "review_required",
        )
    elif scenario_variant == "glossary-after":
        _before_path, before = _snapshot(config, "glossary-before")
        check(
            "a glossary data change alters correction without Pipeline code changes",
            any(row["term_id"] == "TERM-TARGET-001" for row in data["corrections"])
            and data["quality"]["knowledge_status"] == "accepted"
            and before["manifest"]["pipeline_sha256"]
            == data["manifest"]["pipeline_sha256"],
        )
    if scenario == "recovery":
        check(
            "resume scope contains only the corrected previously failed source",
            data["manifest"]["resume"] is True
            and data["manifest"]["resume_recomputed_source_ids"] == ["SRC-CLINICAL"]
            and {
                row["source_id"] for row in data["manifest"]["resume_scope"]
            }
            == {"SRC-CLINICAL"},
        )
    return VerificationResult(
        scenario=scenario,
        passed=all(value for _, value in checks),
        checks=tuple(checks),
        states=states,
    )


def print_verification(result: VerificationResult) -> None:
    for label, passed in result.checks:
        print(f"{'PASS' if passed else 'FAIL'}: {label}")
    if result.scenario != "recovery":
        print(
            "SKIP: resume scope contains only new, changed or previously failed "
            "stage inputs (recovery scenario only)"
        )
    print("verified research signals:")
    for signal_id in sorted(result.states):
        print(f"  {signal_id}={result.states[signal_id]}")
