from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path

import pyarrow as pa
import pytest
import vane

from claims_disposition_sql_pipeline import photo_ai
from claims_disposition_sql_pipeline.config import load_runtime_config
from claims_disposition_sql_pipeline.vane_udfs import stable_json


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class FakeSession:
    def from_arrow(self, table):
        return table


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


def test_local_runner_uses_vane_provider_without_relation_actor(monkeypatch):
    config = replace(
        load_runtime_config(PROJECT_ROOT / "runtime.yml"),
        runner="local",
    )
    image_bytes = b"fixture image bytes"
    digest = hashlib.sha256(image_bytes).hexdigest()
    object_key = "claims/CLM-001/photos/PHOTO-001.png"
    material_rows = [
        {
            "claim_id": "CLM-001",
            "description": "Front bumper damage",
            "model_input_usable": True,
            "usable_photo_inputs_json": stable_json(
                [
                    {
                        "file_id": "PHOTO-001",
                        "file_order": 1,
                        "bucket": config.minio.bucket,
                        "object_key": object_key,
                        "sha256": digest,
                        "photo_quality": {"photo_usable": True},
                    }
                ]
            ),
        }
    ]
    provider_calls = []
    prompt_calls = []

    class Store:
        def get_bytes(self, bucket, key):
            assert (bucket, key) == (config.minio.bucket, object_key)
            return image_bytes

    class Prompter:
        async def prompt(self, messages):
            prompt_calls.append(messages)
            return '{"damage_visible":true}'

    class Descriptor:
        def instantiate(self):
            return Prompter()

    class Provider:
        def get_prompter(self, **options):
            provider_calls.append(options)
            return Descriptor()

    monkeypatch.setattr(photo_ai, "MinioStore", lambda _config: Store())
    monkeypatch.setattr(photo_ai, "probe_qwen", lambda _config: None)
    monkeypatch.setattr(
        vane.ai,
        "load_provider",
        lambda provider, **options: provider_calls.append((provider, options))
        or Provider(),
    )
    monkeypatch.setattr(
        vane.ai,
        "prompt",
        lambda *_args, **_kwargs: pytest.fail(
            "LocalRunner must not use the relation actor boundary"
        ),
    )

    result = photo_ai.build_photo_ai_relation(
        material_rows,
        FakeSession(),
        config,
    )

    assert isinstance(result, pa.Table)
    assert provider_calls[0][0] == "openai"
    assert provider_calls[1]["system_message"] == photo_ai.DAMAGE_SYSTEM_MESSAGE
    assert len(prompt_calls) == 1
    assert prompt_calls[0][1] == image_bytes
    assert result.to_pylist() == [
        {
            "claim_id": "CLM-001",
            "file_id": "PHOTO-001",
            "file_order": 1,
            "photo_sha256": digest,
            "raw_damage_response": '{"damage_visible":true}',
        }
    ]


def test_ray_prompt_projects_appended_response_before_materializing(monkeypatch):
    config = replace(
        load_runtime_config(PROJECT_ROOT / "runtime.yml"),
        runner="ray",
    )
    image_bytes = b"fixture image bytes"
    digest = hashlib.sha256(image_bytes).hexdigest()
    object_key = "claims/CLM-001/photos/PHOTO-001.png"
    response = '{"damage_visible":true}'
    material_rows = [
        {
            "claim_id": "CLM-001",
            "description": "Front bumper damage",
            "model_input_usable": True,
            "usable_photo_inputs_json": stable_json(
                [
                    {
                        "file_id": "PHOTO-001",
                        "file_order": 1,
                        "bucket": config.minio.bucket,
                        "object_key": object_key,
                        "sha256": digest,
                        "photo_quality": {"photo_usable": True},
                    }
                ]
            ),
        }
    ]
    materialized_columns = []

    class Store:
        def get_bytes(self, bucket, key):
            assert (bucket, key) == (config.minio.bucket, object_key)
            return image_bytes

    def fake_prompt(relation, _messages, **_options):
        table = relation.table.append_column(
            "raw_damage_response",
            pa.array([response], type=pa.string()),
        )
        assert table.column_names == [
            "claim_id",
            "file_id",
            "file_order",
            "photo_sha256",
            "prompt_text",
            "image_bytes",
            "raw_damage_response",
        ]
        return FakeRelation(table)

    def materialize(relation):
        materialized_columns.append(relation.table.column_names)
        return relation.table

    monkeypatch.setattr(photo_ai, "MinioStore", lambda _config: Store())
    monkeypatch.setattr(photo_ai, "probe_qwen", lambda _config: None)
    monkeypatch.setattr(vane.ai, "prompt", fake_prompt)

    result = photo_ai.build_photo_ai_relation(
        material_rows,
        FakeSession(),
        config,
        request_relation_factory=FakeRelation,
        response_materializer=materialize,
        result_factory=lambda table: table,
    )

    assert materialized_columns == [["raw_damage_response"]]
    assert result.to_pylist() == [
        {
            "claim_id": "CLM-001",
            "file_id": "PHOTO-001",
            "file_order": 1,
            "photo_sha256": digest,
            "raw_damage_response": response,
        }
    ]
