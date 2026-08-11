from __future__ import annotations

from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PINNED_VANE = "vane-ai[openai]==0.1.0"
PINNED_OPENAI = "openai==2.45.0"


def _project_metadata() -> dict:
    return tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]


def test_packaging_declares_every_direct_runtime_dependency() -> None:
    project = _project_metadata()
    dependencies = set(project["dependencies"])

    assert PINNED_VANE in dependencies
    assert PINNED_OPENAI in dependencies
    for package in (
        "faster-whisper",
        "minio",
        "pyarrow",
        "pyyaml",
        "pytz",
        "socksio",
    ):
        assert any(
            requirement.startswith(package) for requirement in dependencies
        )
    assert project.get("optional-dependencies", {}).get("test") == ["pytest>=8,<9"]
    assert (PROJECT_ROOT / "requirements.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["-e .[test]"]


def test_sql_dag_files_are_all_present() -> None:
    sql_root = PROJECT_ROOT / "src" / "customer_service_audit" / "sql"
    expected = (
        "staging/stg_calls.sql",
        "staging/stg_run_config.sql",
        "intermediate/int_call_inputs.sql",
        "intermediate/int_call_probe_udf.sql",
        "intermediate/int_call_facts.sql",
        "intermediate/int_call_transcript_udf.sql",
        "intermediate/int_transcript_quality_udf.sql",
        "intermediate/int_transcript_facts.sql",
        "intermediate/int_analysis_validation_inputs.sql",
        "intermediate/int_analysis_validation_udf.sql",
        "intermediate/int_analysis_facts.sql",
        "marts/call_audit_report.sql",
    )
    for relative in expected:
        assert (sql_root / relative).is_file(), f"missing SQL stage: {relative}"


def test_runner_sql_stages_allow_leading_comments() -> None:
    from customer_service_audit.pipeline import _sql_stage_parts

    sql_root = PROJECT_ROOT / "src" / "customer_service_audit" / "sql"
    expected_targets = {
        "intermediate/int_call_probe_udf.sql": "int_call_probe_udf",
        "intermediate/int_call_transcript_udf.sql": "int_call_transcript_udf",
        "intermediate/int_transcript_quality_udf.sql": "int_transcript_quality_udf",
        "intermediate/int_analysis_validation_udf.sql": "int_analysis_validation_udf",
    }
    for relative, expected_target in expected_targets.items():
        target, query = _sql_stage_parts(sql_root / relative)
        assert target == expected_target
        assert query.strip().lower().startswith("select")


def test_transcript_quality_udf_is_materialized_before_json_parsing() -> None:
    sql_root = PROJECT_ROOT / "src" / "customer_service_audit" / "sql/intermediate"
    udf_stage = (sql_root / "int_transcript_quality_udf.sql").read_text(
        encoding="utf-8"
    )
    fact_stage = (sql_root / "int_transcript_facts.sql").read_text(
        encoding="utf-8"
    )

    assert udf_stage.count("transcript_quality_json(") == 1
    assert "transcript_quality_json(" not in fact_stage
    assert "from int_transcript_quality_udf" in fact_stage


def test_fixture_expected_analyses_match_packaged_assets() -> None:
    from customer_service_audit.fixture_loader import (
        EXPECTED_ANALYSES,
        PACKAGED_AUDIO_ASSETS,
    )

    assert set(EXPECTED_ANALYSES) == set(PACKAGED_AUDIO_ASSETS)
    for asset in PACKAGED_AUDIO_ASSETS.values():
        asset_path = (
            PROJECT_ROOT / "src" / "customer_service_audit" / "assets" / asset
        )
        assert asset_path.is_file(), f"missing packaged fixture audio: {asset}"


def test_runbooks_document_external_ray_worker_credentials() -> None:
    for relative in ("docs/runbook.md", "docs/runbook.zh-CN.md"):
        text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        assert "OPENAI_API_KEY" in text
        assert "secret management" in text
        assert "uv pip install 'vane-ai[openai]==0.1.0'" in text
        assert "uv pip install -r requirements.txt" in text
        assert "test.pypi.org" not in text
        assert "unsafe-best-match" not in text


def test_readmes_report_ray_default_as_end_to_end_verified() -> None:
    english = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (PROJECT_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    assert "defaults to `runner: ray`" in english
    assert "verified with the real fixture, ASR, and Qwen" in english
    assert "默认为 `runner: ray`" in chinese
    assert "真实 fixture、ASR 和 Qwen" in chinese
