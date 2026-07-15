from __future__ import annotations

from pathlib import Path

from procurement_audit_sql_demo import cli
from procurement_audit_sql_demo.pipeline import PipelineResult


def _result(tmp_path):
    return PipelineResult(
        executed_relations=(
            "stg_scores",
            "stg_evidence_images",
            "int_evidence_ocr",
            "int_evidence_ai",
            "int_conflict_facts",
            "int_score_metrics",
            "audit_findings",
            "audit_summary",
        ),
        findings=({}, {}, {}),
        summary={
            "project_id": "PRJ-2026-001",
            "title": "智能产线升级项目",
            "status": "review_required",
            "finding_count": 3,
            "high_severity_count": 2,
            "original_winner_supplier_id": "SUP-JW-001",
            "winner_without_flagged_expert": "SUP-ZJ-002",
            "flagged_expert_id": "EXP-001",
        },
        finding_count=3,
        summary_count=1,
        findings_path=tmp_path / "audit_findings.jsonl",
        summary_path=tmp_path / "audit_summary.jsonl",
    )


def test_cli_prints_business_result_and_vane_capabilities(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "load_runtime_config", lambda _path: object())
    monkeypatch.setattr(cli, "run_pipeline", lambda _config: _result(tmp_path))

    assert cli.main(["--config", str(tmp_path / "runtime.yml")]) == 0

    output = capsys.readouterr().out
    assert "智能产线升级项目" in output
    assert "EXP-001" in output
    assert "SUP-JW-001 -> SUP-ZJ-002" in output
    assert "3 findings" in output
    assert "PostgreSQL business rows + MinIO evidence objects" in output
    assert "[stateful UDF]" in output
    assert "[AI Function]" in output
    assert "[stateless UDF]" in output
    assert "[SQL]" in output


def test_cli_returns_nonzero_without_hiding_failure(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "load_runtime_config", lambda _path: object())
    monkeypatch.setattr(
        cli,
        "run_pipeline",
        lambda _config: (_ for _ in ()).throw(ConnectionError("Qwen unavailable")),
    )

    assert cli.main(["--config", str(tmp_path / "runtime.yml")]) == 1
    assert "Qwen unavailable" in capsys.readouterr().err


def test_cli_e2e_seeds_external_sources_before_running(monkeypatch):
    events = []

    monkeypatch.setattr(
        cli,
        "_run_command",
        lambda command, arguments: events.append((command, arguments)) or 0,
    )

    assert cli.main(["e2e"]) == 0
    assert events == [("fixture", []), ("run", [])]
