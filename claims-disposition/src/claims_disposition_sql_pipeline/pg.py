"""PostgreSQL schema and query primitives."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import PostgresConfig


RAW_DDL = """
create schema if not exists claims_disposition_raw;

create table if not exists claims_disposition_raw.claims (
  claim_id text primary key,
  scenario text not null,
  description text not null,
  submitted_at timestamptz not null,
  is_test_claim boolean not null,
  materials_json jsonb not null,
  constraint claims_materials_is_array
    check (jsonb_typeof(materials_json) = 'array')
);
""".strip()


OUTPUT_DDL = """
create schema if not exists claims_disposition_output;

create table if not exists claims_disposition_output.claim_disposition (
  claim_id text primary key,
  disposition text not null,
  disposition_confidence numeric(4, 2) not null,
  primary_reason_code text not null,
  reason_summary text not null,
  next_action text not null,
  supporting_facts_json jsonb not null,
  created_by text not null,
  decided_at timestamptz not null,
  constraint claims_disposition_value_is_valid check (
    disposition in (
      'approve_for_payment',
      'deny_claim',
      'request_more_materials',
      'manual_review'
    )
  ),
  constraint claims_disposition_confidence_is_valid check (
    disposition_confidence between 0 and 1
  )
);
""".strip()


def connect_postgres(config: PostgresConfig):
    return psycopg.connect(
        config.dsn,
        connect_timeout=5,
        row_factory=dict_row,
    )


def initialize_schemas(connection) -> None:
    connection.execute(RAW_DDL)
    connection.execute(OUTPUT_DDL)


def read_claim_rows(connection, config: PostgresConfig) -> list[dict[str, Any]]:
    query = sql.SQL("select * from {}.{} order by submitted_at, claim_id").format(
        sql.Identifier(config.raw_schema),
        sql.Identifier(config.raw_table),
    )
    return list(connection.execute(query).fetchall())


def reset_fixture_rows(connection, rows: list[dict[str, Any]]) -> None:
    """Replace the raw demo snapshot with the supplied single-table rows."""

    connection.execute("delete from claims_disposition_output.claim_disposition")
    connection.execute("delete from claims_disposition_raw.claims")
    values = []
    for row in rows:
        materials = row["materials_json"]
        if isinstance(materials, str):
            import json

            materials = json.loads(materials)
        values.append(
            (
                row["claim_id"],
                row["scenario"],
                row["description"],
                row["submitted_at"],
                row["is_test_claim"],
                Jsonb(materials),
            )
        )
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            insert into claims_disposition_raw.claims (
              claim_id,
              scenario,
              description,
              submitted_at,
              is_test_claim,
              materials_json
            ) values (%s, %s, %s, %s, %s, %s)
            """,
            values,
        )


def probe_postgres(config: PostgresConfig) -> None:
    with connect_postgres(config) as connection:
        connection.execute("select 1").fetchone()
