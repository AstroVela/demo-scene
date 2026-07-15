#!/usr/bin/env python3
"""Build auditable Agent context from multiple enterprise evidence relations."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa
import vane

SOURCE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SOURCE_DIR.parent
for import_path in (REPO_ROOT, SOURCE_DIR):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))
existing_pythonpath = os.environ.get("PYTHONPATH")
pythonpath_entries = [str(REPO_ROOT), str(SOURCE_DIR)]
if existing_pythonpath:
    paths = existing_pythonpath.split(os.pathsep)
    missing_paths = [path for path in pythonpath_entries if path not in paths]
    if missing_paths:
        os.environ["PYTHONPATH"] = os.pathsep.join(missing_paths + paths)
else:
    os.environ["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

from _common import (
    PUBLIC_BACKEND_CHOICES,
    backend_metadata_entry,
    batch_udf_options,
    merge_backend_metadata,
    positive_int,
    require_local_relation_runner,
    table_from_rows,
    write_json,
)
from _media import (
    MEDIA_METRICS_TYPE,
    SUPPORTED_MODALITIES,
    process_audio,
    process_document,
    process_image,
    process_text,
    project_raw_assets,
    validate_modalities,
    verify_public_asset_snapshot,
)


DEFAULT_INPUT_DIR = REPO_ROOT / "data" / "enterprise_multimodal_agent"
DEFAULT_SCENARIO_SNAPSHOT = DEFAULT_INPUT_DIR / "scenario_snapshot.json"
DEFAULT_ASSET_CATALOG = DEFAULT_INPUT_DIR / "asset_catalog.csv"
ASSET_SNAPSHOT_METADATA = DEFAULT_INPUT_DIR / "asset_snapshot.json"
DEFAULT_OUTPUT_DIR = Path("output/enterprise_multimodal_agent")
MODULE_NAME = "src.enterprise_multimodal_agent"
FRESHNESS_DAYS = 30
SCENARIO_SCHEMA_VERSION = 1
OUTPUT_SCHEMA_VERSION = 1
ASSET_FEATURE_SCHEMA = {
    "asset_id": "VARCHAR",
    "modality": "VARCHAR",
    "source_uri": "VARCHAR",
    "source_page_uri": "VARCHAR",
    "source_version": "VARCHAR",
    "license_id": "VARCHAR",
    "license_uri": "VARCHAR",
    "mime_type": "VARCHAR",
    "evidence_text": "VARCHAR",
    "content_sha256": "VARCHAR",
    "byte_size": "BIGINT",
    "token_count": "BIGINT",
    "asset_decision": "VARCHAR",
    "risk_flags": "VARCHAR[]",
    "risk_count": "BIGINT",
    "blocking_risk_count": "BIGINT",
    "media_metrics": (
        "STRUCT(width BIGINT, height BIGINT, duration_seconds DOUBLE, "
        "sample_rate BIGINT)"
    ),
}
ASSET_FEATURE_ARROW_SCHEMA = {
    "asset_id": pa.string(),
    "modality": pa.string(),
    "source_uri": pa.string(),
    "source_page_uri": pa.string(),
    "source_version": pa.string(),
    "license_id": pa.string(),
    "license_uri": pa.string(),
    "mime_type": pa.string(),
    "evidence_text": pa.string(),
    "content_sha256": pa.string(),
    "byte_size": pa.int64(),
    "token_count": pa.int64(),
    "asset_decision": pa.string(),
    "risk_flags": pa.list_(pa.string()),
    "risk_count": pa.int64(),
    "blocking_risk_count": pa.int64(),
    "media_metrics": MEDIA_METRICS_TYPE,
}
REVIEW_QUEUE_ORDER = """
case review_state
  when 'blocked' then 0
  when 'needs_review' then 1
  else 2
end,
case_id
"""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def csv_file_metadata(path: Path) -> dict[str, Any]:
    with path.open(newline="", encoding="utf-8") as input_file:
        reader = csv.DictReader(input_file)
        records = sum(1 for _ in reader)
        columns = list(reader.fieldnames or [])
    return {
        "file": path.name,
        "path": display_path(path),
        "sha256": file_sha256(path),
        "records": records,
        "columns": columns,
    }


def verify_scenario_snapshot(
    input_dir: Path,
    snapshot_path: Path,
) -> dict[str, Any]:
    if not snapshot_path.is_file():
        raise FileNotFoundError(f"scenario snapshot does not exist: {snapshot_path}")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if snapshot.get("schema_version") != SCENARIO_SCHEMA_VERSION:
        raise ValueError(
            "unsupported enterprise scenario schema version: "
            f"{snapshot.get('schema_version')}"
        )

    paths = required_input_paths(input_dir)
    expected_files = {
        entry.get("file"): entry for entry in snapshot.get("files", [])
    }
    if set(expected_files) != {path.name for path in paths.values()}:
        raise ValueError("scenario snapshot file list does not match required inputs")

    failures: list[str] = []
    for path in paths.values():
        actual = csv_file_metadata(path)
        expected = expected_files[path.name]
        for field in ("sha256", "records", "columns"):
            if actual[field] != expected.get(field):
                failures.append(
                    f"{path.name} {field} mismatch: "
                    f"expected {expected.get(field)!r}, got {actual[field]!r}"
                )
    if failures:
        raise ValueError("invalid enterprise scenario snapshot: " + "; ".join(failures))
    return snapshot


def required_input_paths(input_dir: Path) -> dict[str, Path]:
    return {
        "cases": input_dir / "cases.csv",
        "requirements": input_dir / "requirements.csv",
        "evidence_links": input_dir / "evidence_links.csv",
    }


def validate_input_paths(input_dir: Path) -> dict[str, Path]:
    paths = required_input_paths(input_dir)
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing enterprise input files: " + ", ".join(missing))
    return paths


def process_asset_batch(
    batch: pa.Table,
    processor: Callable[[dict[str, Any]], dict[str, Any]],
) -> pa.Table:
    rows: list[dict[str, Any]] = []
    for row in batch.to_pylist():
        feature = processor(row)
        metadata = json.loads(row["metadata_json"] or "{}")
        flags = list(feature["risk_flags"])
        rows.append(
            {
                "asset_id": row["record_id"],
                "modality": row["modality"],
                "source_uri": row["source_uri"],
                "source_page_uri": metadata.get("source_page_uri", ""),
                "source_version": metadata.get("source_version", ""),
                "license_id": row["license_id"],
                "license_uri": metadata.get("license_uri", ""),
                "mime_type": row["mime_type"],
                "evidence_text": feature["content_text"],
                "content_sha256": feature["content_sha256"],
                "byte_size": feature["byte_size"],
                "token_count": feature["token_count"],
                "asset_decision": feature["decision"],
                "risk_flags": flags,
                "risk_count": len(flags),
                "blocking_risk_count": len(flags),
                "media_metrics": feature["media_metrics"],
            }
        )

    return table_from_rows(rows, ASSET_FEATURE_ARROW_SCHEMA)


def process_document_asset_batch(batch: pa.Table) -> pa.Table:
    return process_asset_batch(batch, process_document)


def process_image_asset_batch(batch: pa.Table) -> pa.Table:
    return process_asset_batch(batch, process_image)


def process_audio_asset_batch(batch: pa.Table) -> pa.Table:
    return process_asset_batch(batch, process_audio)


def process_text_asset_batch(batch: pa.Table) -> pa.Table:
    return process_asset_batch(batch, process_text)


def importable_batch_function(name: str) -> Any:
    if __name__ != "__main__":
        return globals()[name]

    import importlib

    module = importlib.import_module(MODULE_NAME)
    return getattr(module, name)


def relation_row_count(rel: Any) -> int:
    return int(rel.aggregate("count(*) as row_count").fetchone()[0])


def validation_count(conn: Any, query: str) -> int:
    return int(conn.sql(query).fetchone()[0])


def validate_source_integrity(conn: Any) -> None:
    supported_modalities = ", ".join(
        f"'{modality}'" for modality in SUPPORTED_MODALITIES
    )
    checks = {
        "empty case field": """
            select count(*)
            from business_cases
            where trim(coalesce(case_id, '')) = ''
               or trim(coalesce(account_id, '')) = ''
               or trim(coalesce(business_question, '')) = ''
        """,
        "case with invalid review date": """
            select count(*) from business_cases where review_due_at is null
        """,
        "duplicate case_id": """
            select count(*) from (
              select case_id from business_cases group by case_id having count(*) > 1
            )
        """,
        "empty requirement field": """
            select count(*)
            from case_requirements
            where trim(coalesce(case_id, '')) = ''
               or trim(coalesce(evidence_type, '')) = ''
        """,
        "unsupported requirement modality": f"""
            select count(*)
            from case_requirements
            where evidence_type not in ({supported_modalities})
        """,
        "duplicate requirement": """
            select count(*) from (
              select case_id, evidence_type
              from case_requirements
              group by case_id, evidence_type
              having count(*) > 1
            )
        """,
        "empty evidence field": """
            select count(*)
            from evidence_links
            where trim(coalesce(record_id, '')) = ''
               or trim(coalesce(case_id, '')) = ''
               or trim(coalesce(asset_id, '')) = ''
               or trim(coalesce(source_system, '')) = ''
               or trim(coalesce(evidence_title, '')) = ''
        """,
        "evidence with invalid observation date": """
            select count(*) from evidence_links where observed_at is null
        """,
        "evidence with incomplete claim": """
            select count(*)
            from evidence_links
            where (trim(coalesce(claim_key, '')) = '')
               <> (trim(coalesce(claim_value, '')) = '')
        """,
        "duplicate evidence record_id": """
            select count(*) from (
              select record_id from evidence_links group by record_id having count(*) > 1
            )
        """,
        "empty asset field": """
            select count(*)
            from public_assets
            where trim(coalesce(asset_id, '')) = ''
               or trim(coalesce(modality, '')) = ''
               or trim(coalesce(source_uri, '')) = ''
               or trim(coalesce(license_id, '')) = ''
               or trim(coalesce(mime_type, '')) = ''
               or trim(coalesce(expected_sha256, '')) = ''
        """,
        "invalid asset SHA-256": """
            select count(*)
            from public_assets
            where not regexp_matches(expected_sha256, '^[0-9a-fA-F]{64}$')
        """,
        "duplicate asset_id": """
            select count(*) from (
              select asset_id from public_assets group by asset_id having count(*) > 1
            )
        """,
        "requirement with unknown case": """
            select count(*)
            from case_requirements r
            left join business_cases c using (case_id)
            where c.case_id is null
        """,
        "evidence with unknown case": """
            select count(*)
            from evidence_links e
            left join business_cases c using (case_id)
            where c.case_id is null
        """,
        "evidence with unknown asset": """
            select count(*)
            from evidence_links e
            left join public_assets a using (asset_id)
            where a.asset_id is null
        """,
        "case without requirement": """
            select count(*)
            from business_cases c
            left join case_requirements r using (case_id)
            where r.case_id is null
        """,
        "evidence observed after review date": """
            select count(*)
            from evidence_links e
            join business_cases c using (case_id)
            where e.observed_at > c.review_due_at
        """,
    }
    failures = [
        f"{label} ({count})"
        for label, query in checks.items()
        if (count := validation_count(conn, query)) > 0
    ]
    if relation_row_count(conn.sql("select * from business_cases")) == 0:
        failures.append("business_cases is empty")
    if relation_row_count(conn.sql("select * from public_assets")) == 0:
        failures.append("public_assets is empty")
    if relation_row_count(conn.sql("select * from evidence_links")) == 0:
        failures.append("evidence_links is empty")
    if failures:
        raise ValueError("invalid enterprise evidence inputs: " + ", ".join(failures))


def materialize_sources(
    conn: Any,
    input_dir: Path,
    asset_catalog: Path,
) -> tuple[Any, Any, Any, Any, list[str]]:
    paths = validate_input_paths(input_dir)
    if not asset_catalog.is_file():
        raise FileNotFoundError(f"public asset catalog does not exist: {asset_catalog}")

    cases = conn.read_csv(str(paths["cases"]), header=True).project(
        "case_id, account_id, business_question, "
        "try_cast(review_due_at as date) as review_due_at"
    )
    requirements = conn.read_csv(str(paths["requirements"]), header=True).project(
        "case_id, evidence_type"
    )
    links = conn.read_csv(str(paths["evidence_links"]), header=True).project(
        """
        record_id,
        case_id,
        asset_id,
        source_system,
        try_cast(observed_at as date) as observed_at,
        evidence_title,
        claim_key,
        claim_value
        """
    )
    assets = project_raw_assets(
        conn.read_csv(str(asset_catalog), header=True)
    ).project(
        """
        record_id,
        record_id as asset_id,
        modality,
        source_uri,
        license_id,
        split,
        mime_type,
        text,
        content_path,
        expected_sha256,
        content_base64,
        metadata_json
        """
    )

    conn.sql("drop table if exists business_cases")
    cases.order("case_id").to_table("business_cases")
    conn.sql("drop table if exists case_requirements")
    requirements.order("case_id, evidence_type").to_table("case_requirements")
    conn.sql("drop table if exists evidence_links")
    links.order("case_id, record_id").to_table("evidence_links")
    conn.sql("drop table if exists public_assets")
    assets.order("asset_id").to_table("public_assets")

    validate_source_integrity(conn)
    public_assets = conn.sql(
        """
        select a.*
        from public_assets a
        join (select distinct asset_id from evidence_links) e using (asset_id)
        order by a.asset_id
        """
    )
    modalities = validate_modalities(public_assets)
    return (
        conn.sql("select * from business_cases"),
        conn.sql("select * from case_requirements"),
        public_assets,
        conn.sql("select * from evidence_links"),
        modalities,
    )


def build_asset_feature_relations(
    public_assets: Any,
    modalities: list[str],
    args: argparse.Namespace,
) -> tuple[Any, dict[str, dict[str, Any]]]:
    stage_functions = {
        "document": "process_document_asset_batch",
        "image": "process_image_asset_batch",
        "audio": "process_audio_asset_batch",
        "text": "process_text_asset_batch",
    }
    udf_options = batch_udf_options(args.execution_backend)
    relations: list[Any] = []
    backend_metadata: dict[str, dict[str, Any]] = {}
    for modality in SUPPORTED_MODALITIES:
        if modality not in modalities:
            continue
        source = public_assets.filter(f"modality = '{modality}'").order("record_id")
        relations.append(
            source.map_batches(
                importable_batch_function(stage_functions[modality]),
                schema=ASSET_FEATURE_SCHEMA,
                batch_size=args.batch_size,
                **udf_options,
            )
        )
        backend_metadata[f"process_{modality}_asset"] = backend_metadata_entry(
            args.execution_backend
        )

    features = relations[0]
    for relation in relations[1:]:
        features = features.union(relation)
    return features, backend_metadata


def build_evidence_features(conn: Any) -> Any:
    return conn.sql(
        """
        select
          l.record_id,
          l.case_id,
          a.asset_id,
          a.modality as evidence_type,
          a.modality,
          l.source_system,
          a.source_uri,
          a.source_page_uri,
          a.source_version,
          a.license_id,
          a.license_uri,
          l.observed_at,
          l.evidence_title,
          a.evidence_text,
          l.claim_key,
          l.claim_value,
          a.content_sha256,
          a.byte_size,
          a.token_count,
          a.asset_decision,
          case
            when lower(l.claim_value) = 'blocked'
              then list_append(a.risk_flags, 'asserted_blocker')
            else a.risk_flags
          end as risk_flags,
          a.risk_count
            + case when lower(l.claim_value) = 'blocked' then 1 else 0 end
            as risk_count,
          a.blocking_risk_count
            + case when lower(l.claim_value) = 'blocked' then 1 else 0 end
            as blocking_risk_count,
          a.media_metrics
        from evidence_links l
        join asset_features a using (asset_id)
        order by l.case_id, l.record_id
        """
    )


def build_case_relations(conn: Any) -> tuple[Any, Any, Any, Any]:
    evidence_gaps_rel = conn.sql(
        """
        select
          r.case_id,
          c.account_id,
          r.evidence_type as missing_evidence_type,
          'missing_required_evidence' as reason
        from case_requirements r
        join business_cases c using (case_id)
        left join evidence_features e
          on e.case_id = r.case_id
         and e.evidence_type = r.evidence_type
        where e.record_id is null
        order by r.case_id, r.evidence_type
        """
    )
    conn.sql("drop table if exists evidence_gaps")
    evidence_gaps_rel.to_table("evidence_gaps")

    evidence_conflicts_rel = conn.sql(
        """
        select
          case_id,
          claim_key,
          count(distinct claim_value) as distinct_values,
          string_agg(distinct claim_value, ', ' order by claim_value) as claim_values,
          string_agg(record_id, ', ' order by record_id) as evidence_ids
        from evidence_features
        where claim_key <> '' and claim_value <> ''
        group by case_id, claim_key
        having count(distinct claim_value) > 1
        order by case_id, claim_key
        """
    )
    conn.sql("drop table if exists evidence_conflicts")
    evidence_conflicts_rel.to_table("evidence_conflicts")

    agent_context_rel = conn.sql(
        f"""
        with evidence_rollup as (
          select
            e.case_id,
            count(*) as evidence_count,
            count(distinct e.source_system) as source_count,
            count(distinct e.modality) as modality_count,
            sum(case when e.asset_decision = 'rejected' then 1 else 0 end)
              as rejected_asset_count,
            sum(e.risk_count) as risk_count,
            sum(e.blocking_risk_count) as blocking_risk_count,
            sum(
              case
                when date_diff('day', e.observed_at, c.review_due_at) > {FRESHNESS_DAYS}
                  then 1
                else 0
              end
            ) as stale_evidence_count,
            list(e.record_id order by e.observed_at desc, e.record_id) as evidence_ids,
            list(e.asset_id order by e.observed_at desc, e.record_id) as asset_ids,
            list(distinct e.source_system order by e.source_system) as source_systems,
            list(distinct e.modality order by e.modality) as modalities,
            list(distinct e.license_id order by e.license_id) as license_ids,
            string_agg(
              '[' || e.modality || '/' || e.source_system || '] '
                || e.evidence_title || ': ' || e.evidence_text,
              '\n' order by e.observed_at desc, e.record_id
            ) as context_text
          from evidence_features e
          join business_cases c using (case_id)
          group by e.case_id
        ),
        gap_counts as (
          select case_id, count(*) as missing_evidence_count
          from evidence_gaps
          group by case_id
        ),
        conflict_counts as (
          select case_id, count(*) as conflict_count
          from evidence_conflicts
          group by case_id
        )
        select
          'ctx-' || c.case_id as context_id,
          c.case_id,
          c.account_id,
          c.business_question,
          c.review_due_at,
          coalesce(e.evidence_count, 0) as evidence_count,
          coalesce(e.source_count, 0) as source_count,
          coalesce(e.modality_count, 0) as modality_count,
          coalesce(g.missing_evidence_count, 0) as missing_evidence_count,
          coalesce(k.conflict_count, 0) as conflict_count,
          coalesce(e.stale_evidence_count, 0) as stale_evidence_count,
          coalesce(e.rejected_asset_count, 0) as rejected_asset_count,
          coalesce(e.risk_count, 0) as risk_count,
          coalesce(e.evidence_ids, []) as evidence_ids,
          coalesce(e.asset_ids, []) as asset_ids,
          coalesce(e.source_systems, []) as source_systems,
          coalesce(e.modalities, []) as modalities,
          coalesce(e.license_ids, []) as license_ids,
          coalesce(e.context_text, '') as context_text,
          case
            when coalesce(g.missing_evidence_count, 0) > 0
              or coalesce(k.conflict_count, 0) > 0
              or coalesce(e.blocking_risk_count, 0) > 0 then 'blocked'
            when coalesce(e.stale_evidence_count, 0) > 0
              or coalesce(e.risk_count, 0) > 0 then 'needs_review'
            else 'ready'
          end as review_state
        from business_cases c
        left join evidence_rollup e using (case_id)
        left join gap_counts g using (case_id)
        left join conflict_counts k using (case_id)
        order by c.case_id
        """
    )
    conn.sql("drop table if exists agent_context")
    agent_context_rel.to_table("agent_context")
    agent_context = conn.sql("select * from agent_context")

    review_queue = agent_context.filter("review_state <> 'ready'").order(
        REVIEW_QUEUE_ORDER
    )
    status_summary = agent_context.aggregate(
        "review_state, count(*) as cases, sum(evidence_count) as evidence_records"
    ).order("review_state")
    return (
        conn.sql("select * from evidence_gaps"),
        conn.sql("select * from evidence_conflicts"),
        review_queue,
        status_summary,
    )


def write_artifacts(
    *,
    output_dir: Path,
    cases: Any,
    requirements: Any,
    public_assets: Any,
    evidence_links: Any,
    asset_features: Any,
    evidence_features: Any,
    agent_context: Any,
    evidence_gaps: Any,
    evidence_conflicts: Any,
    review_queue: Any,
    status_summary: Any,
    backend_metadata: dict[str, dict[str, Any]],
    modalities: list[str],
    runner: str,
    args: argparse.Namespace,
) -> dict[str, int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    input_dir = Path(args.input_dir)
    asset_catalog = Path(args.asset_catalog)
    default_scenario = input_dir.resolve() == DEFAULT_INPUT_DIR.resolve()
    default_assets = asset_catalog.resolve() == DEFAULT_ASSET_CATALOG.resolve()
    counts = {
        "case_rows": relation_row_count(cases),
        "requirement_rows": relation_row_count(requirements),
        "asset_rows": relation_row_count(public_assets),
        "input_rows": relation_row_count(evidence_links),
        "asset_feature_rows": relation_row_count(asset_features),
        "evidence_feature_rows": relation_row_count(evidence_features),
        "context_rows": relation_row_count(agent_context),
        "gap_rows": relation_row_count(evidence_gaps),
        "conflict_rows": relation_row_count(evidence_conflicts),
        "review_rows": relation_row_count(review_queue),
    }

    asset_features.write_parquet(str(output_dir / "asset_features.parquet"))
    agent_context.write_parquet(str(output_dir / "agent_context.parquet"))
    evidence_features.write_parquet(str(output_dir / "evidence_features.parquet"))
    evidence_gaps.write_csv(str(output_dir / "evidence_gaps.csv"))
    evidence_conflicts.write_csv(str(output_dir / "evidence_conflicts.csv"))
    review_queue.write_csv(str(output_dir / "review_queue.csv"))
    status_summary.write_csv(str(output_dir / "status_summary.csv"))
    write_json(
        output_dir / "manifest.json",
        {
            "example": "enterprise_multimodal_agent",
            "vane_version": vane.__version__,
            "runner": runner,
            "batch_size": args.batch_size,
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "scenario_schema_version": SCENARIO_SCHEMA_VERSION,
            "input_dir": display_path(input_dir),
            "asset_catalog": display_path(asset_catalog),
            "source_mode": (
                "public_snapshot"
                if default_scenario and default_assets
                else "custom_inputs"
            ),
            "scenario_mode": (
                "pinned_fixture" if default_scenario else "custom_scenario"
            ),
            "asset_source_mode": (
                "public_snapshot" if default_assets else "custom_asset_catalog"
            ),
            "scenario_snapshot_verified": default_scenario,
            "scenario_snapshot_metadata": (
                display_path(DEFAULT_SCENARIO_SNAPSHOT) if default_scenario else ""
            ),
            "scenario_snapshot_metadata_sha256": (
                file_sha256(DEFAULT_SCENARIO_SNAPSHOT) if default_scenario else ""
            ),
            "asset_snapshot_verified": default_assets,
            "asset_snapshot_metadata": (
                display_path(ASSET_SNAPSHOT_METADATA) if default_assets else ""
            ),
            "asset_snapshot_metadata_sha256": (
                file_sha256(ASSET_SNAPSHOT_METADATA) if default_assets else ""
            ),
            "source_files": sorted(
                path.name
                for path in required_input_paths(input_dir).values()
            ),
            "input_files": sorted(
                (
                    csv_file_metadata(path)
                    for path in required_input_paths(input_dir).values()
                ),
                key=lambda item: item["file"],
            ),
            "modalities": modalities,
            "freshness_days": FRESHNESS_DAYS,
            "content_hash": "sha256",
            **counts,
            "output_files": [
                "asset_features.parquet",
                "agent_context.parquet",
                "evidence_features.parquet",
                "evidence_gaps.csv",
                "evidence_conflicts.csv",
                "review_queue.csv",
                "status_summary.csv",
            ],
            **merge_backend_metadata(backend_metadata),
        },
    )
    return counts


def run(args: argparse.Namespace) -> None:
    runner = require_local_relation_runner(vane.current_config().runner)
    input_dir = Path(args.input_dir)
    asset_catalog = Path(args.asset_catalog)
    if input_dir.resolve() == DEFAULT_INPUT_DIR.resolve():
        verify_scenario_snapshot(input_dir, DEFAULT_SCENARIO_SNAPSHOT)
    if asset_catalog.resolve() == DEFAULT_ASSET_CATALOG.resolve():
        verify_public_asset_snapshot(asset_catalog, ASSET_SNAPSHOT_METADATA)

    conn = vane.connect()
    cases, requirements, public_assets, evidence_links, modalities = materialize_sources(
        conn,
        input_dir,
        asset_catalog,
    )

    asset_features_rel, backend_metadata = build_asset_feature_relations(
        public_assets,
        modalities,
        args,
    )
    conn.sql("drop table if exists asset_features")
    asset_features_rel.order("asset_id").to_table("asset_features")
    asset_features = conn.sql("select * from asset_features")

    evidence_features_rel = build_evidence_features(conn)
    conn.sql("drop table if exists evidence_features")
    evidence_features_rel.order("case_id, record_id").to_table("evidence_features")
    evidence_features = conn.sql("select * from evidence_features")

    evidence_gaps, evidence_conflicts, review_queue, status_summary = (
        build_case_relations(conn)
    )
    agent_context = conn.sql("select * from agent_context")
    counts = write_artifacts(
        output_dir=Path(args.output_dir),
        cases=cases,
        requirements=requirements,
        public_assets=public_assets,
        evidence_links=evidence_links,
        asset_features=asset_features,
        evidence_features=evidence_features,
        agent_context=agent_context,
        evidence_gaps=evidence_gaps,
        evidence_conflicts=evidence_conflicts,
        review_queue=review_queue,
        status_summary=status_summary,
        backend_metadata=backend_metadata,
        modalities=modalities,
        runner=runner,
        args=args,
    )

    print(f"Business cases: {counts['case_rows']}")
    print(f"Public assets: {counts['asset_rows']}")
    print(f"Evidence records: {counts['input_rows']}")
    print(f"Missing requirements: {counts['gap_rows']}")
    print(f"Conflicting claims: {counts['conflict_rows']}")
    print(f"Cases requiring review: {counts['review_rows']}")
    print(f"Output directory: {args.output_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build auditable Agent context from enterprise evidence relations.",
    )
    parser.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR))
    parser.add_argument("--asset-catalog", default=str(DEFAULT_ASSET_CATALOG))
    parser.add_argument("--batch-size", type=positive_int, default=4)
    parser.add_argument(
        "--execution-backend",
        choices=PUBLIC_BACKEND_CHOICES,
        default="auto",
        help="Let Vane infer the backend, or pin a task backend explicitly.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()
    try:
        require_local_relation_runner(vane.current_config().runner)
        validate_input_paths(Path(args.input_dir))
        if not Path(args.asset_catalog).is_file():
            raise FileNotFoundError(
                f"public asset catalog does not exist: {args.asset_catalog}"
            )
    except (RuntimeError, FileNotFoundError) as exc:
        parser.error(str(exc))
    return args


if __name__ == "__main__":
    run(parse_args())
