"""Load one deterministic synthetic company into PostgreSQL and MinIO."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
import sys
from typing import Any

from psycopg import sql

from .assets import SyntheticAsset, build_assets
from .config import DEFAULT_CONFIG_PATH, RuntimeConfig, load_runtime_config
from .minio_store import MinioStore
from .pg import connect, insert_rows


SCENARIOS = (
    "default",
    "glossary-before",
    "glossary-after",
    "recovery-fault",
    "recovery-fixed",
)


def logical_scenario(variant: str) -> str:
    if variant.startswith("recovery-"):
        return "recovery"
    return variant


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load the synthetic fund-research fixture")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--scenario", choices=SCENARIOS, default="default")
    return parser


def _create_raw_schema(connection: Any, config: RuntimeConfig) -> None:
    raw = sql.Identifier(config.postgres.raw_schema)
    connection.execute(sql.SQL("drop schema if exists {} cascade").format(raw))
    connection.execute(sql.SQL("create schema {}").format(raw))
    statements = [
        """
        create table {}.fixture_metadata (
            singleton_id smallint primary key check (singleton_id = 1),
            logical_scenario text not null,
            fixture_variant text not null,
            fixture_version text not null
        )
        """,
        """
        create table {}.companies (
            company_id text primary key,
            canonical_name text not null,
            industry text not null,
            aliases text[] not null
        )
        """,
        """
        create table {}.investment_theses (
            thesis_id text primary key,
            company_id text not null references {}.companies(company_id),
            thesis_version integer not null,
            thesis_title text not null,
            approved_by text not null,
            approved_at timestamptz not null,
            status text not null check (status = 'approved')
        )
        """,
        """
        create table {}.thesis_conditions (
            condition_id text primary key,
            thesis_id text not null references {}.investment_theses(thesis_id),
            metric_code text not null,
            operator text not null check (operator in ('gte', 'lte', 'qualitative')),
            threshold_numeric double precision,
            unit text,
            condition_text text not null,
            mandatory boolean not null
        )
        """,
        """
        create table {}.domain_terms (
            term_id text not null,
            canonical_term text not null,
            alias text not null,
            category text not null,
            scope_company_id text not null references {}.companies(company_id),
            primary key (term_id, alias)
        )
        """,
        """
        create table {}.source_files (
            source_id text primary key,
            company_id text not null references {}.companies(company_id),
            title text not null,
            source_role text not null,
            trust_tier integer not null check (trust_tier between 1 and 3),
            observed_at timestamptz not null,
            media_type text not null,
            bucket text not null,
            object_key text not null,
            sha256 text not null check (length(sha256) = 64),
            unique (bucket, object_key)
        )
        """,
        """
        create table {}.incoming_signals (
            signal_id text primary key,
            company_id text not null references {}.companies(company_id),
            thesis_id text not null references {}.investment_theses(thesis_id),
            signal_type text not null,
            summary text not null
        )
        """,
        """
        create table {}.signal_sources (
            signal_id text not null references {}.incoming_signals(signal_id),
            source_id text not null references {}.source_files(source_id),
            primary key (signal_id, source_id)
        )
        """,
    ]
    for template in statements:
        placeholders = template.count("{}")
        connection.execute(
            sql.SQL(template).format(*([raw] * placeholders))
        )


def _create_work_schema(connection: Any, config: RuntimeConfig) -> None:
    work = sql.Identifier(config.postgres.work_schema)
    connection.execute(sql.SQL("create schema if not exists {}").format(work))
    connection.execute(
        sql.SQL(
            """
            create table if not exists {}.source_processing_status (
                logical_scenario text not null,
                run_id text not null,
                source_id text not null,
                source_sha256 text not null,
                stage text not null,
                stage_version text not null,
                status text not null check (
                    status in ('pending', 'succeeded', 'quarantined', 'failed')
                ),
                error_code text,
                attempt integer not null,
                result_locator text,
                result_json jsonb,
                started_at timestamptz not null,
                completed_at timestamptz,
                primary key (
                    logical_scenario, source_id, source_sha256, stage, stage_version
                )
            )
            """
        ).format(work)
    )


def _fixture_rows(
    config: RuntimeConfig,
    scenario: str,
    assets: list[SyntheticAsset],
) -> dict[str, list[dict[str, Any]]]:
    include_nectin_alias = scenario != "glossary-before"
    terms = [
        {
            "term_id": "TERM-COMPANY-001",
            "canonical_term": "Lanxing Biotech",
            "alias": "Lengthing Biotech",
            "category": "company",
            "scope_company_id": "SYN-BIO-001",
        },
        {
            "term_id": "TERM-COMPANY-001",
            "canonical_term": "Lanxing Biotech",
            "alias": "Length in biotech",
            "category": "company",
            "scope_company_id": "SYN-BIO-001",
        },
        {
            "term_id": "TERM-PRODUCT-001",
            "canonical_term": "LX-101",
            "alias": "LX101",
            "category": "product",
            "scope_company_id": "SYN-BIO-001",
        },
    ]
    if include_nectin_alias:
        terms.append(
            {
                "term_id": "TERM-TARGET-001",
                "canonical_term": "Nectin-4",
                "alias": "actin-4",
                "category": "target",
                "scope_company_id": "SYN-BIO-001",
            }
        )
    return {
        "fixture_metadata": [
            {
                "singleton_id": 1,
                "logical_scenario": logical_scenario(scenario),
                "fixture_variant": scenario,
                "fixture_version": "synthetic-biotech-v1",
            }
        ],
        "companies": [
            {
                "company_id": "SYN-BIO-001",
                "canonical_name": "澜星生物 / Lanxing Biotech",
                "industry": "创新药 / biotechnology",
                "aliases": ["澜星生物", "Lanxing Biotech", "Lanxing"],
            }
        ],
        "investment_theses": [
            {
                "thesis_id": "THESIS-LANXING-001",
                "company_id": "SYN-BIO-001",
                "thesis_version": 3,
                "thesis_title": "LX-101 efficacy, safety, liquidity, and filing thesis",
                "approved_by": "SYNTHETIC-ANALYST",
                "approved_at": "2026-04-15T10:00:00+08:00",
                "status": "approved",
            }
        ],
        "thesis_conditions": [
            {
                "condition_id": "COND-EFFICACY",
                "thesis_id": "THESIS-LANXING-001",
                "metric_code": "ORR",
                "operator": "gte",
                "threshold_numeric": 40.0,
                "unit": "percent",
                "condition_text": "Overall ORR must be at least 40%.",
                "mandatory": True,
            },
            {
                "condition_id": "COND-SAFETY",
                "thesis_id": "THESIS-LANXING-001",
                "metric_code": "TRAE_G3_PLUS",
                "operator": "lte",
                "threshold_numeric": 35.0,
                "unit": "percent",
                "condition_text": "Grade 3+ TRAE must be at most 35%.",
                "mandatory": True,
            },
            {
                "condition_id": "COND-RUNWAY",
                "thesis_id": "THESIS-LANXING-001",
                "metric_code": "CASH_RUNWAY",
                "operator": "gte",
                "threshold_numeric": 18.0,
                "unit": "months",
                "condition_text": "Cash runway must be at least 18 months.",
                "mandatory": True,
            },
            {
                "condition_id": "COND-REGULATORY",
                "thesis_id": "THESIS-LANXING-001",
                "metric_code": "BLA_STATUS",
                "operator": "qualitative",
                "threshold_numeric": None,
                "unit": "status",
                "condition_text": "The BLA filing should progress as planned.",
                "mandatory": True,
            },
        ],
        "domain_terms": terms,
        "source_files": [
            {
                "source_id": asset.source_id,
                "company_id": "SYN-BIO-001",
                "title": asset.title,
                "source_role": asset.source_role,
                "trust_tier": asset.trust_tier,
                "observed_at": asset.observed_at,
                "media_type": asset.media_type,
                "bucket": config.minio.bucket,
                "object_key": asset.object_key,
                "sha256": asset.sha256,
            }
            for asset in assets
        ],
        "incoming_signals": [
            {
                "signal_id": "SIG-CLINICAL",
                "company_id": "SYN-BIO-001",
                "thesis_id": "THESIS-LANXING-001",
                "signal_type": "clinical_results",
                "summary": "Phase II efficacy and safety topline results",
            },
            {
                "signal_id": "SIG-RUNWAY",
                "company_id": "SYN-BIO-001",
                "thesis_id": "THESIS-LANXING-001",
                "signal_type": "financial_update",
                "summary": "Updated cash runway estimate",
            },
            {
                "signal_id": "SIG-REGULATORY",
                "company_id": "SYN-BIO-001",
                "thesis_id": "THESIS-LANXING-001",
                "signal_type": "regulatory_timing",
                "summary": "Conflicting BLA timing statements",
            },
            {
                "signal_id": "SIG-RUMOR",
                "company_id": "SYN-BIO-001",
                "thesis_id": "THESIS-LANXING-001",
                "signal_type": "unverified_rumor",
                "summary": "Unverified claim that the trial was halted",
            },
        ],
        "signal_sources": [
            {"signal_id": "SIG-CLINICAL", "source_id": "SRC-CLINICAL"},
            {"signal_id": "SIG-RUNWAY", "source_id": "SRC-FINANCIAL"},
            {"signal_id": "SIG-REGULATORY", "source_id": "SRC-REG-OFFICIAL"},
            {"signal_id": "SIG-REGULATORY", "source_id": "SRC-REG-EXPERT"},
            {"signal_id": "SIG-RUMOR", "source_id": "SRC-RUMOR"},
        ],
    }


def load_fixture(config: RuntimeConfig, scenario: str) -> dict[str, int]:
    assets = build_assets()
    store = MinioStore(config.minio)
    store.ensure_bucket()
    if scenario == "recovery-fixed":
        # Preserve every unchanged object's exact bytes (especially generated
        # speech) so the recovery anti-join has one genuinely changed/failed
        # source instead of treating a new TTS render as a content change.
        assets = [
            asset
            if asset.source_id == "SRC-CLINICAL"
            else replace(
                asset,
                content=store.get_bytes(config.minio.bucket, asset.object_key),
            )
            for asset in assets
        ]
    rows = _fixture_rows(config, scenario, assets)
    for asset in assets:
        content = (
            b"synthetic-corrupt-pdf-for-recovery-demo"
            if scenario == "recovery-fault" and asset.source_id == "SRC-CLINICAL"
            else asset.content
        )
        store.put_bytes(asset.object_key, content, content_type=asset.media_type)

    with connect(config.postgres) as connection:
        _create_raw_schema(connection, config)
        _create_work_schema(connection, config)
        if scenario != "recovery-fixed":
            connection.execute(
                sql.SQL(
                    "delete from {}.source_processing_status where logical_scenario = %s"
                ).format(sql.Identifier(config.postgres.work_schema)),
                (logical_scenario(scenario),),
            )
        for table in (
            "fixture_metadata",
            "companies",
            "investment_theses",
            "thesis_conditions",
            "domain_terms",
            "source_files",
            "incoming_signals",
            "signal_sources",
        ):
            insert_rows(
                connection,
                config.postgres.raw_schema,
                table,
                rows[table],
            )
        connection.commit()
    return {table: len(values) for table, values in rows.items()}


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        config = load_runtime_config(args.config)
        counts = load_fixture(config, args.scenario)
    except Exception as exc:
        print(f"fixture failed: {exc}", file=sys.stderr)
        return 1
    print(
        "loaded synthetic fixture: "
        f"{counts['companies']} company, {counts['thesis_conditions']} thesis conditions, "
        f"{counts['source_files']} source files, and {counts['incoming_signals']} signals "
        f"(scenario={args.scenario})"
    )
    return 0
