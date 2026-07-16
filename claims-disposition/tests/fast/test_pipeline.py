from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from claims_disposition_sql_pipeline import pipeline


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_claim_material_runner_query_aggregates_precomputed_row_facts():
    sql_path = (
        PROJECT_ROOT
        / "src/claims_disposition_sql_pipeline/sql/intermediate/int_claim_material_facts.sql"
    )
    query = pipeline._claim_material_aggregation_query(sql_path)

    assert "__runner_claim_material_row_facts" in query
    assert "document_ocr_json(" not in query
    assert "minio_object_sha256(" not in query


def test_claim_damage_runner_query_aggregates_prevalidated_photo_results():
    sql_path = (
        PROJECT_ROOT
        / "src/claims_disposition_sql_pipeline/sql/intermediate/int_claim_damage_facts.sql"
    )
    query = pipeline._damage_aggregation_query(sql_path)

    assert "__runner_classified_photo_results" in query
    assert "photo_damage_result_json(" not in query


def test_materialize_relation_uses_runner_backed_relation_write():
    expected = pa.table({"value": [1, 2]})
    calls = []

    class Relation:
        def write_parquet(self, path):
            calls.append(Path(path))
            pq.write_table(expected, path)

        def to_arrow_table(self):
            raise AssertionError("direct DuckDB materialization used")

    actual = pipeline.materialize_relation(Relation())

    assert len(calls) == 1
    assert actual.equals(expected)
