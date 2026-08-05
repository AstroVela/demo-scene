# Auditable Customer Service Call Audit with Vane

[简体中文](README.zh-CN.md)

Customer service quality review rarely starts from structured data: raw call recordings sit in object storage, and the metrics a review team needs — problem category, customer sentiment, urgency, follow-up flags — must be extracted from speech. This demo turns those recordings into one Vane Relation pipeline that probes audio quality, transcribes each call with a real faster-whisper ASR engine, extracts audit metrics with a real local Qwen model, applies deterministic DuckDB SQL validation, and atomically publishes one analysis JSON per call plus a batch summary back to MinIO.

The synthetic fixture covers four audit outcomes:

| Call | Expected problem_category | Expected customer_sentiment |
| --- | --- | --- |
| `CALL-REFUND-ANGRY` | `refund_request` | `very_negative` |
| `CALL-BILLING-CALM` | `billing_dispute` | `neutral` |
| `CALL-TECH-FRUSTRATED` | `technical_support` | `negative` |
| `CALL-PRAISE-HAPPY` | `praise` | `very_positive` |

> These are quality-review findings, not official compliance verdicts or HR decisions.

## Why Vane

Vane is a multi-compute engine for multimodal data: it lets object-storage locators, audio probes, SQL, stateless Python UDFs, stateful actors, and AI models work together in one composable and traceable Relation pipeline. Vane also separates pipeline logic from execution backends. The checked-in configuration defaults to the `local` Runner; `runtime.yml` can switch to the `ray` Runner without changing the relation code. Local runs one driver-owned faster-whisper engine and attaches an immutable result lookup; Ray initializes the engine inside an isolated stateful Actor worker.

## Architecture

```text
MinIO recordings (4 synthetic calls)
  -> object existence, SHA-256, and audio probe facts
  -> faster-whisper ASR boundary (stateful actor or driver lookup)
  -> transcript quality gate
  -> Qwen audit analysis (problem category, sentiment, urgency, follow-up)
  -> strict JSON contract validation
  -> deterministic review disposition SQL
  -> per-call analysis JSON + batch summary publication to MinIO
```

The core relation path is:

```text
stg_calls / stg_run_config
  -> int_call_inputs -> int_call_probe_udf -> int_call_facts
  -> int_call_transcript_udf -> int_transcript_facts
  -> int_call_analysis_ai (vane.ai.prompt)
  -> int_analysis_validation_inputs -> int_analysis_validation_udf
  -> int_analysis_facts -> call_audit_report
```

1. Lists the `recordings/` prefix in MinIO as an ordered call manifest; the runtime never reads local fixture files directly.
2. Probes each recording through MinIO UDFs: object existence, SHA-256, and a stdlib-only WAV header check that produces duration, channels, sample rate, and an `audio_usable` gate. Unusable audio is routed to a manual-review disposition and never reaches ASR.
3. Transcribes each usable recording with faster-whisper (`zh`, `beam_size=5`, VAD enabled). Local runs one driver-owned engine and attaches an immutable lookup keyed by `(bucket, object_key)`; Ray attaches the reusable stateful Actor. Both return the same transcript JSON contract to SQL.
4. Applies a deterministic transcript quality gate (`min_text_chars`, ASR status, language confidence) before any transcript is sent to the model.
5. Sends only usable transcripts to Qwen with a hardened audit prompt: the transcript is delimited as untrusted evidence, the response must satisfy a complete JSON Schema, and prompt-injection content inside a transcript is never followed.
6. Validates every untrusted model response through `validate_call_analysis_json` (enum domains, score ranges, required fields), producing either a `success` record or a deterministic `invalid_response` finding with uncertainty reasons.
7. Derives a reviewable `review_disposition` in pure SQL (`audited`, `review_unusable_audio`, `review_low_quality_transcript`, `review_invalid_analysis`), then publishes one analysis JSON per call plus `batch_summary.json` to the `analysis/` prefix, cleaning stale outputs first.

## Run the demo

This demo requires CPython 3.12 and pins the public PyPI release `vane-ai==0.1.0a1`. The release provides CPython 3.10, 3.11, and 3.12 `manylinux_2_28_x86_64` wheels (glibc 2.28 or newer), so run it on x86_64 Linux (or WSL). Follow the [complete runbook](docs/runbook.md) to create the environment, install Vane with `python -m pip install vane-ai`, install the demo with `python -m pip install -r requirements.txt`, and prepare running MinIO and Qwen services. Then run:

```bash
python scripts/run_demo.py e2e
```

`runtime.yml` defaults to `runner: local`. Set it to `runner: ray` and connect a Ray cluster to exercise the distributed Actor and AI Relation path.

A successful run prints:

```text
loaded 4 call recordings
published 4 call analysis files
verified 4 call analyses: CALL-BILLING-CALM=(billing_dispute,neutral), CALL-PRAISE-HAPPY=(praise,very_positive), CALL-REFUND-ANGRY=(refund_request,very_negative), CALL-TECH-FRUSTRATED=(technical_support,negative)
```

There is no AI mock fallback: unavailable services, unusable audio, invalid AI JSON, incompatible runtimes, SQL failures, and publication failures all exit nonzero.

## Implementation layout and where Vane is used

```text
customer-service-audit/
├── pyproject.toml
│   # Declares Python/runtime dependencies, including the exact public-PyPI Vane pin.
│
├── requirements.txt
│   # Installs this source tree and its fast-test extra from pyproject.toml.
│
├── runtime.yml
│   # Configures the Vane Runner (Local by default), MinIO, faster-whisper, and Qwen.
│
├── scripts/
│   ├── run_demo.py
│   │   # Verifies CPython 3.12, exact Vane identities, required APIs, package
│   │   # origins, and loopback networking before invoking the CLI.
│   └── make_fixtures.py
│       # Regenerates the four deterministic call recordings (edge-tts -> 16 kHz
│       # mono PCM WAV); run only when re-authoring assets.
│
├── src/customer_service_audit/
│   ├── cli.py
│   │   # Dispatches the fixture, run, verify, and e2e commands.
│   │
│   ├── config.py
│   │   # Loads and strictly validates runtime.yml into typed runtime settings.
│   │
│   ├── fixture_loader.py
│   │   # Uploads the packaged WAV fixtures to MinIO and refreshes the prefixes.
│   │
│   ├── minio_store.py
│   │   # Wraps MinIO reads, existence checks, SHA-256, uploads, and cleanup.
│   │
│   ├── pipeline.py
│   │   # Owns the driver DuckDB catalog and a separate Runner connection, stages
│   │   # their boundary through temporary Parquet, and orchestrates the DAG.
│   │   └── [Vane] vane.configure selects Local or Ray; Relation.write_parquet
│   │
│   ├── vane_udfs.py
│   │   # [Vane] Stateless @vane.func probes/validators and the stateful
│   │   # @vane.cls-style AsrTranscribeActor (faster-whisper, lazy load).
│   │
│   ├── call_ai.py
│   │   # [Vane] The real AI boundary: vane.ai.load_provider on Local and
│   │   # vane.ai.prompt on Ray, with a hardened audit prompt and JSON Schema.
│   │
│   ├── output_writer.py
│   │   # Validates the output contract and publishes per-call JSON + summary.
│   │
│   ├── verify_outputs.py
│   │   # Re-reads MinIO analysis JSON and asserts the four fixture outcomes.
│   │
│   └── sql/  (staging / intermediate / marts — the complete SQL DAG)
│
└── tests/fast/
    # Fast, dependency-light tests for config, UDF contracts, and output shape.
```

## Fixture provenance

The four packaged recordings in `src/customer_service_audit/assets/` are synthetic Chinese customer service dialogues synthesized with edge-tts and re-encoded to 16 kHz mono PCM WAV; they are deterministic, redistributable demo assets. Expected outcomes are asserted in `fixture_loader.EXPECTED_ANALYSES`. If you regenerate assets, the expected outcomes must be re-derived and re-validated before release.
