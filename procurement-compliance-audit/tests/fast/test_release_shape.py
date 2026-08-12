from __future__ import annotations

from pathlib import Path
import tomllib

from procurement_audit_sql_demo.pipeline import CORE_RELATIONS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = PROJECT_ROOT.parent


def test_runtime_source_contract_uses_postgres_and_minio_not_local_paths():
    runtime = (PROJECT_ROOT / "runtime.yml").read_text(encoding="utf-8")
    source_files = [
        PROJECT_ROOT / "src/procurement_audit_sql_demo/pipeline.py",
        PROJECT_ROOT / "src/procurement_audit_sql_demo/ai.py",
        PROJECT_ROOT / "src/procurement_audit_sql_demo/vane_functions.py",
        PROJECT_ROOT / "src/procurement_audit_sql_demo/sql/staging/stg_evidence_images.sql",
        PROJECT_ROOT
        / "src/procurement_audit_sql_demo/sql/intermediate/int_evidence_ocr_udf.sql",
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
    qwen_guide_paths = (
        REPOSITORY_ROOT / "docs/local-qwen-service.md",
        REPOSITORY_ROOT / "docs/local-qwen-service.zh.md",
    )
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    for name, runbook in runbooks.items():
        for required in (
            "uv venv --python 3.12 .venv",
            "uv pip install 'vane-ai[openai]==0.1.0'",
            "uv pip install -r requirements.txt",
            "uv pip check",
            "Qwen2.5-VL-3B-Instruct",
            "runner: local",
            "runner: ray",
            "vane.ai.load_provider",
            "vane.ray_cxx",
            "OPENAI_API_KEY",
        ):
            assert required in runbook, f"{name} is missing {required!r}"
        assert "0.1.0a1" not in runbook
        assert "duckdb.ray_cxx" not in runbook
        assert "DuckDB Python package" not in runbook
        assert "test.pypi.org" not in runbook
        assert "unsafe-best-match" not in runbook

    assert "python scripts/run_demo.py" in readme
    assert "docs/runbook.md" in readme
    assert "../docs/local-qwen-service.md" in readme
    assert "../docs/local-qwen-service.zh.md" in readme
    for qwen_guide_path in qwen_guide_paths:
        assert qwen_guide_path.is_file()
        assert not (PROJECT_ROOT / "docs" / qwen_guide_path.name).exists()
        qwen_guide = qwen_guide_path.read_text(encoding="utf-8")
        assert "vllm==0.25.1" in qwen_guide
        assert "66285546d2b821cf421d4f5eb2576359d3770cd3" in qwen_guide
        assert "/v1/chat/completions" in qwen_guide
    assert "vane-ai[openai]==0.1.0" in project["dependencies"]
    assert "openai==2.45.0" in project["dependencies"]
    assert "socksio==1.0.0" in project["dependencies"]
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
    assert "defaults to `runner: ray`" in readme
    assert "verified with the real fixture, OCR, and Qwen" in readme
    chinese_readme = (PROJECT_ROOT / "README.zh-CN.md").read_text(
        encoding="utf-8"
    )
    assert "默认是 `runner: ray`" in chinese_readme
    assert "真实 fixture、OCR 和 Qwen" in chinese_readme


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
