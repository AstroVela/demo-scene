from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from claims_disposition_sql_pipeline import pipeline
from claims_disposition_sql_pipeline.config import load_runtime_config
from claims_disposition_sql_pipeline.fixture_loader import build_fixture
from claims_disposition_sql_pipeline.vane_udfs import (
    photo_damage_result_json,
    stable_json,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_claim_material_ocr_is_a_direct_runner_sql_projection():
    ocr_statement = pipeline.DOCUMENT_OCR_UDF_STAGE.read_text(encoding="utf-8")
    material_statement = pipeline.MATERIAL_FACT_STAGE.read_text(
        encoding="utf-8"
    )

    assert ocr_statement.count("document_ocr_json(") == 1
    assert "json_extract" not in ocr_statement
    assert "int_claim_document_ocr_udf" in material_statement
    assert "document_ocr_json(" not in material_statement


def test_claim_material_sql_calls_ocr_once_per_document():
    config = load_runtime_config(PROJECT_ROOT / "runtime.yml")
    fixture = build_fixture(config.minio.bucket)
    ocr_calls = []
    connection = duckdb.connect()
    try:
        pipeline.register_or_replace_table(
            connection,
            "claims_runtime_claims",
            pipeline.rows_to_arrow(list(fixture.claims)),
        )
        pipeline.register_or_replace_table(
            connection,
            "claims_runtime_run_config",
            pipeline.rows_to_arrow(
                [
                    pipeline.build_run_config_row(
                        config,
                        datetime(2026, 7, 17, tzinfo=timezone.utc),
                    )
                ]
            ),
        )
        connection.create_function(
            "minio_object_exists",
            lambda _bucket, _object_key: True,
            ["VARCHAR", "VARCHAR"],
            "BOOLEAN",
        )
        connection.create_function(
            "minio_object_sha256",
            lambda _bucket, _object_key: "0" * 64,
            ["VARCHAR", "VARCHAR"],
            "VARCHAR",
        )
        connection.create_function(
            "photo_quality_json",
            lambda _bucket, _object_key: stable_json(
                {
                    "status": "success",
                    "photo_usable": True,
                    "quality_score": 0.95,
                }
            ),
            ["VARCHAR", "VARCHAR"],
            "VARCHAR",
        )

        def document_ocr(_bucket, object_key):
            ocr_calls.append(object_key)
            return stable_json(
                {
                    "status": "success",
                    "mean_confidence": 0.95,
                    "text_lines": [],
                }
            )

        connection.create_function(
            "document_ocr_json",
            document_ocr,
            ["VARCHAR", "VARCHAR"],
            "VARCHAR",
        )
        connection.create_function(
            "document_fields_json",
            lambda _ocr: stable_json({"claim_number": "fixture"}),
            ["VARCHAR"],
            "VARCHAR",
        )
        connection.create_function(
            "document_quality_json",
            lambda _ocr, _fields, _claim_id, _required, _confidence: stable_json(
                {"document_usable": True}
            ),
            ["VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR", "DOUBLE"],
            "VARCHAR",
        )

        for sql_path in pipeline.SQL_STAGES:
            pipeline._execute_sql_file(connection, sql_path)
        pipeline._execute_sql_file(connection, pipeline.MATERIAL_INPUT_STAGE)
        pipeline._execute_sql_file(connection, pipeline.OBJECT_PROBE_UDF_STAGE)
        pipeline._execute_sql_file(connection, pipeline.OBJECT_FACT_STAGE)
        for sql_path in pipeline.OBJECT_FACT_UDF_STAGES:
            pipeline._execute_sql_file(connection, sql_path)
        pipeline._execute_sql_file(connection, pipeline.DOCUMENT_FIELDS_UDF_STAGE)
        pipeline._execute_sql_file(connection, pipeline.DOCUMENT_QUALITY_INPUT_STAGE)
        pipeline._execute_sql_file(connection, pipeline.DOCUMENT_QUALITY_UDF_STAGE)
        pipeline._execute_sql_file(connection, pipeline.MATERIAL_FACT_STAGE)
        rows = connection.sql(
            "select claim_id, document_ocr_json "
            "from int_claim_material_facts order by claim_id"
        ).fetchall()
    finally:
        connection.close()

    assert len(rows) == 4
    assert len(ocr_calls) == 4
    assert all("/documents/" in object_key for object_key in ocr_calls)


def test_claim_damage_sql_owns_validation_classification_and_aggregation():
    validation_statement = pipeline.DAMAGE_VALIDATION_UDF_STAGE.read_text(
        encoding="utf-8"
    )
    fact_statement = pipeline.DAMAGE_FACT_STAGE.read_text(
        encoding="utf-8"
    )

    assert validation_statement.count("photo_damage_result_json(") == 1
    assert "json_extract" not in validation_statement
    assert "from int_claim_damage_validation_udf" in fact_statement
    assert "positive_damage_result" in fact_statement
    assert "negative_damage_result" in fact_statement
    assert "aggregated_damage_facts" in fact_statement
    assert "json_list_has_meaningful_damage" not in fact_statement


def test_claim_damage_runner_validation_feeds_pure_sql_rules(tmp_path):
    photo_sha256 = "1" * 64
    photo_input = {
        "file_id": "PHOTO-001",
        "file_order": 1,
        "sha256": photo_sha256,
        "photo_quality": {"photo_usable": True},
    }
    raw_response = stable_json(
        {
            "vehicle_visible": True,
            "target_vehicle_clear": True,
            "damage_visible": True,
            "damaged_parts": ["front_bumper"],
            "damage_types": ["dent"],
            "evidence_summary": "A dent is visible on the front bumper.",
            "finding_determinate": True,
            "evidence_limitations": [],
            "severity_hint": "minor",
            "confidence": 0.95,
        }
    )
    connection = duckdb.connect()
    runner_connection = duckdb.connect()
    try:
        pipeline.register_or_replace_table(
            connection,
            "int_claim_material_facts",
            pa.Table.from_pylist(
                [
                    {
                        "claim_id": "CLM-001",
                        "model_input_usable": True,
                        "usable_photo_inputs_json": stable_json([photo_input]),
                    }
                ]
            ),
        )
        pipeline.register_or_replace_table(
            connection,
            "int_claim_photo_ai",
            pa.Table.from_pylist(
                [
                    {
                        "claim_id": "CLM-001",
                        "file_id": "PHOTO-001",
                        "file_order": 1,
                        "photo_sha256": photo_sha256,
                        "raw_damage_response": raw_response,
                    }
                ]
            ),
        )
        runner_connection.create_function(
            "photo_damage_result_json",
            photo_damage_result_json.python_function,
            ["VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR"],
            "VARCHAR",
        )
        pipeline._execute_sql_file(connection, pipeline.DAMAGE_VALIDATION_INPUT_STAGE)
        pipeline._execute_runner_sql_file(
            connection,
            runner_connection,
            pipeline.DAMAGE_VALIDATION_UDF_STAGE,
            workspace=pipeline.RunnerWorkspace(tmp_path),
            source_relations=("int_claim_damage_validation_inputs",),
            materializer=lambda relation: relation.to_arrow_table(),
        )
        pipeline._execute_sql_file(connection, pipeline.DAMAGE_FACT_STAGE)
        result = connection.sql(
            "select model_status, positive_damage_result_count, "
            "negative_damage_result_count, severity_hint "
            "from int_claim_damage_facts"
        ).fetchone()
    finally:
        runner_connection.close()
        connection.close()

    assert result == ("success", 1, 0, "minor")


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
