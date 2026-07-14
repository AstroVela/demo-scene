from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pyarrow as pa


TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")
PUBLIC_BACKEND_CHOICES = ("auto", "subprocess_task", "ray_task")


def batch_udf_options(execution_backend: str) -> dict[str, str]:
    if execution_backend == "auto":
        return {}
    return {"execution_backend": execution_backend}


def backend_metadata_entry(execution_backend: str) -> dict[str, Any]:
    inferred = execution_backend == "auto"
    return {
        "requested_backend": execution_backend,
        "actual_backend": None if inferred else execution_backend,
        "resolution": "vane_inferred" if inferred else "explicit",
        "fallback_reason": "",
    }


def require_local_relation_runner(runner: str | None) -> str:
    normalized = str(runner or "").strip().lower()
    if normalized == "ray":
        raise RuntimeError(
            "runner='ray' is not supported by these examples because their named "
            "tables live in the client connection; use a native runner and select "
            "--execution-backend ray_task when Ray-backed batch UDFs are required"
        )
    return normalized or "native"


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text or "")]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def table_from_rows(rows: list[dict[str, Any]], schema: dict[str, pa.DataType]) -> pa.Table:
    return pa.table(
        {
            name: pa.array([row.get(name) for row in rows], data_type)
            for name, data_type in schema.items()
        }
    )


def merge_backend_metadata(stage_metadata: dict[str, dict[str, Any]]) -> dict[str, Any]:
    actual_backends = {
        metadata["actual_backend"] for metadata in stage_metadata.values()
    }
    fallback_reasons = {
        stage: metadata["fallback_reason"]
        for stage, metadata in stage_metadata.items()
        if metadata.get("fallback_reason")
    }
    result = {
        "requested_execution_backend": next(
            iter(stage_metadata.values())
        )["requested_backend"]
        if stage_metadata
        else "",
        "execution_backend": next(iter(actual_backends))
        if len(actual_backends) == 1
        else "mixed",
        "execution_backends": {
            stage: metadata["actual_backend"]
            for stage, metadata in stage_metadata.items()
        },
        "fallback_reasons": fallback_reasons,
    }
    if stage_metadata and all(
        "resolution" in metadata for metadata in stage_metadata.values()
    ):
        resolutions = {metadata["resolution"] for metadata in stage_metadata.values()}
        result["execution_backend_resolution"] = (
            next(iter(resolutions)) if len(resolutions) == 1 else "mixed"
        )
        result["execution_backend_resolutions"] = {
            stage: metadata["resolution"] for stage, metadata in stage_metadata.items()
        }
    return result
