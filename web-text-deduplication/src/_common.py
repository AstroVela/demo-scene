from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq


TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")
PUBLIC_BACKEND_CHOICES = ("auto", "subprocess_task", "ray_task")


def batch_udf_options(execution_backend: str) -> dict[str, str]:
    if execution_backend == "auto":
        return {}
    return {"execution_backend": execution_backend}


def require_ray_runner(runner: str) -> None:
    if runner != "ray":
        raise RuntimeError(
            "this demo requires Vane RayRunner; unset VANE_RUNNER or set it to ray"
        )


def backend_metadata_entry(execution_backend: str) -> dict[str, Any]:
    inferred = execution_backend == "auto"
    return {
        "requested_backend": execution_backend,
        "actual_backend": "ray_task" if inferred else execution_backend,
        "resolution": "vane_inferred" if inferred else "explicit",
        "fallback_reason": "",
    }


def read_csv_as_strings(path: Path) -> pa.Table:
    with path.open(newline="", encoding="utf-8") as input_file:
        columns = next(csv.reader(input_file))
    return pacsv.read_csv(
        path,
        convert_options=pacsv.ConvertOptions(
            column_types={column: pa.string() for column in columns},
            strings_can_be_null=False,
        ),
    )


def _replace_path(staged_path: Path, target_path: Path) -> None:
    previous_path = staged_path.parent / "previous"
    if target_path.exists():
        target_path.replace(previous_path)
    try:
        staged_path.replace(target_path)
    except BaseException:
        if previous_path.exists():
            previous_path.replace(target_path)
        raise


class RunnerWorkspace:
    """Stage driver inputs and RayRunner results as Parquet scans."""

    def __init__(self, root: Path, connection: Any) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.connection = connection
        self.counter = 0

    def next_path(self, name: str) -> Path:
        self.counter += 1
        return self.root / f"{self.counter:03d}-{name}.parquet"

    def stage_table(self, name: str, table: pa.Table) -> Any:
        path = self.next_path(name)
        pq.write_table(table, path)
        return self.connection.read_parquet(str(path))

    def write_relation(self, name: str, relation: Any) -> Path:
        path = self.next_path(name)
        relation.write_parquet(str(path))
        if not path.exists():
            pq.write_table(relation.limit(0).to_arrow_table(), path)
        return path

    def write_parquet(self, relation: Any, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix=f".{path.name}.tmp-", dir=path.parent
        ) as temp_root:
            staged_path = Path(temp_root) / "new"
            relation.write_parquet(str(staged_path))
            _replace_path(staged_path, path)

    def write_parquet_table(self, table: pa.Table, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix=f".{path.name}.tmp-", dir=path.parent
        ) as temp_root:
            staged_path = Path(temp_root) / "new"
            pq.write_table(table, staged_path)
            _replace_path(staged_path, path)

    def materialize(self, name: str, relation: Any) -> Any:
        return self.stage_table(name, relation.project("*").to_arrow_table())

    def materialize_view(self, name: str, relation: Any) -> Any:
        materialized = self.materialize(name, relation)
        materialized.create_view(name, replace=True)
        return materialized

    def write_csv(self, relation: Any, path: Path) -> None:
        table = relation.project("*").to_arrow_table()
        arrays = []
        for field, column in zip(table.schema, table.columns):
            if pa.types.is_nested(field.type):
                arrays.append(
                    pa.array(
                        [
                            json.dumps(value, sort_keys=True, default=str)
                            if value is not None
                            else None
                            for value in column.to_pylist()
                        ],
                        type=pa.string(),
                    )
                )
            else:
                arrays.append(column)
        path.parent.mkdir(parents=True, exist_ok=True)
        pacsv.write_csv(pa.Table.from_arrays(arrays, names=table.column_names), path)


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
