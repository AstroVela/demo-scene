from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pyarrow as pa
import pytest

from procurement_audit_sql_demo.ai import EvidenceAiInputError
from procurement_audit_sql_demo.config import load_runtime_config
from procurement_audit_sql_demo.pipeline import CORE_RELATIONS, run_pipeline
from procurement_audit_sql_demo.vane_functions import stable_json, validate_audit_fact_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _fixed_ai_table() -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                "project_id": "PRJ-2026-001",
                "file_id": "EVD-REC-001",
                "raw_response": stable_json(
                    {
                        "document_type": "recommendation_record",
                        "expert_id": "EXP-001",
                        "supplier_name": "景维自动化有限公司",
                        "recommended": True,
                        "participated": None,
                        "recused": None,
                        "evidence_quote": "推荐供应商：景维自动化有限公司",
                        "confidence": 0.96,
                    }
                ),
            },
            {
                "project_id": "PRJ-2026-001",
                "file_id": "EVD-MIN-001",
                "raw_response": stable_json(
                    {
                        "document_type": "committee_minutes",
                        "expert_id": "EXP-001",
                        "supplier_name": None,
                        "recommended": None,
                        "participated": True,
                        "recused": False,
                        "evidence_quote": "参加评审：是；是否回避：否",
                        "confidence": 0.95,
                    }
                ),
            },
        ]
    )


def test_pipeline_runs_eight_relations_on_one_connection_and_publishes(tmp_path):
    config = replace(
        load_runtime_config(PROJECT_ROOT / "runtime.yml"),
        output_dir=tmp_path / "output",
    )
    events = []

    def configure_runner(*, runner):
        events.append(f"configure:{runner}")

    def attach_functions(connection, fixture):
        events.append("attach_functions")
        connection.create_function(
            "evidence_ocr_json",
            lambda _path: stable_json(
                {
                    "status": "success",
                    "full_text": "fixture OCR text",
                    "mean_confidence": 0.95,
                    "text_line_count": 1,
                    "error": None,
                }
            ),
            ["VARCHAR"],
            "VARCHAR",
        )
        connection.create_function(
            "validate_audit_fact_json",
            validate_audit_fact_json,
            ["VARCHAR"],
            "VARCHAR",
        )

    def build_ai(ocr_rows, connection, fixture, runtime_config):
        events.append(f"ai:{len(ocr_rows)}")
        assert runtime_config is config
        assert {row["file_id"] for row in ocr_rows} == {"EVD-REC-001", "EVD-MIN-001"}
        return connection.from_arrow(_fixed_ai_table())

    result = run_pipeline(
        config,
        configure_runner=configure_runner,
        runtime_function_attacher=attach_functions,
        ai_relation_builder=build_ai,
    )

    assert events == ["configure:local", "attach_functions", "ai:2"]
    assert result.executed_relations == CORE_RELATIONS
    assert result.finding_count == 3
    assert result.summary_count == 1
    assert result.summary["status"] == "review_required"
    assert result.summary["winner_without_flagged_expert"] == "SUP-ZJ-002"
    assert len(result.findings) == 3
    assert len(
        (tmp_path / "output/audit_findings.jsonl").read_text(encoding="utf-8").splitlines()
    ) == 3
    assert json.loads(
        (tmp_path / "output/audit_summary.jsonl").read_text(encoding="utf-8")
    )["flagged_expert_id"] == "EXP-001"


def test_pipeline_does_not_publish_when_ocr_coverage_is_incomplete(tmp_path):
    config = replace(
        load_runtime_config(PROJECT_ROOT / "runtime.yml"),
        output_dir=tmp_path / "output",
    )

    def attach_functions(connection, _fixture):
        def evidence_ocr(path):
            if path.endswith("expert_recommendation.png"):
                return stable_json(
                    {
                        "status": "success",
                        "full_text": "专家编号 EXP-001；推荐供应商 景维自动化有限公司",
                        "mean_confidence": 0.95,
                        "text_line_count": 1,
                        "error": None,
                    }
                )
            return stable_json(
                {
                    "status": "unreadable",
                    "full_text": "",
                    "mean_confidence": 0.0,
                    "text_line_count": 0,
                    "error": "no_text_detected",
                }
            )

        connection.create_function(
            "evidence_ocr_json",
            evidence_ocr,
            ["VARCHAR"],
            "VARCHAR",
        )
        connection.create_function(
            "validate_audit_fact_json",
            validate_audit_fact_json,
            ["VARCHAR"],
            "VARCHAR",
        )

    with pytest.raises(EvidenceAiInputError, match="EVD-MIN-001"):
        run_pipeline(
            config,
            configure_runner=lambda **_kwargs: None,
            runtime_function_attacher=attach_functions,
        )

    assert not config.output_dir.exists()
