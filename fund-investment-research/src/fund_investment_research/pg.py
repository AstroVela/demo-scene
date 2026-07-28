"""PostgreSQL schema and snapshot helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from .config import PostgresConfig


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def checked_identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid PostgreSQL identifier: {value!r}")
    return value


@contextmanager
def connect(config: PostgresConfig):
    with psycopg.connect(config.dsn, row_factory=dict_row) as connection:
        yield connection


def qualified(schema: str, table: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(
        sql.Identifier(checked_identifier(schema)),
        sql.Identifier(checked_identifier(table)),
    )


def read_table(
    connection: psycopg.Connection[Any],
    schema: str,
    table: str,
    *,
    order_by: Sequence[str],
) -> list[dict[str, Any]]:
    order = sql.SQL(", ").join(sql.Identifier(checked_identifier(name)) for name in order_by)
    statement = sql.SQL("select * from {} order by {}").format(
        qualified(schema, table),
        order,
    )
    return [dict(row) for row in connection.execute(statement).fetchall()]


def insert_rows(
    connection: psycopg.Connection[Any],
    schema: str,
    table: str,
    rows: Iterable[Mapping[str, Any]],
) -> None:
    values = list(rows)
    if not values:
        return
    columns = list(values[0])
    if any(list(row) != columns for row in values):
        raise ValueError(f"{table} rows must use one stable column order")
    statement = sql.SQL("insert into {} ({}) values ({})").format(
        qualified(schema, table),
        sql.SQL(", ").join(sql.Identifier(checked_identifier(name)) for name in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            statement,
            [tuple(row[column] for column in columns) for row in values],
        )
