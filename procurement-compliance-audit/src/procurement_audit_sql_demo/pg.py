"""PostgreSQL schema, fixture writes, and ordered source reads."""

from __future__ import annotations

import json
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import PostgresConfig


def connect_postgres(config: PostgresConfig):
    return psycopg.connect(
        config.dsn,
        connect_timeout=5,
        row_factory=dict_row,
    )


def _relation(schema: str, table: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))


def initialize_schema(connection: Any, config: PostgresConfig) -> None:
    schema = sql.Identifier(config.raw_schema)
    project = _relation(config.raw_schema, config.project_table)
    supplier = _relation(config.raw_schema, config.supplier_table)
    score = _relation(config.raw_schema, config.score_table)
    evidence = _relation(config.raw_schema, config.evidence_table)

    connection.execute(sql.SQL("create schema if not exists {}").format(schema))
    connection.execute(
        sql.SQL(
            """
            create table if not exists {} (
              project_id text primary key,
              title text not null,
              original_winner_supplier_id text not null,
              score_bias_threshold double precision not null
                check (score_bias_threshold between 0 and 100),
              ai_min_confidence double precision not null
                check (ai_min_confidence between 0 and 1)
            )
            """
        ).format(project)
    )
    connection.execute(
        sql.SQL(
            """
            create table if not exists {} (
              project_id text not null references {} (project_id) on delete cascade,
              supplier_id text not null,
              supplier_name text not null,
              aliases_json jsonb not null check (jsonb_typeof(aliases_json) = 'array'),
              primary key (project_id, supplier_id)
            )
            """
        ).format(supplier, project)
    )
    connection.execute(
        sql.SQL(
            """
            create table if not exists {} (
              project_id text not null,
              expert_id text not null,
              expert_name text not null,
              supplier_id text not null,
              score double precision not null check (score between 0 and 100),
              primary key (project_id, expert_id, supplier_id),
              foreign key (project_id, supplier_id)
                references {} (project_id, supplier_id) on delete cascade
            )
            """
        ).format(score, supplier)
    )
    connection.execute(
        sql.SQL(
            """
            create table if not exists {} (
              project_id text not null references {} (project_id) on delete cascade,
              file_id text not null,
              role text not null,
              bucket text not null,
              object_key text not null,
              media_type text not null,
              primary key (project_id, file_id),
              unique (project_id, role),
              unique (bucket, object_key)
            )
            """
        ).format(evidence, project)
    )


def _json_list(value: Any) -> list[Any]:
    return json.loads(value) if isinstance(value, str) else list(value)


def reset_fixture_rows(
    connection: Any,
    config: PostgresConfig,
    *,
    projects: list[dict[str, Any]],
    suppliers: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
) -> None:
    """Replace the complete focused-demo raw snapshot in one transaction."""

    relations = (
        config.evidence_table,
        config.score_table,
        config.supplier_table,
        config.project_table,
    )
    for table in relations:
        connection.execute(
            sql.SQL("delete from {}").format(_relation(config.raw_schema, table))
        )

    with connection.cursor() as cursor:
        cursor.executemany(
            sql.SQL("insert into {} values (%s, %s, %s, %s, %s)").format(
                _relation(config.raw_schema, config.project_table)
            ),
            [
                (
                    row["project_id"],
                    row["title"],
                    row["original_winner_supplier_id"],
                    row["score_bias_threshold"],
                    row["ai_min_confidence"],
                )
                for row in projects
            ],
        )
        cursor.executemany(
            sql.SQL("insert into {} values (%s, %s, %s, %s)").format(
                _relation(config.raw_schema, config.supplier_table)
            ),
            [
                (
                    row["project_id"],
                    row["supplier_id"],
                    row["supplier_name"],
                    Jsonb(_json_list(row["aliases_json"])),
                )
                for row in suppliers
            ],
        )
        cursor.executemany(
            sql.SQL("insert into {} values (%s, %s, %s, %s, %s)").format(
                _relation(config.raw_schema, config.score_table)
            ),
            [
                (
                    row["project_id"],
                    row["expert_id"],
                    row["expert_name"],
                    row["supplier_id"],
                    row["score"],
                )
                for row in scores
            ],
        )
        cursor.executemany(
            sql.SQL("insert into {} values (%s, %s, %s, %s, %s, %s)").format(
                _relation(config.raw_schema, config.evidence_table)
            ),
            [
                (
                    row["project_id"],
                    row["file_id"],
                    row["role"],
                    row["bucket"],
                    row["object_key"],
                    row["media_type"],
                )
                for row in evidence
            ],
        )


def _read_rows(
    connection: Any,
    config: PostgresConfig,
    table: str,
    order_by: tuple[str, ...],
) -> list[dict[str, Any]]:
    query = sql.SQL("select * from {} order by {}").format(
        _relation(config.raw_schema, table),
        sql.SQL(", ").join(sql.Identifier(column) for column in order_by),
    )
    return list(connection.execute(query).fetchall())


def read_source_rows(
    connection: Any,
    config: PostgresConfig,
) -> tuple[list[dict[str, Any]], ...]:
    return (
        _read_rows(connection, config, config.project_table, ("project_id",)),
        _read_rows(
            connection,
            config,
            config.supplier_table,
            ("project_id", "supplier_id"),
        ),
        _read_rows(
            connection,
            config,
            config.score_table,
            ("project_id", "expert_id", "supplier_id"),
        ),
        _read_rows(
            connection,
            config,
            config.evidence_table,
            ("project_id", "file_id"),
        ),
    )


def probe_postgres(config: PostgresConfig) -> None:
    with connect_postgres(config) as connection:
        connection.execute("select 1").fetchone()
