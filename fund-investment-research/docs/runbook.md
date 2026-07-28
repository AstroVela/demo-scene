# Fund Research Demo Runbook

## Runtime boundary

This demo accepts only the Ray Runner and requires the image-capable local Vane build under `~/vane`. Always enter through `scripts/run_demo.py`; the launcher checks CPython 3.11, the local wheel origin, exact Vane/DuckDB identities, `image_columns` support, and Ray Runner availability.

## Install

From `fund-investment-research`:

```bash
uv venv --python ~/vane/.venv/bin/python .venv
uv pip install \
  --python .venv/bin/python \
  ~/vane/dist/vane_ai-0.1.0a1-cp311-cp311-linux_x86_64.whl
uv pip install --python .venv/bin/python -e '.[test]'
```

The host also needs `pdftoppm`, ffmpeg, eSpeak/pyttsx3, PostgreSQL, and MinIO.

## Services

The checked-in [`runtime.yml`](../runtime.yml) expects:

| Service | Default |
| --- | --- |
| PostgreSQL | `127.0.0.1:5432` |
| MinIO | `127.0.0.1:9000` |
| Qwen2.5-VL OpenAI-compatible API | `http://127.0.0.1:8001/v1` |
| Whisper OpenAI-compatible API | `http://127.0.0.1:8002/v1` |

Both model `/health` responses must contain `status: ok` and the exact configured model name. The synthetic fixture creates a 16 kHz mono WAV. See the repository-level [Qwen service guide](../../docs/local-qwen-service.md).

With `ray.address` empty, Vane starts a local Ray instance. An external Ray cluster also needs network access to every service and a shared path for Runner staging files.

## Test and run

```bash
.venv/bin/pytest -q tests/fast
.venv/bin/python scripts/run_demo.py e2e
```

The split form is:

```bash
.venv/bin/python scripts/run_demo.py fixture --scenario default
.venv/bin/python scripts/run_demo.py run
.venv/bin/python scripts/run_demo.py verify --scenario default
```

Only `fixture` creates local synthetic seed material. `run` reads its formal inputs exclusively from PostgreSQL and MinIO.

## Glossary drill

```bash
.venv/bin/python scripts/run_demo.py fixture --scenario glossary-before
.venv/bin/python scripts/run_demo.py run
.venv/bin/python scripts/run_demo.py verify --scenario glossary-before

.venv/bin/python scripts/run_demo.py fixture --scenario glossary-after
.venv/bin/python scripts/run_demo.py run
.venv/bin/python scripts/run_demo.py verify --scenario glossary-after
```

The second snapshot adds only the `actin-4 → Nectin-4` PostgreSQL alias. Verification requires a changed correction/knowledge disposition and the same `pipeline_sha256`.

## Recovery drill

```bash
.venv/bin/python scripts/run_demo.py fixture --scenario recovery-fault
.venv/bin/python scripts/run_demo.py run  # expected nonzero

.venv/bin/python scripts/run_demo.py fixture --scenario recovery-fixed
.venv/bin/python scripts/run_demo.py run --resume
.venv/bin/python scripts/run_demo.py verify --scenario recovery-fixed
```

The fault is a genuinely corrupt clinical PDF, not a mocked result. The fixed run must publish with `resume_recomputed_source_ids == ["SRC-CLINICAL"]`.

## Failure behavior

- A source locator, hash, media, or decode failure is quarantined and prevents an incomplete publication.
- OCR/ASR/AI, Ray, storage, SQL, or publication failures exit nonzero.
- AI JSON and role semantics retry once, then fail closed.
- Publication validates cross-references and hashes before atomically moving `current`.
- The most recent successful snapshot remains available after a failed run.

Generated `.venv`, `output`, model weights, real research materials, and production credentials must not be committed.
