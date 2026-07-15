from __future__ import annotations

import json

from PIL import Image

from procurement_audit_sql_demo.vane_functions import EvidenceOcrActor


def _make_png(path):
    Image.new("RGB", (80, 40), "white").save(path, format="PNG")
    return path


def test_ocr_actor_reuses_one_engine_for_multiple_images(tmp_path):
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

    actor = EvidenceOcrActor(engine_factory=factory)
    first = json.loads(actor(str(_make_png(tmp_path / "one.png"))))
    second = json.loads(actor(str(_make_png(tmp_path / "two.png"))))

    assert calls == {"factory": 1, "engine": 2}
    assert first == second == {
        "error": None,
        "full_text": "专家编号 EXP-001\n参加评审 是",
        "mean_confidence": 0.96,
        "status": "success",
        "text_line_count": 2,
    }


def test_ocr_actor_returns_stable_unreadable_contract(tmp_path):
    invalid = tmp_path / "invalid.png"
    invalid.write_bytes(b"not an image")
    actor = EvidenceOcrActor(engine_factory=lambda: lambda _value: [])

    result = json.loads(actor(str(invalid)))

    assert result["status"] == "unreadable"
    assert result["full_text"] == ""
    assert result["mean_confidence"] == 0.0
    assert result["text_line_count"] == 0
    assert result["error"] == "image_decode_failed"


def test_ocr_actor_rejects_path_outside_its_allowed_root(tmp_path):
    allowed = tmp_path / "fixture"
    allowed.mkdir()
    outside = _make_png(tmp_path / "outside.png")
    actor = EvidenceOcrActor(
        allowed_root=allowed,
        engine_factory=lambda: lambda _value: [],
    )

    result = json.loads(actor(str(outside)))

    assert result["status"] == "unreadable"
    assert result["error"] == "path_outside_fixture"
