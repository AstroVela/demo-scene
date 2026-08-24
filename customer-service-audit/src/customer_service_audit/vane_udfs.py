"""Stateless Vane expression UDFs, the stateful ASR actor, and validators."""

from __future__ import annotations

from dataclasses import dataclass
import io
import json
import math
import re
import urllib.request
from typing import Any, Mapping
import uuid
import wave

import vane

from .config import AsrConfig, MinioConfig
from .minio_store import MinioStore


PROBLEM_CATEGORIES = (
    "refund_request",
    "billing_dispute",
    "service_complaint",
    "technical_support",
    "product_consultation",
    "praise",
    "other",
)
CUSTOMER_SENTIMENTS = (
    "very_negative",
    "negative",
    "neutral",
    "positive",
    "very_positive",
)
URGENCY_LEVELS = ("low", "medium", "high")
RESOLUTION_STATUSES = (
    "resolved",
    "partially_resolved",
    "unresolved",
    "not_applicable",
)
AGENT_ATTITUDES = ("professional", "acceptable", "poor", "unknown")

_PROBLEM_CATEGORY_SET = frozenset(PROBLEM_CATEGORIES)
_CUSTOMER_SENTIMENT_SET = frozenset(CUSTOMER_SENTIMENTS)
_URGENCY_LEVEL_SET = frozenset(URGENCY_LEVELS)
_RESOLUTION_STATUS_SET = frozenset(RESOLUTION_STATUSES)
_AGENT_ATTITUDE_SET = frozenset(AGENT_ATTITUDES)


@dataclass(frozen=True)
class SqlUdfSpec:
    function: object
    alias: str
    parameters: tuple[str, ...]


@dataclass(frozen=True)
class MinioUdfs:
    object_exists: object
    object_sha256: object
    audio_probe: object


def stable_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def analyze_audio_bytes(value: bytes) -> dict[str, Any]:
    """Probe one PCM WAV object with the standard library only."""

    try:
        with wave.open(io.BytesIO(value), "rb") as audio_file:
            channels = audio_file.getnchannels()
            sample_rate = audio_file.getframerate()
            sample_width = audio_file.getsampwidth()
            frame_count = audio_file.getnframes()
    except (EOFError, wave.Error) as exc:
        return {
            "status": "decode_error",
            "audio_usable": False,
            "error_type": type(exc).__name__,
        }

    if sample_rate <= 0 or channels <= 0 or sample_width <= 0:
        return {
            "status": "decode_error",
            "audio_usable": False,
            "error_type": "invalid_wave_header",
        }

    duration = frame_count / float(sample_rate)
    reasons: list[str] = []
    if duration < 1.0:
        reasons.append("audio_too_short")
    if duration > 900.0:
        reasons.append("audio_too_long")

    return {
        "status": "success",
        "channels": channels,
        "sample_rate": sample_rate,
        "sample_width_bytes": sample_width,
        "frame_count": frame_count,
        "duration_seconds": round(duration, 4),
        "audio_usable": not reasons,
        "quality_reasons": reasons,
    }


def build_minio_udfs(config: MinioConfig) -> MinioUdfs:
    @vane.func(return_dtype="BOOLEAN", name="minio_object_exists")
    def object_exists(bucket: str, object_key: str) -> bool:
        return MinioStore(config).exists(bucket, object_key)

    @vane.func(return_dtype="VARCHAR", name="minio_object_sha256")
    def object_sha256(bucket: str, object_key: str) -> str | None:
        return MinioStore(config).sha256(bucket, object_key)

    @vane.func(return_dtype="VARCHAR", name="audio_probe_json")
    def audio_probe(bucket: str, object_key: str) -> str:
        value = MinioStore(config).get_bytes(bucket, object_key)
        return stable_json(analyze_audio_bytes(value))

    return MinioUdfs(object_exists, object_sha256, audio_probe)


def stateless_udf_specs(udfs: MinioUdfs) -> list[SqlUdfSpec]:
    return [
        SqlUdfSpec(udfs.object_exists, "minio_object_exists", ("VARCHAR", "VARCHAR")),
        SqlUdfSpec(udfs.object_sha256, "minio_object_sha256", ("VARCHAR", "VARCHAR")),
        SqlUdfSpec(udfs.audio_probe, "audio_probe_json", ("VARCHAR", "VARCHAR")),
    ]


# ---------------------------------------------------------------------------
# ASR actor: faster-whisper loaded once per worker, mirroring DocumentOcrActor
# ---------------------------------------------------------------------------


def _build_faster_whisper(config: AsrConfig):
    from faster_whisper import WhisperModel

    model = WhisperModel(
        config.model,
        device=config.device,
        compute_type=config.compute_type,
    )

    def transcribe(wav_bytes: bytes) -> dict[str, Any]:
        segments, info = model.transcribe(
            io.BytesIO(wav_bytes),
            language=config.language,
            beam_size=config.beam_size,
            vad_filter=True,
        )
        segment_list = list(segments)
        text = "".join(segment.text for segment in segment_list).strip()
        return {
            "text": text,
            "language": str(getattr(info, "language", "") or ""),
            "language_probability": float(
                getattr(info, "language_probability", 0.0) or 0.0
            ),
            "duration_seconds": round(
                float(getattr(info, "duration", 0.0) or 0.0), 4
            ),
            "segment_count": len(segment_list),
        }

    return transcribe


def _wav_duration_seconds(wav_bytes: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as audio_file:
            rate = audio_file.getframerate()
            if rate <= 0:
                return 0.0
            return round(audio_file.getnframes() / rate, 4)
    except (wave.Error, EOFError):
        return 0.0


def _build_openai_audio(config: AsrConfig):
    """Transcribe through an OpenAI-compatible /audio/transcriptions endpoint.

    The engine posts each recording as multipart form data to an internal
    model gateway (for example a qwen-asr service), so no local model files
    and no Hugging Face access are required.
    """

    endpoint = config.base_url.rstrip("/")
    if not endpoint.endswith("/audio/transcriptions"):
        endpoint = f"{endpoint}/audio/transcriptions"

    def transcribe(wav_bytes: bytes) -> dict[str, Any]:
        boundary = f"----vaneAudit{uuid.uuid4().hex}"
        text_parts = [
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="model"\r\n\r\n'
                f"{config.model}\r\n"
            ),
            (
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="response_format"\r\n\r\n'
                "json\r\n"
            ),
        ]
        if config.language:
            text_parts.append(
                f"--{boundary}\r\n"
                'Content-Disposition: form-data; name="language"\r\n\r\n'
                f"{config.language}\r\n"
            )
        text_parts.append(
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        )
        body = (
            "".join(text_parts).encode("utf-8")
            + wav_bytes
            + f"\r\n--{boundary}--\r\n".encode("utf-8")
        )
        request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Authorization": f"Bearer {config.api_key}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=config.timeout_seconds
            ) as response:
                raw = response.read().decode("utf-8")
        except Exception as exc:
            raise RuntimeError(
                f"openai-audio transcription failed against {endpoint}: {exc}"
            ) from exc
        try:
            payload = json.loads(raw)
            text = str(payload.get("text", "") or "").strip()
        except (json.JSONDecodeError, AttributeError):
            # Some gateways return plain text despite response_format=json.
            text = raw.strip()
        return {
            "text": text,
            "language": config.language,
            "language_probability": 1.0 if config.language else 0.0,
            "duration_seconds": _wav_duration_seconds(wav_bytes),
            "segment_count": 1 if text else 0,
        }

    return transcribe


def _default_engine_factory(config: AsrConfig):
    """Pick the ASR backend for the configured engine."""

    if config.engine == "openai-audio":
        return lambda: _build_openai_audio(config)
    return lambda: _build_faster_whisper(config)


@vane.cls(
    actor_number=1,
    return_dtype="VARCHAR",
    name="asr_transcribe_json",
    gpus=0,
)
class AsrTranscribeActor:
    """Stateful ASR worker: the whisper model loads lazily once per actor."""

    def __init__(
        self,
        minio_config: MinioConfig,
        asr_config: AsrConfig,
        engine_factory=None,
    ) -> None:
        self.store = MinioStore(minio_config)
        self.asr_config = asr_config
        self._engine_factory = engine_factory or _default_engine_factory(asr_config)
        self.engine = None

    def __call__(self, bucket: str, object_key: str) -> str:
        value = self.store.get_bytes(bucket, object_key)
        probe = analyze_audio_bytes(value)
        if probe.get("status") != "success" or probe.get("audio_usable") is not True:
            return stable_json(
                {
                    "status": "unusable_audio",
                    "text": "",
                    "language": "",
                    "language_probability": 0.0,
                    "duration_seconds": 0.0,
                    "segment_count": 0,
                    "engine": self.asr_config.engine,
                    "model": self.asr_config.model,
                }
            )
        if self.engine is None:
            self.engine = self._engine_factory()
        observation = self.engine(value)
        return stable_json(
            {
                "status": "success",
                "text": observation["text"],
                "language": observation["language"],
                "language_probability": round(
                    observation["language_probability"], 4
                ),
                "duration_seconds": observation["duration_seconds"],
                "segment_count": observation["segment_count"],
                "engine": self.asr_config.engine,
                "model": self.asr_config.model,
            }
        )


# ---------------------------------------------------------------------------
# Transcript quality gate
# ---------------------------------------------------------------------------


def assess_transcript_quality(
    transcript: Mapping[str, Any],
    min_text_chars: int,
) -> dict[str, Any]:
    text = str(transcript.get("text", "") or "").strip()
    failure_reasons: list[str] = []
    if transcript.get("status") != "success":
        failure_reasons.append("asr_unsuccessful")
    if len(text) < min_text_chars:
        failure_reasons.append("transcript_too_short")
    probability = transcript.get("language_probability", 0.0)
    confidence = (
        float(probability)
        if isinstance(probability, (int, float)) and not isinstance(probability, bool)
        else 0.0
    )
    return {
        "transcript_usable": not failure_reasons,
        "asr_status": str(transcript.get("status", "unknown")),
        "text_length": len(text),
        "min_text_chars": int(min_text_chars),
        "language_confidence": round(confidence, 4),
        "failure_reasons": failure_reasons,
    }


@vane.func(return_dtype="VARCHAR", name="transcript_quality_json")
def transcript_quality_json(transcript_json: str, min_text_chars: int) -> str:
    try:
        transcript = json.loads(transcript_json)
    except (TypeError, json.JSONDecodeError):
        transcript = {}
    if not isinstance(transcript, Mapping):
        transcript = {}
    return stable_json(assess_transcript_quality(transcript, min_text_chars))


# ---------------------------------------------------------------------------
# AI response contract validation
# ---------------------------------------------------------------------------


def _analysis_error(reason: str, call_id: str, sha256: str) -> str:
    return stable_json(
        {
            "status": "invalid_response",
            "call_id": call_id,
            "sha256": sha256,
            "problem_category": "other",
            "customer_sentiment": "neutral",
            "sentiment_score": 0.0,
            "urgency": "medium",
            "key_issues": [],
            "customer_request": "",
            "resolution_status": "not_applicable",
            "requires_followup": True,
            "agent_attitude": "unknown",
            "summary": "",
            "uncertainty_reasons": [reason],
            "confidence": 0.0,
        }
    )


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return [item.strip() for item in value if item.strip()]


def _score(value: Any, lower: float, upper: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result) or not lower <= result <= upper:
        return None
    return result


def parse_call_analysis_result(
    raw_response: str,
    call_id: str,
    sha256: str,
) -> str:
    """Normalize and strictly validate one untrusted analysis response."""

    value = raw_response.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return _analysis_error("invalid_json", call_id, sha256)
    if not isinstance(payload, Mapping):
        return _analysis_error("response_not_object", call_id, sha256)

    problem_category = payload.get("problem_category")
    if problem_category not in _PROBLEM_CATEGORY_SET:
        return _analysis_error("invalid_problem_category", call_id, sha256)
    customer_sentiment = payload.get("customer_sentiment")
    if customer_sentiment not in _CUSTOMER_SENTIMENT_SET:
        return _analysis_error("invalid_customer_sentiment", call_id, sha256)
    sentiment_score = _score(payload.get("sentiment_score"), -1.0, 1.0)
    if sentiment_score is None:
        return _analysis_error("invalid_sentiment_score", call_id, sha256)
    urgency = payload.get("urgency")
    if urgency not in _URGENCY_LEVEL_SET:
        return _analysis_error("invalid_urgency", call_id, sha256)
    key_issues = _string_list(payload.get("key_issues"))
    if key_issues is None:
        return _analysis_error("invalid_key_issues", call_id, sha256)
    customer_request = payload.get("customer_request")
    if not isinstance(customer_request, str) or not customer_request.strip():
        return _analysis_error("invalid_customer_request", call_id, sha256)
    resolution_status = payload.get("resolution_status")
    if resolution_status not in _RESOLUTION_STATUS_SET:
        return _analysis_error("invalid_resolution_status", call_id, sha256)
    requires_followup = payload.get("requires_followup")
    if not isinstance(requires_followup, bool):
        return _analysis_error("invalid_requires_followup", call_id, sha256)
    agent_attitude = payload.get("agent_attitude")
    if agent_attitude not in _AGENT_ATTITUDE_SET:
        return _analysis_error("invalid_agent_attitude", call_id, sha256)
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return _analysis_error("invalid_summary", call_id, sha256)
    confidence = _score(payload.get("confidence"), 0.0, 1.0)
    if confidence is None:
        return _analysis_error("invalid_confidence", call_id, sha256)

    return stable_json(
        {
            "status": "success",
            "call_id": call_id,
            "sha256": sha256,
            "problem_category": problem_category,
            "customer_sentiment": customer_sentiment,
            "sentiment_score": round(sentiment_score, 4),
            "urgency": urgency,
            "key_issues": key_issues,
            "customer_request": customer_request.strip(),
            "resolution_status": resolution_status,
            "requires_followup": requires_followup,
            "agent_attitude": agent_attitude,
            "summary": summary.strip(),
            "uncertainty_reasons": [],
            "confidence": round(confidence, 4),
        }
    )


@vane.func(return_dtype="VARCHAR", name="validate_call_analysis_json")
def validate_call_analysis_json(
    raw_response: str,
    call_id: str,
    sha256: str,
) -> str:
    return parse_call_analysis_result(raw_response, call_id, sha256)
