from __future__ import annotations

from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = PROJECT_ROOT.parent
PINNED_VANE = "vane-ai[openai]==0.1.0"
PINNED_OPENAI = "openai==2.45.0"
QWEN_REVISION = "66285546d2b821cf421d4f5eb2576359d3770cd3"


def _project_metadata() -> dict:
    return tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]


def test_packaging_declares_every_direct_runtime_dependency():
    project = _project_metadata()
    dependencies = set(project["dependencies"])

    assert PINNED_VANE in dependencies
    assert PINNED_OPENAI in dependencies
    for package in (
        "minio",
        "numpy",
        "onnxruntime",
        "pillow",
        "psycopg",
        "pyarrow",
        "pyyaml",
        "pytz",
        "rapidocr",
        "socksio",
    ):
        assert any(requirement.startswith(package) for requirement in dependencies)
    assert project.get("optional-dependencies", {}).get("test") == ["pytest>=8,<9"]
    assert (PROJECT_ROOT / "requirements.txt").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "# Runtime dependencies, including Vane, are pinned in pyproject.toml.",
        "-e .[test]",
    ]


def test_runbooks_document_a_fresh_environment_and_all_services():
    required_fragments = (
        "uv venv --python 3.12 .venv",
        f"uv pip install '{PINNED_VANE}'",
        "uv pip install -r requirements.txt",
        "uv pip check",
        "python scripts/run_demo.py fixture",
        "python scripts/run_demo.py run",
        "python scripts/run_demo.py verify",
        "python scripts/run_demo.py e2e",
        "runner: local",
        "runner: ray",
        "127.0.0.1:5432",
        "127.0.0.1:9000",
        "127.0.0.1:8001",
        "v1.5.0-vane.b1c745e9c4",
        "0c2adbf409",
        "vane.ai.load_provider",
        "vane.ray_cxx",
        "OPENAI_API_KEY",
    )
    for name in ("docs/runbook.md", "docs/runbook.zh-CN.md"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert "0.1.0a1" not in text
        assert "duckdb.ray_cxx" not in text
        assert "DuckDB Python package" not in text
        assert "test.pypi.org" not in text
        assert "unsafe-best-match" not in text
        for required in required_fragments:
            assert required in text, f"{name} is missing {required!r}"


def test_readmes_report_ray_default_end_to_end_validation():
    english = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (PROJECT_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    assert "defaults to `runner: ray`" in english
    assert "verified with the real fixture, OCR, and Qwen" in english
    assert "默认是 `runner: ray`" in chinese
    assert "真实 fixture、OCR 和 Qwen" in chinese


def test_runbooks_explain_direct_dependencies_and_service_ownership():
    dependency_names = (
        "openai",
        "minio",
        "psycopg",
        "rapidocr",
        "onnxruntime",
        "numpy",
        "pillow",
        "pyarrow",
        "pyyaml",
        "pytz",
        "socksio",
        "pytest",
    )
    for name in ("docs/runbook.md", "docs/runbook.zh-CN.md"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8").lower()
        for dependency in dependency_names:
            assert dependency in text, f"{name} does not explain {dependency}"
        assert "fixture" in text
        assert "postgresql" in text
        assert "minio" in text
        assert "qwen" in text


def test_readmes_link_the_matching_qwen_guides():
    assert "../docs/local-qwen-service.md" in (PROJECT_ROOT / "README.md").read_text(
        encoding="utf-8"
    )
    assert "../docs/local-qwen-service.zh.md" in (
        PROJECT_ROOT / "README.zh-CN.md"
    ).read_text(encoding="utf-8")


def test_qwen_guides_pin_and_smoke_test_the_model_service():
    for name in ("local-qwen-service.md", "local-qwen-service.zh.md"):
        assert not (PROJECT_ROOT / "docs" / name).exists()
        path = REPOSITORY_ROOT / "docs" / name
        assert path.is_file(), f"missing {name}"
        text = path.read_text(encoding="utf-8")
        assert "vllm==0.25.1" in text
        assert "--torch-backend=auto" in text
        assert QWEN_REVISION in text
        assert "/health" in text
        assert "/v1/models" in text
        assert "/v1/chat/completions" in text
        assert "127.0.0.1" in text


def test_release_files_do_not_reference_the_legacy_runtime():
    legacy_prefix = "/home/zhuwei/vane/" + ".venv-system"
    release_files = [
        PROJECT_ROOT / "README.md",
        PROJECT_ROOT / "README.zh-CN.md",
        PROJECT_ROOT / "docs/runbook.md",
        PROJECT_ROOT / "docs/runbook.zh-CN.md",
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "requirements.txt",
        PROJECT_ROOT / "runtime.yml",
        *(PROJECT_ROOT / "scripts").glob("*.py"),
    ]
    release_files.extend(
        path
        for path in (
            REPOSITORY_ROOT / "docs/local-qwen-service.md",
            REPOSITORY_ROOT / "docs/local-qwen-service.zh.md",
        )
        if path.is_file()
    )

    for path in release_files:
        assert legacy_prefix not in path.read_text(encoding="utf-8")
