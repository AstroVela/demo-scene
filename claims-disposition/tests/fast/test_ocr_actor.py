from __future__ import annotations

import io
import json

from PIL import Image

from claims_disposition_sql_pipeline import vane_udfs
from claims_disposition_sql_pipeline.config import MinioConfig


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (80, 40), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_document_ocr_actor_initializes_engine_lazily_and_reuses_it(monkeypatch):
    objects = {
        ("claims", "one.png"): _png_bytes(),
        ("claims", "two.png"): _png_bytes(),
    }

    class Store:
        def __init__(self, _config):
            pass

        def get_bytes(self, bucket, object_key):
            return objects[(bucket, object_key)]

    calls = {"factory": 0, "engine": 0}

    def engine_factory():
        calls["factory"] += 1

        def engine(_value):
            calls["engine"] += 1
            return [
                ([[0, 0], [60, 0], [60, 15], [0, 15]], "CLAIM NUMBER: C-1", 0.98)
            ]

        return engine

    monkeypatch.setattr(vane_udfs, "MinioStore", Store)
    actor = vane_udfs.DocumentOcrActor(
        MinioConfig("127.0.0.1:9000", "key", "secret", False, "claims"),
        engine_factory=engine_factory,
    )

    assert calls == {"factory": 0, "engine": 0}
    first = json.loads(actor("claims", "one.png"))
    second = json.loads(actor("claims", "two.png"))

    assert calls == {"factory": 1, "engine": 2}
    assert first == second
    assert first["status"] == "success"
