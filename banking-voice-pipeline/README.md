# Banking Voice Analysis with Vane and Jev

[English](README.md) | [简体中文](README.zh-CN.md)

Turn short Chinese banking recordings into reviewable business fields: CPUs decode audio, Whisper transcribes on a GPU, native `Relation.jev()` produces structured judgments, and deterministic SQL maps them to processing queues. A keyword baseline and the Jev judgment share the same transcripts, and only final results are written.

## Vane highlights

- Combines CPU tasks, GPU actors, and Jev actors on one `Relation` chain; the execution layer owns batching, retries, and data movement.
- `Relation.jev()` owns request organization, concurrency, and row/answer alignment. `state()` returns `NULL` for failed or suspicious transcripts, so those rows never reach the external service.
- Only `results.parquet` and `review.csv` are written. CSV export and evaluation read the saved result and never rerun Whisper or Jev.

## Quick start

The demo targets Python 3.12, uv, and one CUDA GPU. Jev support currently ships only in TestPyPI dev builds of `vane-ai`, so the pinned Vane build and its `typesafe` SDK are installed separately:

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install --index-strategy unsafe-best-match \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  'vane-ai[typesafe]==0.3.0.dev8' 'typesafe-sdk==0.7.0'
uv pip install -r requirements.txt
uv pip check

export TYPESAFE_API_KEY="your-api-key"
.venv/bin/python src/banking_voice_pipeline.py \
  --output-dir output/banking_voice_pipeline \
  --limit 112
```

The output directory must be new. The run downloads the pinned MInDS-14 Chinese subset and `faster-whisper-small`, then prints keyword-baseline and Jev intent metrics. CUDA requires cuBLAS 12 and cuDNN 9 on `LD_LIBRARY_PATH`; see the [faster-whisper GPU notes](https://github.com/SYSTRAN/faster-whisper#gpu).

> The demo ships no measured results. The fixed 112-recording subset has been used during development, and overlap with upstream model training data cannot be ruled out, so it is not an independent test set. Every judgment requires human review.

## Output

| File | Purpose |
| --- | --- |
| `results.parquet` | Final results: transcript segments, keyword baseline, raw Jev responses, and business fields |
| `review.csv` | Review table without `response` and `segments`, exported from the saved final result |

| Field | Business meaning |
| --- | --- |
| `intent`, `queue` | Customer request and suggested processing queue |
| `urgency`, `dissatisfaction` | Urgency of unresolved matters and dissatisfaction in the text (1–5) |
| `needs_human` | Whether anything still requires staff handling, verification, or a reply |
| `review_required`, `review_reason` | Whether a human must review the result, and why |

## Tests

Offline unit tests cover the keyword baseline, transcript quality checks, queue mapping, evaluation metrics, question definitions, and CPU audio decoding. No network, GPU, or API key is required:

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## Repository layout

```text
banking-voice-pipeline/
├── src/banking_voice_pipeline.py   # decode, ASR, Jev, SQL mapping, and evaluation in one file
├── tests/                          # offline unit tests
├── requirements.txt                # ASR, data, and Jev SDK dependencies
└── output/                         # written at runtime, ignored by Git
```

## Data and boundaries

- Data is the Chinese subset of [PolyAI MInDS-14](https://huggingface.co/datasets/PolyAI/minds14) (CC-BY-4.0): 502 short recordings, of which the demo selects 112 by path hash. It is never committed to the repository.
- Each recording is one short customer utterance, not a complete multi-turn call; no speaker separation is implemented.
- The `0.5` threshold for `needs_human` is uncalibrated, and fields other than intent have no ground truth labels.
- The pipeline outputs fields only and never triggers business operations; every result carries `review_required`.
