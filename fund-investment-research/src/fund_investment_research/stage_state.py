"""Persistent stage identity, result cache, and resume selection."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import PostgresConfig
from .pg import checked_identifier


class StageStateStore:
    def __init__(self, config: PostgresConfig):
        self.config = config
        self.connection = psycopg.connect(config.dsn, row_factory=dict_row)
        self.table = sql.SQL("{}.source_processing_status").format(
            sql.Identifier(checked_identifier(config.work_schema))
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "StageStateStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def successful_result(
        self,
        *,
        logical_scenario: str,
        source_id: str,
        source_sha256: str,
        stage: str,
        stage_version: str,
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            sql.SQL(
                """
                select result_json
                from {}
                where logical_scenario = %s
                  and source_id = %s
                  and source_sha256 = %s
                  and stage = %s
                  and stage_version = %s
                  and status = 'succeeded'
                """
            ).format(self.table),
            (logical_scenario, source_id, source_sha256, stage, stage_version),
        ).fetchone()
        if row is None:
            return None
        value = row["result_json"]
        return dict(value) if value is not None else {}

    def begin(
        self,
        *,
        run_id: str,
        logical_scenario: str,
        source_id: str,
        source_sha256: str,
        stage: str,
        stage_version: str,
    ) -> int:
        now = datetime.now(timezone.utc)
        row = self.connection.execute(
            sql.SQL(
                """
                insert into {} (
                    logical_scenario, run_id, source_id, source_sha256,
                    stage, stage_version, status, error_code, attempt,
                    result_locator, result_json, started_at, completed_at
                )
                values (%s, %s, %s, %s, %s, %s, 'pending', null, 1, null, null, %s, null)
                on conflict (
                    logical_scenario, source_id, source_sha256, stage, stage_version
                )
                do update set
                    run_id = excluded.run_id,
                    status = 'pending',
                    error_code = null,
                    attempt = {}.attempt + 1,
                    result_locator = null,
                    result_json = null,
                    started_at = excluded.started_at,
                    completed_at = null
                returning attempt
                """
            ).format(self.table, self.table),
            (
                logical_scenario,
                run_id,
                source_id,
                source_sha256,
                stage,
                stage_version,
                now,
            ),
        ).fetchone()
        self.connection.commit()
        return int(row["attempt"])

    def finish(
        self,
        *,
        logical_scenario: str,
        source_id: str,
        source_sha256: str,
        stage: str,
        stage_version: str,
        status: str,
        error_code: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        if status not in {"succeeded", "quarantined", "failed"}:
            raise ValueError(f"invalid terminal stage status: {status}")
        locator = (
            f"postgres-stage://{self.config.work_schema}/"
            f"{logical_scenario}/{source_id}/{source_sha256}/{stage}/{stage_version}"
        )
        self.connection.execute(
            sql.SQL(
                """
                update {}
                set status = %s,
                    error_code = %s,
                    result_locator = %s,
                    result_json = %s,
                    completed_at = %s
                where logical_scenario = %s
                  and source_id = %s
                  and source_sha256 = %s
                  and stage = %s
                  and stage_version = %s
                """
            ).format(self.table),
            (
                status,
                error_code,
                locator,
                Jsonb(result) if result is not None else None,
                datetime.now(timezone.utc),
                logical_scenario,
                source_id,
                source_sha256,
                stage,
                stage_version,
            ),
        )
        self.connection.commit()

    def rows_for_sources(
        self,
        *,
        logical_scenario: str,
        current_hashes: dict[str, str],
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            sql.SQL(
                """
                select run_id, source_id, source_sha256, stage, stage_version,
                       status, error_code, attempt, result_locator,
                       started_at, completed_at
                from {}
                where logical_scenario = %s
                order by source_id, stage, stage_version
                """
            ).format(self.table),
            (logical_scenario,),
        ).fetchall()
        result = []
        for row in rows:
            if current_hashes.get(row["source_id"]) != row["source_sha256"]:
                continue
            item = dict(row)
            for key in ("started_at", "completed_at"):
                if item[key] is not None:
                    item[key] = item[key].isoformat()
            result.append(item)
        return result
