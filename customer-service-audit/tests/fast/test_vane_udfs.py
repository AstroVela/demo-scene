from __future__ import annotations

import io
import json
import wave

import pytest

from customer_service_audit.vane_udfs import (
    analyze_audio_bytes,
    assess_transcript_quality,
    parse_call_analysis_result,
    stable_json,
)


def _wav_bytes(duration_frames: int = 16000, sample_rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(sample_rate)
        audio_file.writeframes(b"\x00\x00" * duration_frames)
    return buffer.getvalue()


def test_analyze_audio_bytes_success() -> None:
    probe = analyze_audio_bytes(_wav_bytes())
    assert probe["status"] == "success"
    assert probe["audio_usable"] is True
    assert probe["sample_rate"] == 16000
    assert probe["duration_seconds"] == pytest.approx(1.0)


def test_analyze_audio_bytes_too_short() -> None:
    probe = analyze_audio_bytes(_wav_bytes(duration_frames=800))
    assert probe["status"] == "success"
    assert probe["audio_usable"] is False
    assert "audio_too_short" in probe["quality_reasons"]


def test_analyze_audio_bytes_corrupt() -> None:
    probe = analyze_audio_bytes(b"not a wav file at all")
    assert probe["status"] == "decode_error"
    assert probe["audio_usable"] is False


def test_assess_transcript_quality_usable() -> None:
    quality = assess_transcript_quality(
        {"status": "success", "text": "客户要求退款处理", "language_probability": 0.95},
        min_text_chars=8,
    )
    assert quality["transcript_usable"] is True
    assert quality["failure_reasons"] == []


def test_assess_transcript_quality_too_short() -> None:
    quality = assess_transcript_quality(
        {"status": "success", "text": "短", "language_probability": 0.9},
        min_text_chars=8,
    )
    assert quality["transcript_usable"] is False
    assert "transcript_too_short" in quality["failure_reasons"]


def _valid_analysis() -> dict:
    return {
        "problem_category": "refund_request",
        "customer_sentiment": "very_negative",
        "sentiment_score": -0.9,
        "urgency": "high",
        "key_issues": ["延迟发货"],
        "customer_request": "立即退款",
        "resolution_status": "partially_resolved",
        "requires_followup": True,
        "agent_attitude": "professional",
        "summary": "客户要求退款。",
        "confidence": 0.9,
    }


def test_parse_call_analysis_valid() -> None:
    result = json.loads(
        parse_call_analysis_result(stable_json(_valid_analysis()), "CALL-1", "s" * 64)
    )
    assert result["status"] == "success"
    assert result["problem_category"] == "refund_request"
    assert result["uncertainty_reasons"] == []


def test_parse_call_analysis_strips_markdown_fence() -> None:
    fenced = "```json\n" + stable_json(_valid_analysis()) + "\n```"
    result = json.loads(parse_call_analysis_result(fenced, "CALL-1", "s" * 64))
    assert result["status"] == "success"


def test_parse_call_analysis_invalid_category() -> None:
    bad = _valid_analysis()
    bad["problem_category"] = "not_a_real_category"
    result = json.loads(
        parse_call_analysis_result(stable_json(bad), "CALL-1", "s" * 64)
    )
    assert result["status"] == "invalid_response"
    assert "invalid_problem_category" in result["uncertainty_reasons"]


def test_parse_call_analysis_bad_json() -> None:
    result = json.loads(parse_call_analysis_result("{not json", "CALL-1", "s" * 64))
    assert result["status"] == "invalid_response"
    assert "invalid_json" in result["uncertainty_reasons"]
