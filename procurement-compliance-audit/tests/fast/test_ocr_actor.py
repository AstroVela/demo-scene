from __future__ import annotations

import json
from types import SimpleNamespace

import io

from PIL import Image

from procurement_audit_sql_demo.vane_functions import EvidenceOcrActor


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (80, 40), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class FakeStore:
    def __init__(self, objects):
        self.objects = objects

    def get_bytes(self, bucket, object_key):
        return self.objects[(bucket, object_key)]


def test_ocr_actor_reuses_one_engine_for_multiple_minio_objects():
    calls = {"factory": 0, "engine": 0}

    def factory():
        calls["factory"] += 1

        def engine(_value):
            calls["engine"] += 1
            return [
                ([[0, 0], [60, 0], [60, 15], [0, 15]], "专家编号 EXP-001", 0.98),
                ([[0, 20], [60, 20], [60, 35], [0, 35]], "参加评审 是", 0.94),
            ]

        return engine

    store = FakeStore(
        {
            ("evidence", "one.png"): _png_bytes(),
            ("evidence", "two.png"): _png_bytes(),
        }
    )
    actor = EvidenceOcrActor(
        SimpleNamespace(),
        engine_factory=factory,
        store_factory=lambda _config: store,
    )
    first = json.loads(actor("evidence", "one.png"))
    second = json.loads(actor("evidence", "two.png"))

    assert calls == {"factory": 1, "engine": 2}
    assert first == second == {
        "error": None,
        "full_text": "专家编号 EXP-001\n参加评审 是",
        "mean_confidence": 0.96,
        "status": "success",
        "text_line_count": 2,
    }


def test_ocr_actor_returns_stable_unreadable_contract():
    actor = EvidenceOcrActor(
        SimpleNamespace(),
        engine_factory=lambda: lambda _value: [],
        store_factory=lambda _config: FakeStore({("evidence", "invalid.png"): b"not an image"}),
    )

    result = json.loads(actor("evidence", "invalid.png"))

    assert result["status"] == "unreadable"
    assert result["full_text"] == ""
    assert result["mean_confidence"] == 0.0
    assert result["text_line_count"] == 0
    assert result["error"] == "image_decode_failed"


def test_ocr_actor_reports_missing_minio_object():
    actor = EvidenceOcrActor(
        SimpleNamespace(),
        engine_factory=lambda: lambda _value: [],
        store_factory=lambda _config: FakeStore({}),
    )

    result = json.loads(actor("evidence", "missing.png"))

    assert result["status"] == "unreadable"
    assert result["error"] == "object_read_failed:KeyError"
