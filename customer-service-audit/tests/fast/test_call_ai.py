from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pyarrow as pa
import pytest
import vane

from customer_service_audit import call_ai, pipeline
from customer_service_audit.call_ai import ANALYSIS_SYSTEM_MESSAGE
from customer_service_audit.config import load_runtime_config
from customer_service_audit.vane_udfs import AsrTranscribeActor


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeRelation:
    def __init__(self, table):
        self.table = table

    def select(self, *expressions):
        names = [str(expression) for expression in expressions]
        return FakeRelation(self.table.select(names))

    def fetchall(self):
        return [
            tuple(row[name] for name in self.table.column_names)
            for row in self.table.to_pylist()
        ]


class FakeSession:
    def from_arrow(self, table):
        return FakeRelation(table)


def test_system_message_calibrates_sentiment_extremes() -> None:
    assert (
        "very_negative only for explicit intense anger"
        in ANALYSIS_SYSTEM_MESSAGE
    )
    assert "positive for ordinary approval" in ANALYSIS_SYSTEM_MESSAGE
    assert (
        "very_positive for explicit emphatic praise"
        in ANALYSIS_SYSTEM_MESSAGE
    )
    assert (
        "stated intent to keep supporting the company"
        in ANALYSIS_SYSTEM_MESSAGE
    )


def test_ray_prompt_projects_appended_response_before_materializing(
    monkeypatch,
) -> None:
    config = replace(
        load_runtime_config(PROJECT_ROOT / "runtime.yml"),
        runner="ray",
    )
    response = '{"problem_category":"billing_dispute"}'
    materialized_columns = []

    def fake_prompt(relation, _messages, **_options):
        table = relation.table.append_column(
            "raw_analysis_response",
            pa.array([response], type=pa.string()),
        )
        assert table.column_names == [
            "call_id",
            "prompt_text",
            "raw_analysis_response",
        ]
        return FakeRelation(table)

    def materialize(relation):
        materialized_columns.append(relation.table.column_names)
        return relation.table

    monkeypatch.setattr(call_ai, "probe_qwen", lambda _config: None)
    monkeypatch.setattr(vane.ai, "prompt", fake_prompt)

    result = call_ai.build_call_ai_relation(
        [
            {
                "call_id": "CALL-001",
                "object_key": "recordings/CALL-001.wav",
                "object_sha256": "1" * 64,
                "duration_seconds": 30.0,
                "transcript_text": "The invoice amount is incorrect.",
                "language_confidence": 0.99,
                "transcript_usable": True,
            }
        ],
        FakeSession(),
        config,
        request_relation_factory=FakeRelation,
        response_materializer=materialize,
        result_factory=lambda table: table,
    )

    assert materialized_columns == [["raw_analysis_response"]]
    assert result.to_pylist() == [
        {
            "call_id": "CALL-001",
            "raw_analysis_response": response,
        }
    ]


def test_pipeline_sets_openai_key_before_runner_initialization(monkeypatch) -> None:
    config = load_runtime_config(PROJECT_ROOT / "runtime.yml")
    worker_environment = {}

    class WorkerPrewarmed(RuntimeError):
        pass

    configured_runners = []

    def initialize_runner():
        worker_environment["OPENAI_API_KEY"] = call_ai.os.environ.get(
            "OPENAI_API_KEY"
        )
        raise WorkerPrewarmed

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(
        pipeline.vane,
        "configure",
        lambda *, runner: configured_runners.append(runner),
    )
    monkeypatch.setattr(pipeline, "probe_runtime", lambda _config: None)
    monkeypatch.setattr(pipeline.vane, "get_or_create_runner", initialize_runner)

    with pytest.raises(WorkerPrewarmed):
        pipeline.run_pipeline(config)

    assert configured_runners == [config.runner]
    assert worker_environment == {"OPENAI_API_KEY": config.ai.api_key}


def test_driver_catalog_rows_use_execute_instead_of_runner_relations() -> None:
    expected = pa.table({"call_id": ["CALL-001"], "status": ["ready"]})

    class DriverConnection:
        def execute(self, query):
            assert query == "select * from driver_only_calls"
            return self

        def to_arrow_table(self):
            return expected

        def sql(self, _query):
            raise AssertionError("Driver catalog read used the Runner Relation API")

    assert pipeline._relation_rows(
        DriverConnection(),
        "driver_only_calls",
    ) == expected.to_pylist()


def test_asr_actor_attaches_with_real_vane_api_in_ray_mode() -> None:
    config = load_runtime_config(PROJECT_ROOT / "runtime.yml")
    vane.configure(runner="ray")
    connection = vane.connect()
    try:
        vane.attach_function(
            AsrTranscribeActor(config.minio, config.asr),
            connection=connection,
            alias="asr_transcribe_json",
            parameters=["VARCHAR", "VARCHAR"],
            replace=True,
        )
    finally:
        connection.close()
        vane.configure(runner="local")
