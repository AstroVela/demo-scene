"""Parquet boundaries shared by LocalRunner and RayRunner stages."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


class RunnerWorkspace:
    """Expose driver tables and materialized results as Parquet relations."""

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

    def materialize_table(
        self,
        name: str,
        relation: Any,
        *,
        empty_table: pa.Table,
    ) -> pa.Table:
        path = self.next_path(name)
        relation.write_parquet(str(path))
        if not path.exists():
            pq.write_table(empty_table, path)
        return pq.read_table(path)

    def create_view(self, name: str, table: pa.Table) -> Any:
        relation = self.stage_table(name, table)
        relation.create_view(name, replace=True)
        return relation
