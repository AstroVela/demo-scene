"""Photo VLM stage for the claims evidence graph POC."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa
from pydantic import BaseModel, Field, ValidationError
from vane.ai import prompt

from claims_evidence_graph_pipeline.contracts import (
    PHOTO_DAMAGE_EVIDENCE,
    PHOTO_MODEL_RUNS,
    ContractError,
    RunConfig,
    sha256_bytes,
    stable_json,
)


PHOTO_DAMAGE_SYSTEM_PROMPT = (
    "You inspect vehicle damage photos for an insurance claim evidence review. "
    "Return JSON only. Do not decide payout, denial, liability, or fraud. "
    "Use unknown when the image does not support a field."
)

PHOTO_DAMAGE_INSTRUCTION = (
    "Analyze the attached vehicle damage photo. Identify whether a vehicle is "
    "visible, whether the target vehicle is clear, whether damage is visible, "
    "the damaged parts, damage types, coarse severity, supporting visual "
    "description, uncertainty reasons, and confidence. Use controlled damage "
    "types such as dent, scratch, crack, glass_shatter, tire_flat, "
    "lamp_broken, unknown, or none_visible."
)


class PhotoDamageReport(BaseModel):
    vehicle_visible: bool = Field(default=False)
    target_vehicle_clear: bool = Field(default=False)
    damage_visible: bool = Field(default=False)
    damaged_parts: list[str] = Field(default_factory=list)
    damage_types: list[str] = Field(default_factory=list)
    severity_hint: str = Field(default="unknown")
    evidence_description: str = Field(default="")
    uncertainty_reasons: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_id(file_id: str, config: RunConfig) -> str:
    return (
        "model_run:photo_damage:"
        f"{file_id}:{config.prompt_version}:{config.response_schema_version}"
    )


def _raw_response_ref(run_id: str) -> str:
    return f"photo_model_runs:{run_id}"


def _json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        object_start = text.find("{")
        object_end = text.rfind("}")
        if object_start != -1 and object_end > object_start:
            return json.loads(text[object_start : object_end + 1])
        raise


def parse_photo_damage_report(raw_response: Any) -> PhotoDamageReport:
    if isinstance(raw_response, PhotoDamageReport):
        return raw_response
    return PhotoDamageReport.model_validate(_json_loads(raw_response))


def image_model_models_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/models"


def check_image_model_service(config: RunConfig, *, timeout: float = 5.0) -> dict[str, Any]:
    """Validate that the OpenAI-compatible VLM endpoint is reachable."""
    if config.image_model_provider != "openai":
        return {
            "status": "skipped",
            "reason": f"provider {config.image_model_provider!r} has no HTTP preflight",
        }

    url = image_model_models_url(config.image_model_base_url)
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {config.image_model_api_key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ContractError(
            "Image model service is not ready. Expected an OpenAI-compatible "
            f"VLM endpoint at {url}. Start Qwen with a command such as: "
            "vllm serve \"$HOME/models/Qwen2.5-VL-3B-Instruct\" "
            "--served-model-name Qwen2.5-VL-3B-Instruct --port 8001 "
            "--gpu-memory-utilization 0.85 --max-model-len 4096 "
            "--limit-mm-per-prompt image=4"
        ) from exc

    model_ids = [
        str(item.get("id"))
        for item in payload.get("data", [])
        if isinstance(item, dict) and item.get("id") is not None
    ]
    if config.image_model not in model_ids:
        raise ContractError(
            "Image model service is reachable but does not advertise "
            f"{config.image_model!r}. Available models: {model_ids}"
        )
    return {"status": "ok", "url": url, "models": model_ids}


def _needs_review(report: PhotoDamageReport) -> bool:
    return (
        not report.vehicle_visible
        or not report.target_vehicle_clear
        or report.confidence < 0.65
        or bool(report.uncertainty_reasons)
    )


def _error_damage_row(
    *,
    prompt_row: dict[str, Any],
    run_id: str,
    config: RunConfig,
    reason: str,
) -> dict[str, Any]:
    return {
        "claim_id": prompt_row["claim_id"],
        "file_id": prompt_row["file_id"],
        "vehicle_visible": False,
        "target_vehicle_clear": False,
        "damage_visible": False,
        "damaged_parts_json": stable_json([]),
        "damage_types_json": stable_json(["unknown"]),
        "severity_hint": "unknown",
        "evidence_description": "",
        "uncertainty_reasons_json": stable_json([reason]),
        "confidence": 0.0,
        "model_provider": config.image_model_provider,
        "model_name": config.image_model,
        "model_version": config.image_model_version,
        "prompt_version": config.prompt_version,
        "response_schema_version": config.response_schema_version,
        "model_run_id": run_id,
        "raw_response_ref": _raw_response_ref(run_id),
        "needs_review": True,
        "evidence_node_id": f"evidence:photo_damage:{prompt_row['file_id']}",
        "source_path": prompt_row["absolute_path"],
    }


def _model_error_damage_rows(
    *,
    prompt_rows: list[dict[str, Any]],
    config: RunConfig,
    reason: str,
) -> list[dict[str, Any]]:
    return [
        _error_damage_row(
            prompt_row=row,
            run_id=_run_id(str(row["file_id"]), config),
            config=config,
            reason=reason,
        )
        for row in prompt_rows
    ]


def _success_damage_row(
    *,
    prompt_row: dict[str, Any],
    run_id: str,
    config: RunConfig,
    report: PhotoDamageReport,
) -> dict[str, Any]:
    return {
        "claim_id": prompt_row["claim_id"],
        "file_id": prompt_row["file_id"],
        "vehicle_visible": report.vehicle_visible,
        "target_vehicle_clear": report.target_vehicle_clear,
        "damage_visible": report.damage_visible,
        "damaged_parts_json": stable_json(report.damaged_parts),
        "damage_types_json": stable_json(report.damage_types),
        "severity_hint": report.severity_hint,
        "evidence_description": report.evidence_description,
        "uncertainty_reasons_json": stable_json(report.uncertainty_reasons),
        "confidence": float(report.confidence),
        "model_provider": config.image_model_provider,
        "model_name": config.image_model,
        "model_version": config.image_model_version,
        "prompt_version": config.prompt_version,
        "response_schema_version": config.response_schema_version,
        "model_run_id": run_id,
        "raw_response_ref": _raw_response_ref(run_id),
        "needs_review": _needs_review(report),
        "evidence_node_id": f"evidence:photo_damage:{prompt_row['file_id']}",
        "source_path": prompt_row["absolute_path"],
    }


def _model_run_row(
    *,
    prompt_row: dict[str, Any],
    run_id: str,
    config: RunConfig,
    started_at: str,
    finished_at: str,
    latency_ms: float,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "claim_id": prompt_row["claim_id"],
        "file_id": prompt_row["file_id"],
        "provider": config.image_model_provider,
        "model_name": config.image_model,
        "model_version": config.image_model_version,
        "prompt_version": config.prompt_version,
        "schema_version": config.response_schema_version,
        "input_image_sha256": prompt_row["input_image_sha256"],
        "request_started_at": started_at,
        "request_finished_at": finished_at,
        "latency_ms": latency_ms,
        "status": status,
        "error_code": error_code,
        "error_message": error_message,
        "raw_response_ref": _raw_response_ref(run_id),
    }


def _prompt_input_table(rows: list[dict[str, Any]]) -> pa.Table:
    return pa.table(
        {
            "claim_id": pa.array([row["claim_id"] for row in rows], type=pa.string()),
            "file_id": pa.array([row["file_id"] for row in rows], type=pa.string()),
            "absolute_path": pa.array(
                [row["absolute_path"] for row in rows],
                type=pa.string(),
            ),
            "input_image_sha256": pa.array(
                [row["input_image_sha256"] for row in rows],
                type=pa.string(),
            ),
            "instruction": pa.array(
                [PHOTO_DAMAGE_INSTRUCTION for _ in rows],
                type=pa.string(),
            ),
            "file_bytes": pa.array([row["file_bytes"] for row in rows], type=pa.binary()),
        }
    )


def _eligible_prompt_rows(
    photo_input_table: pa.Table,
    photo_table: pa.Table,
) -> list[dict[str, Any]]:
    photo_by_file = {row["file_id"]: row for row in photo_table.to_pylist()}
    rows: list[dict[str, Any]] = []
    for row in photo_input_table.to_pylist():
        photo = photo_by_file.get(row["file_id"])
        if not photo or not photo["decode_ok"]:
            continue
        file_bytes = bytes(row["file_bytes"] or b"")
        rows.append(
            {
                "claim_id": row["claim_id"],
                "file_id": row["file_id"],
                "absolute_path": row["absolute_path"],
                "file_bytes": file_bytes,
                "input_image_sha256": sha256_bytes(file_bytes),
            }
        )
    return rows


def run_photo_damage_vlm(
    conn: Any,
    photo_input_table: pa.Table,
    photo_table: pa.Table,
    config: RunConfig,
) -> tuple[pa.Table, pa.Table]:
    prompt_rows = _eligible_prompt_rows(photo_input_table, photo_table)
    if not prompt_rows:
        return PHOTO_DAMAGE_EVIDENCE.arrow_table([]), PHOTO_MODEL_RUNS.arrow_table([])

    input_rel = conn.from_arrow(_prompt_input_table(prompt_rows))
    started_at = _utc_now()
    start = time.perf_counter()
    prompt_execution_backend = (
        "subprocess_task"
        if config.execution_backend == "local"
        else config.execution_backend
    )
    try:
        output_rel = prompt(
            input_rel,
            "instruction",
            image_columns=["file_bytes"],
            provider=config.image_model_provider,
            model=config.image_model,
            base_url=config.image_model_base_url,
            api_key=config.image_model_api_key,
            system_message=PHOTO_DAMAGE_SYSTEM_PROMPT,
            return_format=PhotoDamageReport,
            output_column="raw_response",
            execution_backend=prompt_execution_backend,
            max_tokens=config.image_model_max_tokens,
            temperature=config.image_model_temperature,
        )
        raw_values = output_rel.to_arrow_table().column("raw_response").to_pylist()
    except Exception as exc:
        finished_at = _utc_now()
        latency_ms = (time.perf_counter() - start) * 1000.0
        run_rows = [
            _model_run_row(
                prompt_row=row,
                run_id=_run_id(str(row["file_id"]), config),
                config=config,
                started_at=started_at,
                finished_at=finished_at,
                latency_ms=latency_ms,
                status="failed",
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            for row in prompt_rows
        ]
        if (
            config.enforces_image_model_error_budget
            and len(run_rows) > config.max_image_model_errors
        ):
            raise ContractError(
                "Image model failed for "
                f"{len(run_rows)} rows; max_image_model_errors="
                f"{config.max_image_model_errors}: {type(exc).__name__}: {exc}"
            ) from exc
        return (
            PHOTO_DAMAGE_EVIDENCE.arrow_table(
                _model_error_damage_rows(
                    prompt_rows=prompt_rows,
                    config=config,
                    reason="model_call_failed",
                )
            ),
            PHOTO_MODEL_RUNS.arrow_table(run_rows),
        )

    finished_at = _utc_now()
    latency_ms = (time.perf_counter() - start) * 1000.0
    if len(raw_values) != len(prompt_rows):
        error_message = (
            "Image model returned "
            f"{len(raw_values)} rows for {len(prompt_rows)} prompt rows"
        )
        run_rows = [
            _model_run_row(
                prompt_row=row,
                run_id=_run_id(str(row["file_id"]), config),
                config=config,
                started_at=started_at,
                finished_at=finished_at,
                latency_ms=latency_ms,
                status="failed",
                error_code="ModelOutputRowCountMismatch",
                error_message=error_message,
            )
            for row in prompt_rows
        ]
        if (
            config.enforces_image_model_error_budget
            and len(run_rows) > config.max_image_model_errors
        ):
            raise ContractError(
                "Image model produced "
                f"{len(run_rows)} non-success rows; max_image_model_errors="
                f"{config.max_image_model_errors}: {error_message}"
            )
        return (
            PHOTO_DAMAGE_EVIDENCE.arrow_table(
                _model_error_damage_rows(
                    prompt_rows=prompt_rows,
                    config=config,
                    reason="model_output_row_count_mismatch",
                )
            ),
            PHOTO_MODEL_RUNS.arrow_table(run_rows),
        )

    damage_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []

    for prompt_row, raw_response in zip(prompt_rows, raw_values, strict=True):
        run_id = _run_id(str(prompt_row["file_id"]), config)
        try:
            report = parse_photo_damage_report(raw_response)
        except (json.JSONDecodeError, TypeError, ValidationError, ValueError) as exc:
            run_rows.append(
                _model_run_row(
                    prompt_row=prompt_row,
                    run_id=run_id,
                    config=config,
                    started_at=started_at,
                    finished_at=finished_at,
                    latency_ms=latency_ms,
                    status="parse_error",
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                )
            )
            damage_rows.append(
                _error_damage_row(
                    prompt_row=prompt_row,
                    run_id=run_id,
                    config=config,
                    reason="model_output_parse_error",
                )
            )
            continue

        run_rows.append(
            _model_run_row(
                prompt_row=prompt_row,
                run_id=run_id,
                config=config,
                started_at=started_at,
                finished_at=finished_at,
                latency_ms=latency_ms,
                status="success",
            )
        )
        damage_rows.append(
            _success_damage_row(
                prompt_row=prompt_row,
                run_id=run_id,
                config=config,
                report=report,
            )
        )

    error_count = sum(row["status"] != "success" for row in run_rows)
    if (
        config.enforces_image_model_error_budget
        and error_count > config.max_image_model_errors
    ):
        raise ContractError(
            "Image model produced "
            f"{error_count} non-success rows; max_image_model_errors="
            f"{config.max_image_model_errors}"
        )

    return (
        PHOTO_DAMAGE_EVIDENCE.arrow_table(damage_rows),
        PHOTO_MODEL_RUNS.arrow_table(run_rows),
    )
