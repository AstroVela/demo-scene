from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import vane
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SRC_DIR))

import claims_evidence_graph_pipeline.cli as cli_module
import claims_evidence_graph_pipeline.quality_fixtures_cli as quality_cli_module
from claims_evidence_graph_pipeline.contracts import (
    CLAIM_FILES,
    ContractError,
    DOCUMENT_EVIDENCE,
    DOCUMENT_INPUT,
    PHOTO_DAMAGE_EVIDENCE,
    PHOTO_DAMAGE_EVAL_METRICS,
    PHOTO_EVAL_METRICS,
    PHOTO_HUMAN_LABELS,
    PHOTO_EVIDENCE,
    PHOTO_INPUT,
    PHOTO_MODEL_RUNS,
    RunConfig,
    stable_json,
)
from claims_evidence_graph_pipeline.evaluation import (
    evaluate_photo_damage,
    evaluate_photo_quality,
)
from claims_evidence_graph_pipeline.photo_vlm import (
    PHOTO_DAMAGE_RETURN_FORMAT,
    PhotoDamageReport,
    check_image_model_service,
    configure_image_model_credentials,
    run_photo_damage_vlm,
)
import claims_evidence_graph_pipeline.pipeline as pipeline_module
from claims_evidence_graph_pipeline.pipeline import (
    build_review_tasks,
    read_jsonl,
    run_pipeline,
)
from claims_evidence_graph_pipeline.quality_fixtures import (
    build_quality_fixture_workspace,
)
from claims_evidence_graph_pipeline.runner_workspace import RunnerWorkspace
from claims_evidence_graph_pipeline.udfs import (
    FUNSDDocumentExtractBatch,
    PhotoQualityBatch,
    map_batches_with_backend,
    quality_needs_review,
    quality_score,
    vane_execution_backend,
)
from claims_evidence_graph_pipeline.validation import (
    validate_label_inputs,
    validate_manifests,
    validate_outputs,
)


@pytest.fixture(scope="module", autouse=True)
def _use_local_vane_runner() -> None:
    vane.configure(runner="local")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def _local_run_config(**overrides: Any) -> RunConfig:
    return RunConfig(
        runner="local",
        execution_backend="local",
        **overrides,
    )


def _make_image(path: Path, *, color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (640, 480), color)
    image.save(path)


def _damage_response_json(
    *,
    confidence: float = 0.92,
    evidence_description: str = "Visible scratch on the rear bumper.",
) -> str:
    return json.dumps(
        {
            "vehicle_visible": True,
            "target_vehicle_clear": True,
            "damage_visible": True,
            "damaged_parts": ["rear_bumper"],
            "damage_types": ["scratch"],
            "severity_hint": "minor",
            "evidence_description": evidence_description,
            "uncertainty_reasons": [],
            "confidence": confidence,
        }
    )


def _fake_prompt_response(input_rel: object, raw_response: str) -> object:
    escaped = raw_response.replace("'", "''")
    return input_rel.query(
        "prompt_input",
        f"select *, '{escaped}' as raw_response from prompt_input",
    )


class _OpenAICompatibleFixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/v1/models":
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": self.server.model_id,
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": "fixture",
                        }
                    ],
                },
            )
            return
        self._send_json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": "not found"}})
            return

        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.server.requests.append(payload)
        self._send_json(
            200,
            {
                "id": "chatcmpl-fixture",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": self.server.model_id,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": self.server.response_json,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )


def _start_openai_compatible_fixture(
    *,
    model_id: str,
    response_json: str,
) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAICompatibleFixtureHandler)
    server.model_id = model_id
    server.response_json = response_json
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/v1"


def _make_minimal_workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace_root = tmp_path / "workspace"
    data_root = workspace_root / "claims-poc"

    photo_path = workspace_root / "raw/cardd/images/photo.jpg"
    document_path = (
        workspace_root
        / "raw/funsd/dataset/training_data/images/document.png"
    )
    annotation_path = (
        workspace_root
        / "raw/funsd/dataset/training_data/annotations/document.json"
    )
    _make_image(photo_path, color=(120, 140, 160))
    _make_image(document_path, color=(240, 240, 240))
    annotation_path.parent.mkdir(parents=True, exist_ok=True)
    annotation_path.write_text(
        json.dumps(
            {
                "form": [
                    {
                        "id": 0,
                        "label": "question",
                        "text": "Policy Number",
                        "box": [10, 10, 100, 30],
                        "words": [{"text": "Policy"}, {"text": "Number"}],
                    },
                    {
                        "id": 1,
                        "label": "answer",
                        "text": "",
                        "box": [110, 10, 180, 30],
                        "words": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    _write_jsonl(
        data_root / "manifests/claims.jsonl",
        [
            {
                "claim_id": "CLM-1",
                "scenario": "unit_test",
                "description": "test claim",
                "is_real_claim": False,
                "source_note": "unit test proxy",
            }
        ],
    )
    _write_jsonl(
        data_root / "manifests/claim_files.jsonl",
        [
            {
                "claim_id": "CLM-1",
                "file_id": "PHOTO-1",
                "role": "vehicle_damage_photo",
                "media_type": "image/jpeg",
                "source_dataset": "unit",
                "source_url": "unit://photo",
                "raw_path": "raw/cardd/images/photo.jpg",
                "poc_path": "raw/cardd/images/photo.jpg",
                "notes": "photo",
            },
            {
                "claim_id": "CLM-1",
                "file_id": "DOC-1",
                "role": "scanned_claim_or_estimate_form_proxy",
                "media_type": "image/png",
                "source_dataset": "unit",
                "source_url": "unit://document",
                "raw_path": (
                    "raw/funsd/dataset/training_data/images/document.png"
                ),
                "annotation_raw_path": (
                    "raw/funsd/dataset/training_data/annotations/document.json"
                ),
                "poc_path": (
                    "raw/funsd/dataset/training_data/images/document.png"
                ),
                "annotation_poc_path": (
                    "raw/funsd/dataset/training_data/annotations/document.json"
                ),
                "notes": "document",
            },
        ],
    )
    return data_root, workspace_root


def test_cli_defaults_to_baseline_profile(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["claims_evidence_graph.py"])

    args = cli_module.parse_args()

    assert args.mode == "offline"
    assert args.profile == "baseline"
    assert args.runner == "ray"
    assert args.execution_backend == "ray_task"


def test_cli_accepts_explicit_ray_relation_runner(monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["claims_evidence_graph.py", "--runner", "ray"],
    )

    assert cli_module.parse_args().runner == "ray"


@pytest.mark.parametrize(
    "parse_args",
    [cli_module.parse_args, quality_cli_module.parse_args],
    ids=["pipeline", "quality-fixtures"],
)
def test_clis_accept_ray_task_backend(
    monkeypatch,
    parse_args,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["claims_evidence_graph.py", "--execution-backend", "ray_task"],
    )

    assert parse_args().execution_backend == "ray_task"


@pytest.mark.parametrize(
    "parse_args",
    [cli_module.parse_args, quality_cli_module.parse_args],
    ids=["pipeline", "quality-fixtures"],
)
def test_clis_reject_ray_actor_backend(monkeypatch, parse_args) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["claims_evidence_graph.py", "--execution-backend", "ray_actor"],
    )

    with pytest.raises(SystemExit):
        parse_args()


def test_pipeline_rejects_ray_task_with_local_runner_before_loading_inputs(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        pipeline_module,
        "load_pipeline_inputs",
        lambda _config: pytest.fail(
            "inputs must not load for an incompatible runner/backend pair"
        ),
    )

    with pytest.raises(
        ContractError,
        match="execution-backend must be local when --runner is local",
    ):
        run_pipeline(
            RunConfig(
                runner="local",
                execution_backend="ray_task",
            ),
            print_summary=False,
        )


def test_udf_adapter_preserves_ray_task_backend() -> None:
    assert vane_execution_backend("ray_task") == "ray_task"


def test_udf_adapter_maps_local_to_subprocess_task() -> None:
    assert vane_execution_backend("local") == "subprocess_task"


def test_quality_score_flags_low_quality_image() -> None:
    score, flags = quality_score(
        width=128,
        height=128,
        brightness_mean=10.0,
        brightness_std=3.0,
        blur_score=0.0,
    )
    assert score < 0.6
    assert set(flags) == {"too_dark", "low_contrast", "blurry", "low_resolution"}


def test_quality_needs_review_when_any_quality_flag_exists() -> None:
    assert quality_needs_review(0.85, ["low_resolution"]) is True
    assert quality_needs_review(0.5, []) is True
    assert quality_needs_review(1.0, []) is False


def test_batch_processors_run_via_map_batches(tmp_path: Path) -> None:
    photo_path = tmp_path / "photo.jpg"
    document_path = tmp_path / "document.png"
    annotation_path = tmp_path / "document.json"
    _make_image(photo_path, color=(120, 140, 160))
    _make_image(document_path, color=(240, 240, 240))
    annotation_path.write_text(
        json.dumps(
            {
                "form": [
                    {
                        "id": 0,
                        "label": "question",
                        "text": "Policy Number",
                        "box": [10, 10, 100, 30],
                        "words": [{"text": "Policy"}, {"text": "Number"}],
                    },
                    {
                        "id": 1,
                        "label": "answer",
                        "text": "",
                        "box": [110, 10, 180, 30],
                        "words": [],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    photo_table = PHOTO_INPUT.arrow_table(
        [
            {
                "claim_id": "CLM-1",
                "file_id": "PHOTO-1",
                "file_size_bytes": photo_path.stat().st_size,
                "absolute_path": str(photo_path),
                "file_bytes": photo_path.read_bytes(),
            }
        ]
    )
    document_table = DOCUMENT_INPUT.arrow_table(
        [
            {
                "claim_id": "CLM-1",
                "file_id": "DOC-1",
                "absolute_path": str(document_path),
                "annotation_absolute_path": str(annotation_path),
            }
        ]
    )

    conn = vane.connect()
    photo_result = map_batches_with_backend(
        conn.from_arrow(photo_table),
        PhotoQualityBatch().__call__,
        schema=PHOTO_EVIDENCE.duckdb_schema(),
        batch_size=1,
        execution_backend="local",
    ).to_arrow_table().to_pylist()
    document_result = map_batches_with_backend(
        conn.from_arrow(document_table),
        FUNSDDocumentExtractBatch().__call__,
        schema=DOCUMENT_EVIDENCE.duckdb_schema(),
        batch_size=1,
        execution_backend="local",
    ).to_arrow_table().to_pylist()

    assert len(photo_result) == 1
    assert photo_result[0]["decode_ok"] is True
    assert photo_result[0]["file_id"] == "PHOTO-1"
    assert len(document_result) == 2
    assert document_result[0]["field_name"] == "question_0"
    assert document_result[1]["needs_review"] is True


def test_funsd_document_extract_batch_expands_forms(tmp_path: Path) -> None:
    annotation_path = tmp_path / "annotation.json"
    annotation_path.write_text(
        json.dumps(
            {
                "form": [
                    {"id": 0, "label": "question", "text": "Name", "box": [0, 0, 5, 5]},
                    {"id": 1, "label": "answer", "text": "", "box": [5, 0, 10, 5]},
                ]
            }
        ),
        encoding="utf-8",
    )
    table = DOCUMENT_INPUT.arrow_table(
        [
            {
                "claim_id": "CLM-1",
                "file_id": "DOC-1",
                "absolute_path": str(tmp_path / "document.png"),
                "annotation_absolute_path": str(annotation_path),
            }
        ]
    )

    result = FUNSDDocumentExtractBatch()(table).to_pylist()

    assert len(result) == 2
    assert result[0]["field_name"] == "question_0"
    assert result[0]["needs_review"] is False
    assert result[1]["field_name"] == "answer_1"
    assert result[1]["needs_review"] is True


def test_photo_quality_batch_records_decode_errors() -> None:
    table = PHOTO_INPUT.arrow_table(
        [
            {
                "claim_id": "CLM-1",
                "file_id": "PHOTO-BAD",
                "file_size_bytes": 12,
                "absolute_path": "/tmp/bad.jpg",
                "file_bytes": b"not an image",
            }
        ]
    )

    result = PhotoQualityBatch()(table).to_pylist()

    assert len(result) == 1
    assert result[0]["decode_ok"] is False
    assert "UnidentifiedImageError" in result[0]["decode_error"]
    assert result[0]["quality_score"] == 0.0
    assert json.loads(result[0]["issue_flags_json"]) == ["decode_error"]
    assert result[0]["needs_review"] is True


def test_photo_quality_batch_emits_real_data_quality_fields(tmp_path: Path) -> None:
    photo_path = tmp_path / "photo.jpg"
    _make_image(photo_path, color=(120, 140, 160))
    data = photo_path.read_bytes()
    table = PHOTO_INPUT.arrow_table(
        [
            {
                "claim_id": "CLM-1",
                "file_id": "PHOTO-1",
                "file_size_bytes": len(data),
                "absolute_path": str(photo_path),
                "file_bytes": data,
            }
        ]
    )

    result = PhotoQualityBatch()(table).to_pylist()[0]

    assert result["decode_ok"] is True
    assert result["decode_error"] is None
    assert result["image_format"] == "JPEG"
    assert result["image_mode"] == "RGB"
    assert result["aspect_ratio"] == 640 / 480
    assert result["megapixels"] == 640 * 480 / 1_000_000
    assert len(result["perceptual_hash"]) == 16
    assert result["quality_rule_version"] == "photo_quality_v2"


def test_photo_damage_vlm_uses_qwen_compatible_prompt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from claims_evidence_graph_pipeline import photo_vlm

    photo_path = tmp_path / "photo.jpg"
    _make_image(photo_path, color=(120, 140, 160))
    data = photo_path.read_bytes()
    con = vane.connect()
    calls = []

    def fake_prompt(input_rel, messages, **kwargs):
        calls.append((input_rel.select(*messages).columns, kwargs))
        return _fake_prompt_response(input_rel, _damage_response_json())

    monkeypatch.setattr(photo_vlm, "prompt", fake_prompt)
    photo_input_table = PHOTO_INPUT.arrow_table(
        [
            {
                "claim_id": "CLM-1",
                "file_id": "PHOTO-1",
                "file_size_bytes": len(data),
                "absolute_path": str(photo_path),
                "file_bytes": data,
            }
        ]
    )
    photo_table = PhotoQualityBatch()(photo_input_table)

    damage_table, run_table = run_photo_damage_vlm(
        RunnerWorkspace(tmp_path / "runner-workspace", con),
        photo_input_table,
        photo_table,
        _local_run_config(mode="ai"),
    )

    assert calls[0][0] == ["instruction", "file_bytes"]
    assert calls[0][1]["model"] == "Qwen2.5-VL-3B-Instruct"
    assert calls[0][1]["base_url"] == "http://127.0.0.1:8001/v1"
    assert calls[0][1]["use_chat_completions"] is True
    assert calls[0][1]["max_output_tokens"] == 768
    assert calls[0][1]["return_format"] is PHOTO_DAMAGE_RETURN_FORMAT
    assert damage_table.schema == PHOTO_DAMAGE_EVIDENCE.arrow_table([]).schema
    assert run_table.schema == PHOTO_MODEL_RUNS.arrow_table([]).schema
    damage = damage_table.to_pylist()[0]
    assert damage["file_id"] == "PHOTO-1"
    assert damage["damage_visible"] is True
    assert json.loads(damage["damaged_parts_json"]) == ["rear_bumper"]
    assert damage["needs_review"] is False
    assert run_table.to_pylist()[0]["status"] == "success"


def test_photo_damage_vlm_binds_reordered_results_by_input_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from claims_evidence_graph_pipeline import photo_vlm

    photo_rows = []
    for file_id, color in (
        ("PHOTO-1", (120, 140, 160)),
        ("PHOTO-2", (30, 60, 90)),
    ):
        photo_path = tmp_path / f"{file_id}.jpg"
        _make_image(photo_path, color=color)
        data = photo_path.read_bytes()
        photo_rows.append(
            {
                "claim_id": "CLM-1",
                "file_id": file_id,
                "file_size_bytes": len(data),
                "absolute_path": str(photo_path),
                "file_bytes": data,
            }
        )

    responses = {
        file_id: _damage_response_json(evidence_description=f"belongs-to-{file_id}")
        for file_id in ("PHOTO-1", "PHOTO-2")
    }

    def reversed_prompt_response(input_rel, messages, **kwargs):
        first = responses["PHOTO-1"].replace("'", "''")
        second = responses["PHOTO-2"].replace("'", "''")
        return input_rel.query(
            "prompt_input",
            f"""
            select
              *,
              case file_id
                when 'PHOTO-1' then '{first}'
                when 'PHOTO-2' then '{second}'
              end as raw_response
            from prompt_input
            order by file_id desc
            """,
        )

    monkeypatch.setattr(photo_vlm, "prompt", reversed_prompt_response)
    photo_input_table = PHOTO_INPUT.arrow_table(photo_rows)
    photo_table = PhotoQualityBatch()(photo_input_table)

    damage_table, run_table = run_photo_damage_vlm(
        RunnerWorkspace(tmp_path / "runner-workspace", vane.connect()),
        photo_input_table,
        photo_table,
        _local_run_config(mode="ai"),
    )

    descriptions = {
        row["file_id"]: row["evidence_description"]
        for row in damage_table.to_pylist()
    }
    assert descriptions == {
        "PHOTO-1": "belongs-to-PHOTO-1",
        "PHOTO-2": "belongs-to-PHOTO-2",
    }
    assert {row["status"] for row in run_table.to_pylist()} == {"success"}


def test_photo_damage_vlm_routes_row_count_mismatch_to_error_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from claims_evidence_graph_pipeline import photo_vlm

    photo_path = tmp_path / "photo.jpg"
    _make_image(photo_path, color=(120, 140, 160))
    data = photo_path.read_bytes()
    con = vane.connect()

    def short_prompt_response(input_rel, column, **kwargs):
        class ShortPromptResult:
            def select(self, *columns):
                return self

            def write_parquet(self, path: str) -> None:
                pq.write_table(
                    pa.table(
                        {
                            "file_id": pa.array([], type=pa.string()),
                            "input_image_sha256": pa.array([], type=pa.string()),
                            "raw_response": pa.array([], type=pa.string()),
                        }
                    ),
                    path,
                )

        return ShortPromptResult()

    monkeypatch.setattr(photo_vlm, "prompt", short_prompt_response)
    photo_input_table = PHOTO_INPUT.arrow_table(
        [
            {
                "claim_id": "CLM-1",
                "file_id": "PHOTO-1",
                "file_size_bytes": len(data),
                "absolute_path": str(photo_path),
                "file_bytes": data,
            }
        ]
    )
    photo_table = PhotoQualityBatch()(photo_input_table)

    damage_table, run_table = run_photo_damage_vlm(
        RunnerWorkspace(tmp_path / "runner-workspace", con),
        photo_input_table,
        photo_table,
        _local_run_config(profile="semantic"),
    )

    run = run_table.to_pylist()[0]
    assert run["status"] == "failed"
    assert run["error_code"] == "ModelOutputRowCountMismatch"
    assert "1 prompt rows" in run["error_message"]
    damage = damage_table.to_pylist()[0]
    assert damage["needs_review"] is True
    assert json.loads(damage["uncertainty_reasons_json"]) == [
        "model_output_row_count_mismatch"
    ]


def test_image_model_preflight_fails_when_endpoint_missing() -> None:
    try:
        check_image_model_service(
            _local_run_config(
                mode="ai",
                image_model_base_url="http://127.0.0.1:9/v1",
            ),
            timeout=0.1,
        )
    except ContractError as exc:
        message = str(exc)
    else:
        raise AssertionError("preflight unexpectedly succeeded")

    assert "Image model service is not ready" in message
    assert "vllm serve" in message


def test_photo_damage_vlm_calls_openai_compatible_http_endpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    photo_path = tmp_path / "photo.jpg"
    _make_image(photo_path, color=(120, 140, 160))
    data = photo_path.read_bytes()
    model_id = "Qwen2.5-VL-3B-Instruct"
    server, base_url = _start_openai_compatible_fixture(
        model_id=model_id,
        response_json=_damage_response_json(confidence=0.83),
    )

    try:
        config = _local_run_config(
            mode="ai",
            image_model=model_id,
            image_model_base_url=base_url,
            image_model_api_key="fixture-key",
        )
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        configure_image_model_credentials(config)
        assert check_image_model_service(config)["models"] == [model_id]

        photo_input_table = PHOTO_INPUT.arrow_table(
            [
                {
                    "claim_id": "CLM-1",
                    "file_id": "PHOTO-1",
                    "file_size_bytes": len(data),
                    "absolute_path": str(photo_path),
                    "file_bytes": data,
                }
            ]
        )
        photo_table = PhotoQualityBatch()(photo_input_table)
        damage_table, run_table = run_photo_damage_vlm(
            RunnerWorkspace(
                tmp_path / "runner-workspace",
                vane.connect(),
            ),
            photo_input_table,
            photo_table,
            config,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert damage_table.to_pylist()[0]["confidence"] == 0.83
    assert run_table.to_pylist()[0]["status"] == "success"
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request["model"] == model_id
    assert "response_format" in request
    content = request["messages"][-1]["content"]
    assert any(part.get("type") == "text" for part in content)
    image_parts = [part for part in content if part.get("type") == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_build_review_tasks_flags_decode_and_duplicate_photos(
    tmp_path: Path,
) -> None:
    photo_path = tmp_path / "photo.jpg"
    _make_image(photo_path, color=(120, 140, 160))
    data = photo_path.read_bytes()
    photo_table = PhotoQualityBatch()(
        PHOTO_INPUT.arrow_table(
            [
                {
                    "claim_id": "CLM-1",
                    "file_id": "PHOTO-1",
                    "file_size_bytes": len(data),
                    "absolute_path": str(photo_path),
                    "file_bytes": data,
                },
                {
                    "claim_id": "CLM-1",
                    "file_id": "PHOTO-2",
                    "file_size_bytes": len(data),
                    "absolute_path": str(photo_path),
                    "file_bytes": data,
                },
                {
                    "claim_id": "CLM-1",
                    "file_id": "PHOTO-BAD",
                    "file_size_bytes": 12,
                    "absolute_path": "/tmp/bad.jpg",
                    "file_bytes": b"not an image",
                },
            ]
        )
    )
    claim_file_table = CLAIM_FILES.arrow_table(
        [
            {
                "claim_id": "CLM-1",
                "file_id": "PHOTO-1",
                "role": "vehicle_damage_photo",
                "media_type": "image/jpeg",
                "source_dataset": "unit",
                "source_url": "unit://photo-1",
                "raw_path": "raw/photo-1.jpg",
                "poc_path": "raw/photo-1.jpg",
                "absolute_path": str(photo_path),
                "annotation_absolute_path": None,
                "file_exists": True,
                "file_size_bytes": len(data),
                "sha256": "sha-1",
            },
            {
                "claim_id": "CLM-1",
                "file_id": "PHOTO-2",
                "role": "vehicle_damage_photo",
                "media_type": "image/jpeg",
                "source_dataset": "unit",
                "source_url": "unit://photo-2",
                "raw_path": "raw/photo-2.jpg",
                "poc_path": "raw/photo-2.jpg",
                "absolute_path": str(photo_path),
                "annotation_absolute_path": None,
                "file_exists": True,
                "file_size_bytes": len(data),
                "sha256": "sha-2",
            },
            {
                "claim_id": "CLM-1",
                "file_id": "PHOTO-BAD",
                "role": "vehicle_damage_photo",
                "media_type": "image/jpeg",
                "source_dataset": "unit",
                "source_url": "unit://photo-bad",
                "raw_path": "raw/photo-bad.jpg",
                "poc_path": "raw/photo-bad.jpg",
                "absolute_path": "/tmp/bad.jpg",
                "annotation_absolute_path": None,
                "file_exists": True,
                "file_size_bytes": 12,
                "sha256": "sha-bad",
            },
            {
                "claim_id": "CLM-1",
                "file_id": "DOC-1",
                "role": "document",
                "media_type": "image/png",
                "source_dataset": "unit",
                "source_url": "unit://doc",
                "raw_path": "raw/doc.png",
                "poc_path": "raw/doc.png",
                "absolute_path": "/tmp/doc.png",
                "annotation_absolute_path": None,
                "file_exists": True,
                "file_size_bytes": 1,
                "sha256": "sha-doc",
            },
        ]
    )

    result = build_review_tasks(
        [{"claim_id": "CLM-1"}],
        claim_file_table,
        photo_table,
        DOCUMENT_EVIDENCE.arrow_table([]),
    ).to_pylist()

    task_types = {row["task_type"] for row in result}
    assert "photo_decode_review" in task_types
    assert "duplicate_photo_review" in task_types


def test_validate_manifests_rejects_duplicate_file_ids(tmp_path: Path) -> None:
    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    claims = [
        json.loads(line)
        for line in (data_root / "manifests/claims.jsonl").read_text().splitlines()
    ]
    files = [
        json.loads(line)
        for line in (
            data_root / "manifests/claim_files.jsonl"
        ).read_text().splitlines()
    ]
    files[1]["file_id"] = files[0]["file_id"]

    report = validate_manifests(claims, files, workspace_root)

    assert any("duplicate file_id" in error for error in report.errors)


def test_validate_manifests_rejects_empty_paths(tmp_path: Path) -> None:
    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    claims = [
        json.loads(line)
        for line in (data_root / "manifests/claims.jsonl").read_text().splitlines()
    ]
    files = [
        json.loads(line)
        for line in (
            data_root / "manifests/claim_files.jsonl"
        ).read_text().splitlines()
    ]
    files[0]["raw_path"] = " "
    files[0]["poc_path"] = ""

    report = validate_manifests(claims, files, workspace_root)

    assert any("raw_path must be non-empty" in error for error in report.errors)
    assert any("poc_path must be non-empty" in error for error in report.errors)


def test_validate_label_inputs_rejects_bad_photo_labels() -> None:
    file_rows = [
        {
            "claim_id": "CLM-1",
            "file_id": "PHOTO-1",
            "media_type": "image/jpeg",
        }
    ]
    report = validate_label_inputs(
        photo_label_rows=[
            {
                "claim_id": "CLM-1",
                "file_id": "MISSING",
                "usable_for_review": "yes",
                "vehicle_visible": True,
                "target_vehicle_clear": True,
                "damage_visible": True,
                "damaged_parts_json": stable_json(["door"]),
                "damage_types_json": stable_json(["scratch"]),
                "severity_label": "minor",
                "needs_reshoot": False,
                "labeler_id": "labeler-1",
                "labeled_at": "2026-06-16T00:00:00Z",
                "adjudication_status": "single_label",
            }
        ],
        file_rows=file_rows,
    )

    assert any("references unknown photo file" in error for error in report.errors)
    assert any("usable_for_review must be boolean" in error for error in report.errors)


def _photo_evidence_row(
    *,
    file_id: str,
    needs_review: bool,
) -> dict[str, object]:
    return {
        "claim_id": "CLM-1",
        "file_id": file_id,
        "decode_ok": True,
        "decode_error": None,
        "image_format": "JPEG",
        "image_mode": "RGB",
        "image_width": 640,
        "image_height": 480,
        "file_size_bytes": 1024,
        "aspect_ratio": 640 / 480,
        "megapixels": 640 * 480 / 1_000_000,
        "brightness_mean": 120.0,
        "brightness_std": 30.0,
        "blur_score": 10.0,
        "quality_score": 0.5 if needs_review else 0.8,
        "issue_flags_json": stable_json(["blurry"] if needs_review else []),
        "perceptual_hash": f"hash-{file_id}",
        "quality_rule_version": "photo_quality_v2",
        "needs_review": needs_review,
        "evidence_node_id": f"evidence:photo_quality:{file_id}",
        "source_path": f"/tmp/{file_id}.jpg",
    }


def _photo_label_row(
    *,
    file_id: str,
    usable_for_review: bool,
    needs_reshoot: bool,
    vehicle_visible: bool = True,
    target_vehicle_clear: bool = True,
    damage_visible: bool = True,
    damaged_parts: list[str] | None = None,
    damage_types: list[str] | None = None,
) -> dict[str, object]:
    return {
        "claim_id": "CLM-1",
        "file_id": file_id,
        "usable_for_review": usable_for_review,
        "vehicle_visible": vehicle_visible,
        "target_vehicle_clear": target_vehicle_clear,
        "damage_visible": damage_visible,
        "damaged_parts_json": stable_json(
            ["door"] if damaged_parts is None else damaged_parts
        ),
        "damage_types_json": stable_json(
            ["scratch"] if damage_types is None else damage_types
        ),
        "severity_label": "minor",
        "needs_reshoot": needs_reshoot,
        "labeler_id": "labeler-1",
        "labeled_at": "2026-06-16T00:00:00Z",
        "adjudication_status": "single_label",
    }


def _photo_damage_evidence_row(
    *,
    file_id: str,
    needs_review: bool,
    vehicle_visible: bool,
    target_vehicle_clear: bool,
    damage_visible: bool,
    damaged_parts: list[str] | None = None,
    damage_types: list[str] | None = None,
) -> dict[str, object]:
    return {
        "claim_id": "CLM-1",
        "file_id": file_id,
        "vehicle_visible": vehicle_visible,
        "target_vehicle_clear": target_vehicle_clear,
        "damage_visible": damage_visible,
        "damaged_parts_json": stable_json(
            ["door"] if damaged_parts is None else damaged_parts
        ),
        "damage_types_json": stable_json(
            ["scratch"] if damage_types is None else damage_types
        ),
        "severity_hint": "minor",
        "evidence_description": "unit evidence",
        "uncertainty_reasons_json": stable_json(["unit uncertainty"] if needs_review else []),
        "confidence": 0.5 if needs_review else 0.9,
        "model_provider": "openai",
        "model_name": "Qwen2.5-VL-3B-Instruct",
        "model_version": "",
        "prompt_version": "photo_damage_v1",
        "response_schema_version": "photo_damage_v1",
        "model_run_id": f"model_run:photo_damage:{file_id}:photo_damage_v1:photo_damage_v1",
        "raw_response_ref": (
            f"photo_model_runs:model_run:photo_damage:{file_id}:"
            "photo_damage_v1:photo_damage_v1"
        ),
        "needs_review": needs_review,
        "evidence_node_id": f"evidence:photo_damage:{file_id}",
        "source_path": f"/tmp/{file_id}.jpg",
    }


def test_evaluate_photo_quality_outputs_confusion_metrics() -> None:
    photo_table = PHOTO_EVIDENCE.arrow_table(
        [
            _photo_evidence_row(file_id="PHOTO-TP", needs_review=True),
            _photo_evidence_row(file_id="PHOTO-FP", needs_review=True),
            _photo_evidence_row(file_id="PHOTO-TN", needs_review=False),
            _photo_evidence_row(file_id="PHOTO-FN", needs_review=False),
        ]
    )
    label_table = PHOTO_HUMAN_LABELS.arrow_table(
        [
            _photo_label_row(
                file_id="PHOTO-TP",
                usable_for_review=False,
                needs_reshoot=False,
            ),
            _photo_label_row(
                file_id="PHOTO-FP",
                usable_for_review=True,
                needs_reshoot=False,
            ),
            _photo_label_row(
                file_id="PHOTO-TN",
                usable_for_review=True,
                needs_reshoot=False,
            ),
            _photo_label_row(
                file_id="PHOTO-FN",
                usable_for_review=True,
                needs_reshoot=True,
            ),
            _photo_label_row(
                file_id="PHOTO-MISSING",
                usable_for_review=False,
                needs_reshoot=True,
            ),
        ]
    )

    result = evaluate_photo_quality(photo_table, label_table)

    assert result.schema == PHOTO_EVAL_METRICS.arrow_table([]).schema
    metric = result.to_pylist()[0]
    assert metric["support"] == 4
    assert metric["unmatched_label_count"] == 1
    assert metric["true_positive"] == 1
    assert metric["false_positive"] == 1
    assert metric["true_negative"] == 1
    assert metric["false_negative"] == 1
    assert metric["precision"] == 0.5
    assert metric["recall"] == 0.5
    assert metric["f1"] == 0.5


def test_evaluate_photo_damage_outputs_field_metrics() -> None:
    damage_table = PHOTO_DAMAGE_EVIDENCE.arrow_table(
        [
            _photo_damage_evidence_row(
                file_id="PHOTO-TP",
                needs_review=True,
                vehicle_visible=True,
                target_vehicle_clear=True,
                damage_visible=True,
                damaged_parts=["door"],
                damage_types=["scratch"],
            ),
            _photo_damage_evidence_row(
                file_id="PHOTO-FP",
                needs_review=True,
                vehicle_visible=True,
                target_vehicle_clear=True,
                damage_visible=True,
                damaged_parts=["door"],
                damage_types=["scratch"],
            ),
            _photo_damage_evidence_row(
                file_id="PHOTO-TN",
                needs_review=False,
                vehicle_visible=False,
                target_vehicle_clear=False,
                damage_visible=False,
                damaged_parts=[],
                damage_types=[],
            ),
            _photo_damage_evidence_row(
                file_id="PHOTO-FN",
                needs_review=False,
                vehicle_visible=False,
                target_vehicle_clear=False,
                damage_visible=False,
                damaged_parts=[],
                damage_types=[],
            ),
        ]
    )
    label_table = PHOTO_HUMAN_LABELS.arrow_table(
        [
            _photo_label_row(
                file_id="PHOTO-TP",
                usable_for_review=False,
                needs_reshoot=True,
                vehicle_visible=True,
                target_vehicle_clear=True,
                damage_visible=True,
                damaged_parts=["door"],
                damage_types=["scratch"],
            ),
            _photo_label_row(
                file_id="PHOTO-FP",
                usable_for_review=True,
                needs_reshoot=False,
                vehicle_visible=False,
                target_vehicle_clear=False,
                damage_visible=False,
                damaged_parts=[],
                damage_types=[],
            ),
            _photo_label_row(
                file_id="PHOTO-TN",
                usable_for_review=True,
                needs_reshoot=False,
                vehicle_visible=False,
                target_vehicle_clear=False,
                damage_visible=False,
                damaged_parts=[],
                damage_types=[],
            ),
            _photo_label_row(
                file_id="PHOTO-FN",
                usable_for_review=False,
                needs_reshoot=True,
                vehicle_visible=True,
                target_vehicle_clear=True,
                damage_visible=True,
                damaged_parts=["door"],
                damage_types=["scratch"],
            ),
            _photo_label_row(
                file_id="PHOTO-MISSING",
                usable_for_review=False,
                needs_reshoot=True,
                vehicle_visible=True,
                target_vehicle_clear=True,
                damage_visible=True,
                damaged_parts=["door"],
                damage_types=["scratch"],
            ),
        ]
    )

    result = evaluate_photo_damage(damage_table, label_table)

    assert result.schema == PHOTO_DAMAGE_EVAL_METRICS.arrow_table([]).schema
    metrics = {row["metric_name"]: row for row in result.to_pylist()}
    assert set(metrics) == {
        "photo_damage_vehicle_visible",
        "photo_damage_target_vehicle_clear",
        "photo_damage_damage_visible",
        "photo_damage_damaged_parts_overlap",
        "photo_damage_damage_types_overlap",
        "photo_damage_needs_review",
    }
    for metric in metrics.values():
        assert metric["support"] == 4
        assert metric["unmatched_label_count"] == 1
        assert metric["true_positive"] == 1
        assert metric["false_positive"] == 1
        assert metric["true_negative"] == 1
        assert metric["false_negative"] == 1
        assert metric["precision"] == 0.5
        assert metric["recall"] == 0.5
        assert metric["f1"] == 0.5


def test_run_pipeline_writes_optional_label_outputs(tmp_path: Path) -> None:
    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    output_dir = tmp_path / "outputs"
    photo_labels_path = tmp_path / "labels/photo_labels.jsonl"
    _write_jsonl(
        photo_labels_path,
        [
            {
                "claim_id": "CLM-1",
                "file_id": "PHOTO-1",
                "usable_for_review": True,
                "vehicle_visible": True,
                "target_vehicle_clear": True,
                "damage_visible": True,
                "damaged_parts_json": ["door"],
                "damage_types_json": ["scratch"],
                "severity_label": "minor",
                "needs_reshoot": False,
                "labeler_id": "labeler-1",
                "labeled_at": "2026-06-16T00:00:00Z",
                "adjudication_status": "single_label",
            }
        ],
    )

    result = run_pipeline(
        _local_run_config(
            data_root=data_root,
            workspace_root=workspace_root,
            output_dir=output_dir,
            write_parquet=False,
            photo_labels_path=photo_labels_path,
        ),
        print_summary=False,
    )

    assert result.tables["photo_human_labels"].num_rows == 1
    assert result.tables["photo_eval_metrics"].num_rows == 1
    assert result.tables["photo_human_labels"].schema == PHOTO_HUMAN_LABELS.arrow_table(
        []
    ).schema
    assert result.tables["photo_eval_metrics"].schema == (
        PHOTO_EVAL_METRICS.arrow_table([]).schema
    )
    assert result.metadata["input_validation"]["facts"]["photo_label_rows"] == 1
    assert (output_dir / "photo_human_labels.jsonl").exists()
    assert (output_dir / "photo_eval_metrics.jsonl").exists()


def test_quality_fixture_workspace_exercises_photo_review_and_eval(
    tmp_path: Path,
) -> None:
    paths = build_quality_fixture_workspace(tmp_path / "fixture-workspace")

    result = run_pipeline(
        _local_run_config(
            data_root=paths.data_root,
            workspace_root=paths.workspace_root,
            output_dir=paths.output_dir,
            write_parquet=False,
            photo_labels_path=paths.photo_labels_path,
        ),
        print_summary=False,
    )

    photos = {
        row["file_id"]: row for row in result.tables["photo_evidence"].to_pylist()
    }
    assert result.tables["claim_files"].num_rows == 8
    assert result.tables["photo_evidence"].num_rows == 7
    assert result.tables["document_evidence"].num_rows == 1
    assert result.tables["review_tasks"].num_rows == 5
    assert photos["PHOTO-GOOD"]["needs_review"] is False
    assert photos["PHOTO-LOW-RES"]["needs_review"] is True
    assert photos["PHOTO-LOW-RES"]["issue_flags_json"] == '["low_resolution"]'
    assert photos["PHOTO-CORRUPT"]["decode_ok"] is False

    task_types = {row["task_type"] for row in result.tables["review_tasks"].to_pylist()}
    assert task_types == {
        "duplicate_photo_review",
        "photo_decode_review",
        "photo_quality_review",
    }

    metric = result.tables["photo_eval_metrics"].to_pylist()[0]
    assert metric["support"] == 7
    assert metric["unmatched_label_count"] == 0
    assert metric["true_positive"] == 4
    assert metric["false_positive"] == 0
    assert metric["true_negative"] == 3
    assert metric["false_negative"] == 0
    assert metric["precision"] == 1.0
    assert metric["recall"] == 1.0
    assert metric["f1"] == 1.0


def test_run_pipeline_ai_mode_writes_photo_model_tables(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from claims_evidence_graph_pipeline import photo_vlm

    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    output_dir = tmp_path / "outputs"

    def fake_prompt(input_rel, messages, **kwargs):
        assert input_rel.select(*messages).columns == ["instruction", "file_bytes"]
        return _fake_prompt_response(input_rel, _damage_response_json())

    monkeypatch.setattr(photo_vlm, "prompt", fake_prompt)
    monkeypatch.setattr(
        pipeline_module,
        "check_image_model_service",
        lambda config: {"status": "ok", "models": [config.image_model]},
    )

    result = run_pipeline(
        _local_run_config(
            data_root=data_root,
            workspace_root=workspace_root,
            output_dir=output_dir,
            mode="ai",
            write_parquet=False,
        ),
        print_summary=False,
    )

    assert result.tables["photo_damage_evidence"].num_rows == 1
    assert result.tables["photo_model_runs"].num_rows == 1
    assert result.tables["photo_model_runs"].to_pylist()[0]["status"] == "success"
    assert result.metadata["image_model"]["model"] == "Qwen2.5-VL-3B-Instruct"
    evidence_nodes = result.tables["evidence_nodes"].to_pylist()
    assert any(row["node_type"] == "photo_damage" for row in evidence_nodes)
    assert (output_dir / "photo_damage_evidence.jsonl").exists()
    assert (output_dir / "photo_model_runs.jsonl").exists()


def test_run_pipeline_semantic_profile_writes_photo_model_tables(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from claims_evidence_graph_pipeline import photo_vlm

    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    output_dir = tmp_path / "outputs"

    def fake_prompt(input_rel, messages, **kwargs):
        assert input_rel.select(*messages).columns == ["instruction", "file_bytes"]
        return _fake_prompt_response(input_rel, _damage_response_json())

    monkeypatch.setattr(photo_vlm, "prompt", fake_prompt)
    monkeypatch.setattr(
        pipeline_module,
        "check_image_model_service",
        lambda config: {"status": "ok", "models": [config.image_model]},
    )

    result = run_pipeline(
        _local_run_config(
            data_root=data_root,
            workspace_root=workspace_root,
            output_dir=output_dir,
            profile="semantic",
            write_parquet=False,
        ),
        print_summary=False,
    )

    assert result.metadata["mode"] == "offline"
    assert result.metadata["profile"] == "semantic"
    assert result.metadata["image_semantics_required"] is True
    assert result.tables["photo_damage_evidence"].num_rows == 1
    assert result.tables["photo_model_runs"].num_rows == 1
    assert result.validation.facts["semantic_required"] is True
    assert result.validation.facts["expected_semantic_photo_count"] == 1
    assert (output_dir / "photo_damage_evidence.jsonl").exists()
    assert (output_dir / "photo_model_runs.jsonl").exists()


def test_run_pipeline_baseline_profile_omits_semantic_tables(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    output_dir = tmp_path / "outputs"

    def fail_preflight(config):
        raise AssertionError("baseline profile should not check image model service")

    monkeypatch.setattr(pipeline_module, "check_image_model_service", fail_preflight)

    result = run_pipeline(
        _local_run_config(
            data_root=data_root,
            workspace_root=workspace_root,
            output_dir=output_dir,
            profile="baseline",
            write_parquet=False,
        ),
        print_summary=False,
    )

    assert result.metadata["profile"] == "baseline"
    assert result.metadata["image_semantics_required"] is False
    assert result.metadata["image_model"]["model"] is None
    assert result.validation.facts["semantic_required"] is False
    assert "photo_damage_evidence" not in result.tables
    assert "photo_model_runs" not in result.tables
    assert not (output_dir / "photo_damage_evidence.jsonl").exists()
    assert not (output_dir / "photo_model_runs.jsonl").exists()


def test_validate_outputs_requires_complete_semantic_tables(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from claims_evidence_graph_pipeline import photo_vlm

    data_root, workspace_root = _make_minimal_workspace(tmp_path)

    def fake_prompt(input_rel, column, **kwargs):
        return _fake_prompt_response(input_rel, _damage_response_json())

    monkeypatch.setattr(photo_vlm, "prompt", fake_prompt)
    monkeypatch.setattr(
        pipeline_module,
        "check_image_model_service",
        lambda config: {"status": "ok", "models": [config.image_model]},
    )

    result = run_pipeline(
        _local_run_config(
            data_root=data_root,
            workspace_root=workspace_root,
            output_dir=tmp_path / "outputs",
            profile="semantic",
            write_parquet=False,
        ),
        print_summary=False,
    )
    incomplete_tables = dict(result.tables)
    incomplete_tables.pop("photo_model_runs")
    incomplete_tables["photo_damage_evidence"] = PHOTO_DAMAGE_EVIDENCE.arrow_table([])

    report = validate_outputs(
        incomplete_tables,
        expected_claim_count=1,
        expected_file_count=2,
        expected_photo_count=1,
        expected_document_count=1,
        semantic_required=True,
        expected_semantic_photo_count=1,
    )

    assert report.ok is False
    assert "photo_model_runs is required by semantic profile" in report.errors
    assert any(
        error.startswith("photo_damage_evidence row count mismatch")
        for error in report.errors
    )


def test_run_pipeline_clears_stale_optional_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from claims_evidence_graph_pipeline import photo_vlm

    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    output_dir = tmp_path / "outputs"

    def fake_prompt(input_rel, column, **kwargs):
        return _fake_prompt_response(input_rel, _damage_response_json())

    monkeypatch.setattr(photo_vlm, "prompt", fake_prompt)
    monkeypatch.setattr(
        pipeline_module,
        "check_image_model_service",
        lambda config: {"status": "ok", "models": [config.image_model]},
    )

    run_pipeline(
        _local_run_config(
            data_root=data_root,
            workspace_root=workspace_root,
            output_dir=output_dir,
            mode="ai",
        ),
        print_summary=False,
    )
    assert (output_dir / "photo_damage_evidence.jsonl").exists()
    assert (
        output_dir / "parquet" / "photo_damage_evidence" / "part-00000.parquet"
    ).exists()

    run_pipeline(
        _local_run_config(
            data_root=data_root,
            workspace_root=workspace_root,
            output_dir=output_dir,
            mode="offline",
            write_parquet=False,
        ),
        print_summary=False,
    )

    assert not (output_dir / "photo_damage_evidence.jsonl").exists()
    assert not (output_dir / "photo_model_runs.jsonl").exists()
    assert not (output_dir / "parquet" / "photo_damage_evidence").exists()
    assert not (output_dir / "parquet" / "photo_model_runs").exists()


def test_run_pipeline_ai_mode_writes_photo_damage_eval_metrics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from claims_evidence_graph_pipeline import photo_vlm

    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    output_dir = tmp_path / "outputs"
    photo_labels_path = tmp_path / "labels/photo_labels.jsonl"
    _write_jsonl(
        photo_labels_path,
        [
            _photo_label_row(
                file_id="PHOTO-1",
                usable_for_review=True,
                needs_reshoot=False,
                vehicle_visible=True,
                target_vehicle_clear=True,
                damage_visible=True,
                damaged_parts=["rear_bumper"],
                damage_types=["scratch"],
            )
        ],
    )
    worker_environment = {}

    def fake_prompt(input_rel, messages, **kwargs):
        assert worker_environment == {"OPENAI_API_KEY": "fixture-key"}
        assert input_rel.select(*messages).columns == ["instruction", "file_bytes"]
        return _fake_prompt_response(input_rel, _damage_response_json())

    def prewarm_worker(*, runner):
        assert runner == "local"
        worker_environment["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY")

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(photo_vlm, "prompt", fake_prompt)
    monkeypatch.setattr(pipeline_module.vane, "configure", prewarm_worker)
    monkeypatch.setattr(
        pipeline_module,
        "check_image_model_service",
        lambda config: {"status": "ok", "models": [config.image_model]},
    )

    result = run_pipeline(
        _local_run_config(
            data_root=data_root,
            workspace_root=workspace_root,
            output_dir=output_dir,
            mode="ai",
            write_parquet=False,
            photo_labels_path=photo_labels_path,
            image_model_api_key="fixture-key",
        ),
        print_summary=False,
    )

    metrics = result.tables["photo_damage_eval_metrics"].to_pylist()
    assert result.tables["photo_damage_eval_metrics"].schema == (
        PHOTO_DAMAGE_EVAL_METRICS.arrow_table([]).schema
    )
    assert len(metrics) == 6
    assert {
        row["metric_name"] for row in metrics
    } == {
        "photo_damage_vehicle_visible",
        "photo_damage_target_vehicle_clear",
        "photo_damage_damage_visible",
        "photo_damage_damaged_parts_overlap",
        "photo_damage_damage_types_overlap",
        "photo_damage_needs_review",
    }
    assert all(row["support"] == 1 for row in metrics)
    assert all(row["unmatched_label_count"] == 0 for row in metrics)
    assert (output_dir / "photo_damage_eval_metrics.jsonl").exists()


def test_run_pipeline_ai_mode_accepts_openai_compatible_http_endpoint(
    tmp_path: Path,
) -> None:
    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    output_dir = tmp_path / "outputs"
    model_id = "Qwen2.5-VL-3B-Instruct"
    server, base_url = _start_openai_compatible_fixture(
        model_id=model_id,
        response_json=_damage_response_json(confidence=0.74),
    )

    try:
        result = run_pipeline(
            _local_run_config(
                data_root=data_root,
                workspace_root=workspace_root,
                output_dir=output_dir,
                mode="ai",
                write_parquet=False,
                image_model=model_id,
                image_model_base_url=base_url,
                image_model_api_key="fixture-key",
            ),
            print_summary=False,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert len(server.requests) == 1
    assert result.tables["photo_damage_evidence"].num_rows == 1
    assert result.tables["photo_model_runs"].to_pylist()[0]["status"] == "success"
    assert result.metadata["image_model"]["base_url"] == base_url
    damage = result.tables["photo_damage_evidence"].to_pylist()[0]
    assert damage["damage_visible"] is True
    assert damage["model_name"] == model_id
    assert (output_dir / "photo_damage_evidence.jsonl").exists()
    assert (output_dir / "photo_model_runs.jsonl").exists()


def test_run_pipeline_ai_prompt_failure_review_references_existing_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from claims_evidence_graph_pipeline import photo_vlm

    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    output_dir = tmp_path / "outputs"

    def failing_prompt(*args, **kwargs):
        raise RuntimeError("unit prompt failure")

    monkeypatch.setattr(photo_vlm, "prompt", failing_prompt)
    monkeypatch.setattr(
        pipeline_module,
        "check_image_model_service",
        lambda config: {"status": "ok", "models": [config.image_model]},
    )

    result = run_pipeline(
        _local_run_config(
            data_root=data_root,
            workspace_root=workspace_root,
            output_dir=output_dir,
            mode="ai",
            write_parquet=False,
            max_image_model_errors=1,
        ),
        print_summary=False,
    )

    run = result.tables["photo_model_runs"].to_pylist()[0]
    assert run["status"] == "failed"
    assert result.tables["photo_damage_evidence"].num_rows == 1
    damage = result.tables["photo_damage_evidence"].to_pylist()[0]
    assert damage["needs_review"] is True
    assert json.loads(damage["uncertainty_reasons_json"]) == ["model_call_failed"]

    model_tasks = [
        row
        for row in result.tables["review_tasks"].to_pylist()
        if row["task_type"] == "model_output_review"
    ]
    damage_tasks = [
        row
        for row in result.tables["review_tasks"].to_pylist()
        if row["task_type"] == "photo_damage_review"
    ]
    assert len(model_tasks) == 1
    assert damage_tasks == []
    node_ids = {row["node_id"] for row in result.tables["evidence_nodes"].to_pylist()}
    assert model_tasks[0]["evidence_node_id"] in node_ids
    assert model_tasks[0]["evidence_node_id"].startswith("evidence:photo_damage:")


def test_run_pipeline_semantic_profile_routes_model_failure_to_error_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from claims_evidence_graph_pipeline import photo_vlm

    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    output_dir = tmp_path / "outputs"

    def failing_prompt(*args, **kwargs):
        raise RuntimeError("unit prompt failure")

    monkeypatch.setattr(photo_vlm, "prompt", failing_prompt)
    monkeypatch.setattr(
        pipeline_module,
        "check_image_model_service",
        lambda config: {"status": "ok", "models": [config.image_model]},
    )

    result = run_pipeline(
        _local_run_config(
            data_root=data_root,
            workspace_root=workspace_root,
            output_dir=output_dir,
            profile="semantic",
            write_parquet=False,
        ),
        print_summary=False,
    )

    assert result.tables["photo_model_runs"].to_pylist()[0]["status"] == "failed"
    damage = result.tables["photo_damage_evidence"].to_pylist()[0]
    assert damage["needs_review"] is True
    assert json.loads(damage["uncertainty_reasons_json"]) == ["model_call_failed"]
    assert [
        row
        for row in result.tables["review_tasks"].to_pylist()
        if row["task_type"] == "photo_damage_review"
    ] == []
    assert result.validation.facts["expected_semantic_photo_count"] == 1


def test_run_pipeline_semantic_strict_fails_on_model_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from claims_evidence_graph_pipeline import photo_vlm

    data_root, workspace_root = _make_minimal_workspace(tmp_path)

    def failing_prompt(*args, **kwargs):
        raise RuntimeError("unit prompt failure")

    monkeypatch.setattr(photo_vlm, "prompt", failing_prompt)
    monkeypatch.setattr(
        pipeline_module,
        "check_image_model_service",
        lambda config: {"status": "ok", "models": [config.image_model]},
    )

    try:
        run_pipeline(
            _local_run_config(
                data_root=data_root,
                workspace_root=workspace_root,
                output_dir=tmp_path / "outputs",
                profile="semantic_strict",
                write_parquet=False,
            ),
            print_summary=False,
        )
    except ContractError as exc:
        message = str(exc)
    else:
        raise AssertionError("semantic_strict unexpectedly tolerated model failure")

    assert "Image model failed" in message


def test_run_pipeline_end_to_end_writes_contract_outputs(tmp_path: Path) -> None:
    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    output_dir = tmp_path / "outputs"

    result = run_pipeline(
        _local_run_config(
            data_root=data_root,
            workspace_root=workspace_root,
            output_dir=output_dir,
            write_parquet=False,
        ),
        print_summary=False,
    )

    assert result.tables["claim_files"].num_rows == 2
    assert result.tables["photo_evidence"].num_rows == 1
    assert result.tables["document_evidence"].num_rows == 2
    assert result.tables["review_tasks"].num_rows == 2
    assert {
        row["task_type"] for row in result.tables["review_tasks"].to_pylist()
    } == {"photo_quality_review", "document_field_review"}
    assert result.tables["claim_summary"].to_pylist()[0]["claim_packet_status"] == (
        "needs_review"
    )
    assert (output_dir / "claim_files.jsonl").exists()
    assert (output_dir / "validation_report.json").exists()
    assert json.loads((output_dir / "validation_report.json").read_text())[
        "output"
    ]["ok"]


def test_ray_runner_executes_semantic_pipeline_end_to_end(tmp_path: Path) -> None:
    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    output_dir = tmp_path / "ray-outputs"
    model_id = "Qwen2.5-VL-3B-Instruct"
    server, base_url = _start_openai_compatible_fixture(
        model_id=model_id,
        response_json=_damage_response_json(confidence=0.81),
    )
    env = os.environ.copy()
    env.update(
        {
            "RAY_ADDRESS": "local",
            "RAY_LOG_TO_DRIVER": "0",
            "VANE_PROGRESS": "0",
        }
    )

    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "claims_evidence_graph.py"),
                "--workspace-root",
                str(workspace_root),
                "--data-root",
                str(data_root),
                "--output-dir",
                str(output_dir),
                "--profile",
                "semantic",
                "--runner",
                "ray",
                "--execution-backend",
                "ray_task",
                "--image-model",
                model_id,
                "--image-model-base-url",
                base_url,
                "--image-model-api-key",
                "fixture-key",
                "--skip-parquet",
            ],
            cwd=PROJECT_ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert completed.returncode == 0, completed.stdout + completed.stderr
    metadata = json.loads((output_dir / "run_metadata.json").read_text())
    assert metadata["runner"] == "ray"
    assert metadata["execution_backend"] == "ray_task"
    model_runs = read_jsonl(output_dir / "photo_model_runs.jsonl")
    assert [row["status"] for row in model_runs] == ["success"]
    assert len(server.requests) == 1


def test_run_pipeline_summarizes_claim_with_no_files(tmp_path: Path) -> None:
    data_root, workspace_root = _make_minimal_workspace(tmp_path)
    output_dir = tmp_path / "outputs"
    claims_path = data_root / "manifests/claims.jsonl"
    with claims_path.open("a", encoding="utf-8") as output_file:
        output_file.write(
            json.dumps(
                {
                    "claim_id": "CLM-EMPTY",
                    "scenario": "unit_test",
                    "description": "claim with no attached files",
                    "is_real_claim": False,
                    "source_note": "unit test proxy",
                }
            )
        )
        output_file.write("\n")

    result = run_pipeline(
        _local_run_config(
            data_root=data_root,
            workspace_root=workspace_root,
            output_dir=output_dir,
            write_parquet=False,
        ),
        print_summary=False,
    )

    summaries = {
        row["claim_id"]: row for row in result.tables["claim_summary"].to_pylist()
    }
    assert summaries["CLM-EMPTY"]["file_count"] == 0
    assert summaries["CLM-EMPTY"]["photo_count"] == 0
    assert summaries["CLM-EMPTY"]["document_count"] == 0
    assert summaries["CLM-EMPTY"]["claim_packet_status"] == (
        "missing_required_materials"
    )
    tasks = result.tables["review_tasks"].to_pylist()
    assert any(
        row["claim_id"] == "CLM-EMPTY"
        and row["task_type"] == "missing_material_review"
        for row in tasks
    )


def test_qwen_adapter_help_does_not_import_model_dependencies() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "claims_qwen_openai_server.py"),
            "--help",
        ],
        capture_output=True,
        check=True,
        text=True,
    )

    assert "--model-path" in result.stdout


def test_qwen_adapter_generate_imports_torch_in_request_path(
    monkeypatch,
) -> None:
    module_path = SCRIPTS_DIR / "claims_qwen_openai_server.py"
    spec = importlib.util.spec_from_file_location(
        "claims_qwen_openai_server",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class FakeInferenceMode:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, traceback):
            return False

    class FakeInputs(dict):
        input_ids = [[1, 2]]

        def to(self, device):
            return self

    class FakeProcessor:
        def apply_chat_template(self, *args, **kwargs):
            return "prompt"

        def __call__(self, *args, **kwargs):
            return FakeInputs(input_ids=[[1, 2]])

        def batch_decode(self, *args, **kwargs):
            return [
                json.dumps(
                    {
                        "vehicle_visible": True,
                        "target_vehicle_clear": True,
                        "damage_visible": True,
                        "damaged_parts": ["rear_bumper"],
                        "damage_types": ["scratch"],
                        "severity_hint": "minor",
                        "evidence_description": "Visible scratch.",
                        "uncertainty_reasons": [],
                        "confidence": 0.9,
                    }
                )
            ]

    class FakeModel:
        device = "cpu"

        def generate(self, **kwargs):
            return [[1, 2, 3]]

    fake_torch = types.ModuleType("torch")
    fake_torch.inference_mode = lambda: FakeInferenceMode()
    fake_qwen_utils = types.ModuleType("qwen_vl_utils")
    fake_qwen_utils.process_vision_info = lambda messages: ([], [])
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "qwen_vl_utils", fake_qwen_utils)
    monkeypatch.setattr(module, "load_model", lambda: None)
    module.MODEL = FakeModel()
    module.PROCESSOR = FakeProcessor()
    module.ARGS = types.SimpleNamespace(max_new_tokens=16)

    response = module.generate_chat_completion(
        {
            "messages": [{"role": "user", "content": "analyze"}],
            "temperature": 0,
        }
    )

    parsed = json.loads(response)
    assert parsed["vehicle_visible"] is True
