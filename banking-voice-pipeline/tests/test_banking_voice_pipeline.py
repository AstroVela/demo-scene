"""Offline tests for the banking voice pipeline contracts.

These tests never download data, load Whisper, or call the Jev API.
"""

from __future__ import annotations

import io
import unittest
import wave

import numpy as np
import pyarrow as pa

from src.banking_voice_pipeline import (
    DATA_REVISION,
    INTENTS,
    JEV_MODEL,
    MODEL_REVISION,
    SAMPLE_RATE,
    decode_audio,
    keyword_intent,
    metrics,
    questions,
    queue,
    validate_transcript,
)

try:
    import av  # noqa: F401
    import faster_whisper.audio  # noqa: F401

    AUDIO_DEPS = True
except ImportError:  # pragma: no cover - depends on the installed environment
    AUDIO_DEPS = False


def wav_bytes(channels, sample_rate=SAMPLE_RATE):
    frames = np.stack([np.asarray(channel, dtype=np.float64) for channel in channels], axis=1)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(len(channels))
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes((np.clip(frames, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


class RevisionPinsTests(unittest.TestCase):
    def test_pinned_revisions_and_model(self):
        self.assertEqual(DATA_REVISION, "40ce77cb32a384e4d50a568e1ec39ac804019d33")
        self.assertEqual(MODEL_REVISION, "536b0662742c02347bc0e980a01041f333bce120")
        self.assertEqual(JEV_MODEL, "jev-1.13.0")


class IntentDefinitionTests(unittest.TestCase):
    def test_every_intent_defines_definition_queue_and_keywords(self):
        self.assertEqual(len(INTENTS), 14)
        for name, (definition, queue_name, keywords) in INTENTS.items():
            self.assertTrue(definition, name)
            self.assertTrue(queue_name, name)
            self.assertTrue(keywords, name)


class KeywordIntentTests(unittest.TestCase):
    def test_single_keyword_match_wins(self):
        self.assertEqual(keyword_intent("我想查一下余额"), "balance")

    def test_tie_between_intents_falls_back_to_other(self):
        self.assertEqual(keyword_intent("我要冻结账户并查询余额"), "other")

    def test_no_keyword_falls_back_to_other(self):
        self.assertEqual(keyword_intent("你好，请帮我转人工"), "other")

    def test_whitespace_and_case_are_normalized(self):
        self.assertEqual(keyword_intent(" ATM   取款限额 "), "atm_limit")


class QueueMappingTests(unittest.TestCase):
    def test_known_intents_map_to_business_queues(self):
        self.assertEqual(queue("freeze"), "card_security")
        self.assertEqual(queue("pay_bill"), "payments")
        self.assertEqual(queue("balance"), "account_information")

    def test_unknown_intent_passes_through(self):
        self.assertEqual(queue("other"), "other")
        self.assertIsNone(queue(None))


class ValidateTranscriptTests(unittest.TestCase):
    def test_accepts_ordered_words(self):
        validate_transcript(
            [
                {"role": "caller", "start_ms": 0, "end_ms": 500, "text": "你好"},
                {"role": "caller", "start_ms": 500, "end_ms": 900, "text": "请问"},
            ],
            1000,
        )

    def test_rejects_empty_transcript(self):
        with self.assertRaises(ValueError):
            validate_transcript([], 1000)

    def test_rejects_abnormally_repeated_text(self):
        words = [
            {"role": "caller", "start_ms": index * 100, "end_ms": index * 100 + 50, "text": "谢谢"}
            for index in range(5)
        ]
        with self.assertRaises(ValueError):
            validate_transcript(words, 1000)

    def test_rejects_timestamp_outside_recording(self):
        with self.assertRaises(ValueError):
            validate_transcript([{"role": "caller", "start_ms": 0, "end_ms": 1500, "text": "你好"}], 1000)

    def test_rejects_out_of_order_timestamps(self):
        with self.assertRaises(ValueError):
            validate_transcript(
                [
                    {"role": "caller", "start_ms": 500, "end_ms": 700, "text": "请问"},
                    {"role": "caller", "start_ms": 100, "end_ms": 400, "text": "你好"},
                ],
                1000,
            )


class MetricsTests(unittest.TestCase):
    def test_perfect_predictions(self):
        labels = list(INTENTS)
        result = metrics(labels, labels)
        self.assertEqual(result["intent_accuracy"], 1.0)
        self.assertEqual(result["macro_f1_14_intents"], 1.0)
        self.assertEqual(result["queue_accuracy"], 1.0)

    def test_failed_predictions_stay_in_the_denominator(self):
        labels = list(INTENTS)
        result = metrics([None] * len(labels), labels)
        self.assertEqual(result["intent_accuracy"], 0.0)
        self.assertEqual(result["macro_f1_14_intents"], 0.0)
        self.assertEqual(result["queue_accuracy"], 0.0)

    def test_partial_credit_counts_precision_and_recall(self):
        result = metrics(["freeze", "freeze"], ["freeze", "balance"])
        self.assertEqual(result["intent_accuracy"], 0.5)
        # freeze: tp=1, fp=1, fn=0 -> 2/3; balance: fn=1 -> 0; the other 12 labels score 0.
        self.assertAlmostEqual(result["macro_f1_14_intents"], (2 / 3) / 14)

    def test_queue_accuracy_ignores_sub_intent_differences(self):
        result = metrics(["atm_limit"], ["abroad"])
        self.assertEqual(result["queue_accuracy"], 1.0)
        self.assertEqual(result["intent_accuracy"], 0.0)


class QuestionDefinitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import typesafe_sdk  # noqa: F401
        except ImportError:  # pragma: no cover - depends on the installed environment
            raise unittest.SkipTest("typesafe-sdk is not installed") from None

    def test_questions_cover_intents_and_business_scales(self):
        definition = questions()
        self.assertEqual(
            list(definition),
            ["intent", "urgency", "dissatisfaction", "needs_human", "input_sufficient"],
        )
        self.assertEqual(set(definition["intent"].criteria), {*INTENTS, "other"})
        self.assertEqual(len(definition["urgency"].criteria), 5)
        self.assertEqual(len(definition["dissatisfaction"].criteria), 5)


@unittest.skipUnless(AUDIO_DEPS, "av and faster-whisper are required for audio decoding")
class DecodeAudioTests(unittest.TestCase):
    def test_decodes_mono_audio(self):
        tone = 0.2 * np.sin(2 * np.pi * 440 * np.arange(SAMPLE_RATE // 2) / SAMPLE_RATE)
        decoded = decode_audio(pa.table({"record_id": ["rec-1"], "audio_bytes": [wav_bytes([tone])]}))
        self.assertEqual(decoded.column("error").to_pylist(), [None])
        waveform = decoded.column("waveform").to_pylist()[0]
        self.assertAlmostEqual(len(waveform) / SAMPLE_RATE, 0.5, delta=0.05)

    def test_rejects_stereo_audio(self):
        tone = np.zeros(SAMPLE_RATE // 10)
        decoded = decode_audio(pa.table({"record_id": ["rec-2"], "audio_bytes": [wav_bytes([tone, tone])]}))
        self.assertIn("Expected one mono audio stream", decoded.column("error").to_pylist()[0])

    def test_rejects_empty_audio(self):
        decoded = decode_audio(pa.table({"record_id": ["rec-3"], "audio_bytes": [wav_bytes([np.zeros(0)])]}))
        self.assertIsNotNone(decoded.column("error").to_pylist()[0])


if __name__ == "__main__":
    unittest.main()
