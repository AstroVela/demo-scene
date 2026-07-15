from __future__ import annotations

from pathlib import Path
import tomllib

from procurement_audit_sql_demo.output_writer import OUTPUT_FILENAMES
from procurement_audit_sql_demo.pipeline import CORE_RELATIONS
from procurement_audit_sql_demo.verify_outputs import EXPECTED_RULES


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_release_shape_is_deliberately_small():
    fixture_files = sorted(
        path.name
        for path in (PROJECT_ROOT / "fixtures/expert-score-anomaly").iterdir()
        if path.is_file()
    )

    assert fixture_files == [
        "committee_minutes.png",
        "expert_recommendation.png",
        "expert_scores.csv",
        "project.json",
    ]
    assert len(CORE_RELATIONS) == 8
    assert len(list((PROJECT_ROOT / "src/procurement_audit_sql_demo/sql").rglob("*.sql"))) == 7
    assert EXPECTED_RULES == {
        "EXP-001-conflict-not-recused",
        "EXP-002-score-bias",
        "EXP-003-award-impact",
    }
    assert OUTPUT_FILENAMES == {"audit_findings.jsonl", "audit_summary.jsonl"}


def test_runtime_source_contract_uses_postgres_and_minio_not_local_paths():
    runtime = (PROJECT_ROOT / "runtime.yml").read_text(encoding="utf-8")
    source_files = [
        PROJECT_ROOT / "src/procurement_audit_sql_demo/pipeline.py",
        PROJECT_ROOT / "src/procurement_audit_sql_demo/ai.py",
        PROJECT_ROOT / "src/procurement_audit_sql_demo/vane_functions.py",
        PROJECT_ROOT / "src/procurement_audit_sql_demo/sql/staging/stg_evidence_images.sql",
        PROJECT_ROOT / "src/procurement_audit_sql_demo/sql/intermediate/int_evidence_ocr.sql",
    ]

    assert "postgres:" in runtime
    assert "minio:" in runtime
    assert "fixture_dir" not in runtime
    for path in source_files:
        text = path.read_text(encoding="utf-8")
        assert "local_path" not in text, f"{path.name} still uses a local source path"


def test_queries_are_read_only_and_cover_all_eight_relations():
    queries = (PROJECT_ROOT / "queries.sql").read_text(encoding="utf-8")

    assert queries.lower().count("select * from") == 8
    assert "create " not in queries.lower()
    for relation_name in CORE_RELATIONS:
        assert f"select * from {relation_name}" in queries.lower()


def test_readme_and_runbook_cover_story_execution_and_vane_capabilities():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    runbooks = {
        name: (PROJECT_ROOT / name).read_text(encoding="utf-8")
        for name in ("docs/runbook.md", "docs/runbook.zh-CN.md")
    }
    qwen_guide_path = PROJECT_ROOT / "docs/local-qwen-service.zh.md"
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    for name, runbook in runbooks.items():
        for required in (
            "python3.12 -m venv .venv",
            "https://test.pypi.org/simple/",
            "--extra-index-url https://pypi.org/simple/",
            "vane-ai==0.1.0.dev20260714234347",
            "python -m pip install -r requirements.txt",
            "Qwen2.5-VL-3B-Instruct",
            "runner: local",
            "runner: ray",
        ):
            assert required in runbook, f"{name} is missing {required!r}"

    assert "python scripts/run_demo.py" in readme
    assert "docs/runbook.md" in readme
    assert "docs/local-qwen-service.zh.md" in readme
    assert qwen_guide_path.is_file()
    qwen_guide = qwen_guide_path.read_text(encoding="utf-8")
    assert "vllm==0.25.1" in qwen_guide
    assert "66285546d2b821cf421d4f5eb2576359d3770cd3" in qwen_guide
    assert "/v1/chat/completions" in qwen_guide
    assert "vane-ai==0.1.0.dev20260714234347" in project["dependencies"]
    assert "openai==2.45.0" in project["dependencies"]
    assert any(item.startswith("minio") for item in project["dependencies"])
    assert any(item.startswith("psycopg") for item in project["dependencies"])
    for runbook in runbooks.values():
        for required in (
            "python scripts/run_demo.py fixture",
            "python scripts/run_demo.py run",
            "python scripts/run_demo.py e2e",
            "127.0.0.1:5432",
            "127.0.0.1:9000",
            "PostgreSQL",
            "MinIO",
        ):
            assert required in runbook
    assert "@vane.cls" in readme
    assert "@vane.func" in readme
    assert "vane.ai.prompt" in readme
    assert "AI Function" in readme
    assert "SUP-JW-001" in readme and "SUP-ZJ-002" in readme


def test_old_demo_is_not_a_runtime_dependency():
    old_demo_reference = "procurement-compliance-" + "audit/"
    legacy_vane_prefix = "/home/zhuwei/vane/" + ".venv-system"
    for path in PROJECT_ROOT.rglob("*"):
        if path.is_file() and (
            path.suffix in {".md", ".py", ".sql", ".txt", ".yml", ".toml"}
            or path.name == "PKG-INFO"
        ):
            text = path.read_text(
                encoding="utf-8",
                errors="ignore",
            )
            assert old_demo_reference not in text
            assert legacy_vane_prefix not in text
