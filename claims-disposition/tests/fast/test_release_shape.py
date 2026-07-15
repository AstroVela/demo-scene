from __future__ import annotations

from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PINNED_VANE = "vane-ai==0.1.0.dev20260714234347"
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
    ):
        assert any(requirement.startswith(package) for requirement in dependencies)
    assert project.get("optional-dependencies", {}).get("test") == ["pytest>=8,<9"]
    assert (PROJECT_ROOT / "requirements.txt").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "# Install the pinned TestPyPI Vane wheel first; see docs/runbook.md.",
        "-e .[test]",
    ]


def test_runbooks_document_a_fresh_environment_and_all_services():
    required_fragments = (
        "python3.12 -m venv .venv",
        "https://test.pypi.org/simple/",
        "--extra-index-url https://pypi.org/simple/",
        PINNED_VANE,
        "python -m pip install -r requirements.txt",
        "python -m pip check",
        "python scripts/run_demo.py fixture",
        "python scripts/run_demo.py run",
        "python scripts/run_demo.py verify",
        "python scripts/run_demo.py e2e",
        "127.0.0.1:5432",
        "127.0.0.1:9000",
        "127.0.0.1:8001",
        "v1.6.0-dev121",
        "ca6948529b",
    )
    for name in ("docs/runbook.md", "docs/runbook.zh-CN.md"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        for required in required_fragments:
            assert required in text, f"{name} is missing {required!r}"


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
    assert "docs/local-qwen-service.md" in (PROJECT_ROOT / "README.md").read_text(
        encoding="utf-8"
    )
    assert "docs/local-qwen-service.zh.md" in (
        PROJECT_ROOT / "README.zh-CN.md"
    ).read_text(encoding="utf-8")


def test_qwen_guides_pin_and_smoke_test_the_model_service():
    for name in ("docs/local-qwen-service.md", "docs/local-qwen-service.zh.md"):
        path = PROJECT_ROOT / name
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
            PROJECT_ROOT / "docs/local-qwen-service.md",
            PROJECT_ROOT / "docs/local-qwen-service.zh.md",
        )
        if path.is_file()
    )

    for path in release_files:
        assert legacy_prefix not in path.read_text(encoding="utf-8")
