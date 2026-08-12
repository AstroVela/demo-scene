from __future__ import annotations

from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_vane_install_contract_is_exact_and_resolvable():
    project = tomllib.loads(
        (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    constraints = set(
        (PROJECT_ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines()
    )
    setup_script = (PROJECT_ROOT / "setup.sh").read_text(encoding="utf-8")

    assert "vane-ai[openai]==0.1.0" in project["dependencies"]
    assert "vane-ai==0.1.0" in constraints
    assert "python-dateutil==2.9.0.post0" in constraints
    assert "python-dateutil==3.9.0" not in constraints
    for fragment in (
        "--python \"$VENV_DIR/bin/python\"",
        "'vane-ai[openai]==0.1.0'",
        "-r \"$SCRIPT_DIR/requirements.txt\"",
    ):
        assert fragment in setup_script
    for fragment in ("test.pypi.org", "extra-index-url", "unsafe-best-match"):
        assert fragment not in setup_script


def test_readme_documents_ray_default_execution_contract():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    normalized_readme = " ".join(readme.split())

    assert "checked-in defaults use Vane's RayRunner" in readme
    assert "staged as Parquet" in readme
    assert "materialized again between query stages" in normalized_readme
    assert "--runner local --execution-backend local" in readme
    assert "LocalRunner cannot be combined with `ray_task`" in readme
    assert "ray_actor" not in readme
