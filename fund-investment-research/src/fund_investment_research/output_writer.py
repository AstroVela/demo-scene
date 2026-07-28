"""Cross-reference validation and atomic snapshot publication."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import (
    CONDITION_IDS,
    EVIDENCE_STATUSES,
    KNOWLEDGE_KINDS,
    SIGNAL_STATES,
    ContractError,
    validate_primary_keys,
)


JSONL_OUTPUTS = (
    "transcript_segments",
    "asr_corrections",
    "research_facts",
    "thesis_impact_edges",
    "research_signals",
    "review_tasks",
    "source_processing_status",
)


@dataclass(frozen=True)
class PublishedOutputs:
    scenario_dir: Path
    run_dir: Path
    current_dir: Path


def _json_line(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _validate_outputs(outputs: dict[str, Any], report: str) -> None:
    missing = (set(JSONL_OUTPUTS) | {"asr_quality_metrics", "run_manifest"}) - set(outputs)
    if missing:
        raise ContractError(f"publication is missing outputs: {sorted(missing)}")
    transcripts = outputs["transcript_segments"]
    corrections = outputs["asr_corrections"]
    facts = outputs["research_facts"]
    edges = outputs["thesis_impact_edges"]
    signals = outputs["research_signals"]
    tasks = outputs["review_tasks"]
    statuses = outputs["source_processing_status"]
    validate_primary_keys(transcripts, ("source_id", "segment_id"), "transcript_segments")
    validate_primary_keys(corrections, ("correction_id",), "asr_corrections")
    validate_primary_keys(facts, ("fact_id",), "research_facts")
    validate_primary_keys(edges, ("edge_id",), "thesis_impact_edges")
    validate_primary_keys(signals, ("signal_id", "thesis_id"), "research_signals")
    validate_primary_keys(tasks, ("task_id",), "review_tasks")
    validate_primary_keys(
        statuses,
        ("source_id", "source_sha256", "stage", "stage_version"),
        "source_processing_status",
    )
    source_ids = {row["source_id"] for row in statuses}
    fact_ids = {row["fact_id"] for row in facts}
    signal_ids = {row["signal_id"] for row in signals}
    for fact in facts:
        if fact["knowledge_kind"] not in KNOWLEDGE_KINDS:
            raise ContractError(f"invalid fact knowledge_kind: {fact['fact_id']}")
        if fact["source_id"] not in source_ids:
            raise ContractError(f"fact references unknown source: {fact['fact_id']}")
        if fact["value_numeric"] is not None and not fact["unit"]:
            raise ContractError(f"numeric fact has no unit: {fact['fact_id']}")
    for edge in edges:
        if edge["fact_id"] not in fact_ids:
            raise ContractError(f"edge references unknown fact: {edge['edge_id']}")
        if edge["source_id"] not in source_ids:
            raise ContractError(f"edge references unknown source: {edge['edge_id']}")
        if edge["condition_id"] not in CONDITION_IDS:
            raise ContractError(f"edge references unknown condition: {edge['edge_id']}")
        if edge["evidence_status"] not in EVIDENCE_STATUSES:
            raise ContractError(f"invalid edge evidence status: {edge['edge_id']}")
        if edge["knowledge_kind"] != "model_hypothesis":
            raise ContractError(f"edge must remain a model hypothesis: {edge['edge_id']}")
    facts_by_signal: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        if fact.get("signal_id"):
            facts_by_signal.setdefault(fact["signal_id"], []).append(fact)
    for signal in signals:
        if signal["state"] not in SIGNAL_STATES:
            raise ContractError(f"invalid signal state: {signal['signal_id']}")
        if signal["decision_source"] != "deterministic_sql":
            raise ContractError(f"model cannot decide final state: {signal['signal_id']}")
        signal_facts = facts_by_signal.get(signal["signal_id"], [])
        if signal["state"] in {"thesis_supported", "thesis_review_required"}:
            if not any(fact["trust_tier"] <= 2 for fact in signal_facts):
                raise ContractError(
                    f"low-trust evidence drove automatic state: {signal['signal_id']}"
                )
        if f"`{signal['state']}`" not in report:
            raise ContractError(
                f"Markdown report does not mirror state for {signal['signal_id']}"
            )
    for task in tasks:
        if task.get("signal_id") and task["signal_id"] not in signal_ids:
            raise ContractError(f"task references unknown signal: {task['task_id']}")
        evidence = task.get("evidence_fact_ids")
        if not isinstance(evidence, list) or not evidence:
            raise ContractError(f"task has no focused evidence: {task['task_id']}")
        if any(fact_id not in fact_ids for fact_id in evidence):
            raise ContractError(f"task references unknown evidence: {task['task_id']}")
        if not task.get("judgment_id") or not task.get("source_locator"):
            raise ContractError(f"task is not source-locatable: {task['task_id']}")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(_json_line(value) + "\n", encoding="utf-8")


def publish_outputs(
    *,
    output_root: Path,
    scenario: str,
    run_id: str,
    outputs: dict[str, Any],
    evidence_report: str,
) -> PublishedOutputs:
    """Publish an immutable run and atomically switch the ``current`` symlink."""

    _validate_outputs(outputs, evidence_report)
    scenario_dir = output_root / scenario
    runs_dir = scenario_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".publishing-", dir=runs_dir))
    final_dir = runs_dir / run_id
    try:
        output_hashes: dict[str, str] = {}
        for name in JSONL_OUTPUTS:
            path = temp_dir / f"{name}.jsonl"
            rows = outputs[name]
            path.write_text(
                "".join(_json_line(row) + "\n" for row in rows),
                encoding="utf-8",
            )
            output_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        metrics_path = temp_dir / "asr_quality_metrics.json"
        _write_json(metrics_path, outputs["asr_quality_metrics"])
        output_hashes[metrics_path.name] = hashlib.sha256(
            metrics_path.read_bytes()
        ).hexdigest()
        report_path = temp_dir / "signal_evidence_report.md"
        report_path.write_text(evidence_report, encoding="utf-8")
        output_hashes[report_path.name] = hashlib.sha256(
            report_path.read_bytes()
        ).hexdigest()
        manifest = dict(outputs["run_manifest"])
        manifest["output_hashes"] = output_hashes
        _write_json(temp_dir / "run_manifest.json", manifest)
        if final_dir.exists():
            raise FileExistsError(f"run directory already exists: {final_dir}")
        os.replace(temp_dir, final_dir)
        scenario_dir.mkdir(parents=True, exist_ok=True)
        link_name = f".current-{uuid.uuid4().hex}"
        temporary_link = scenario_dir / link_name
        os.symlink(Path("runs") / run_id, temporary_link)
        os.replace(temporary_link, scenario_dir / "current")
    except Exception:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        raise
    return PublishedOutputs(
        scenario_dir=scenario_dir,
        run_dir=final_dir,
        current_dir=scenario_dir / "current",
    )
