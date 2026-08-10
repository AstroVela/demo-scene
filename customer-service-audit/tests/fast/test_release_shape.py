from __future__ import annotations

from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PINNED_VANE = "vane-ai==0.1.0a1"
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
        "intermediate/int_transcript_facts.sql",
        "intermediate/int_analysis_validation_inputs.sql",
        "intermediate/int_analysis_validation_udf.sql",
        "intermediate/int_analysis_facts.sql",
        "marts/call_audit_report.sql",
    )
    for relative in expected:
        assert (sql_root / relative).is_file(), f"missing SQL stage: {relative}"


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
