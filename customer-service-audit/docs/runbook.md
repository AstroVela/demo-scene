# Customer Service Audit Operational Runbook

[Back to the use case](../README.md) · [简体中文](runbook.zh-CN.md)

This runbook contains the exact environment, installation, service, configuration, command, and troubleshooting contracts for the customer service audit demo.

## Verified environment

| Item | Verified value |
| --- | --- |
| Operating system | Ubuntu 24.04 x86_64, glibc 2.39 |
| Python | CPython 3.12 |
| Vane | `vane-ai[openai]==0.1.0` |
| MinIO | `127.0.0.1:9000` |
| Model service | Qwen (OpenAI-compatible) at `127.0.0.1:8001` |

The verified Vane wheel and its dependencies are installed from PyPI. This demo's launcher accepts only CPython 3.12, and the validated environment is x86_64 Linux.

Install the project-side Ubuntu tools:

```bash
sudo apt update
sudo apt install -y git curl python3.12 python3.12-venv
```

Install uv using its official installation instructions and confirm that `uv --version` succeeds before continuing.

MinIO and the Qwen service are external. The launcher connects to them but does not install, start, stop, or restart them.

## Install from a clean checkout

Run all project commands from the `customer-service-audit` directory.

### 1. Create the virtual environment

```bash
cd customer-service-audit
uv venv --python 3.12 .venv
source .venv/bin/activate
```

### 2. Install Vane from PyPI

```bash
uv pip install 'vane-ai[openai]==0.1.0'
```

PyPI supplies the exact Vane wheel and its dependencies. The launcher rejects any other Vane build.

### 3. Install the demo

```bash
uv pip install -r requirements.txt
uv pip check
```

`requirements.txt` installs this source tree in editable mode with its test extra. `pyproject.toml` is authoritative:

| Dependency | Purpose |
| --- | --- |
| `vane-ai[openai]==0.1.0` | Vane APIs, engine, AI provider, and workers |
| `openai==2.45.0` | OpenAI-compatible Qwen client |
| `minio` | Object reads/writes and SHA-256 UDFs |
| `faster-whisper` | CPU ASR: driver-owned on Local, stateful Actor on Ray |
| `pyarrow` | Relation/Python data boundary |
| `pyyaml` | Strict `runtime.yml` loading |
| `pytz` | Stable timestamps |
| `socksio==1.0.0` | SOCKS proxy support for the first-run Whisper model download |
| `pytest` | Fast tests |

`uv pip check` must finish without reporting an incompatible or missing dependency.

## Prepare the external services

### MinIO

The checked-in `runtime.yml` uses this local synthetic-data contract:

| Service | Required contract |
| --- | --- |
| MinIO | access/secret `vaneinsight` / `vaneinsight_dev_password`, endpoint `127.0.0.1:9000`, HTTP rather than TLS |

You may reuse an existing MinIO or install it from the official documentation. Update `runtime.yml` first if the endpoint or credentials differ. The checked-in values are loopback demo credentials and must not be used in production.

The `fixture` command creates the bucket if missing and refreshes the `recordings/` and `analysis/` prefixes. It does not create the MinIO process or access key.

Probe MinIO with the installed project:

```bash
python - <<'PY'
from customer_service_audit.config import load_runtime_config
from customer_service_audit.pipeline import probe_runtime

probe_runtime(load_runtime_config())
print("MinIO: OK")
PY
```

### Qwen model service

`runtime.yml` expects an OpenAI-compatible endpoint:

| Key | Checked-in value |
| --- | --- |
| `ai.base_url` | `http://127.0.0.1:8001/v1` |
| `ai.health_url` | `http://127.0.0.1:8001/health` |
| `ai.model` | `Qwen2.5-VL-3B-Instruct` |
| `ai.api_key` | `dummy` (loopback services commonly ignore it) |

The health URL must answer HTTP 200 before any AI call is scheduled. If your service exposes a different health route or runs on another host, update `runtime.yml` — `config.py` validates that both URLs are loopback `http://` URLs. To reach a remote model service from the loopback-only configuration, run a local reverse proxy that forwards to the remote endpoint and point `ai.base_url` / `ai.health_url` at the proxy.

## Commands

```bash
python scripts/run_demo.py fixture   # upload packaged WAV fixtures to MinIO
python scripts/run_demo.py run       # execute the full pipeline, publish JSON
python scripts/run_demo.py verify    # assert the four fixture outcomes
python scripts/run_demo.py e2e       # fixture + run + verify in order
```

Each command exits nonzero on any failure and prints a single-line summary on success.

## Runner modes

The checked-in `runtime.yml` defaults to `runner: ray`. This path was verified end to end on a local Ray runtime with MinIO, the real faster-whisper ASR engine, and the local Qwen service.

`runner: local`
: One driver-owned faster-whisper engine; every usable `(bucket, object_key)` locator is transcribed once on the driver and the immutable result is attached to SQL as a lookup UDF. Use this supported fallback only when driver-owned execution is intentional.

The first run downloads the configured faster-whisper model from Hugging Face when it is not already cached. The launcher preserves remote proxy variables and bypasses them only for the loopback Qwen endpoint through `NO_PROXY`/`no_proxy`.

`runner: ray`
: The `AsrTranscribeActor` is attached as a stateful function; the whisper model loads lazily once per Ray worker. Requires a reachable Ray cluster configured through Vane's `VANE_*` environment variables.

The pipeline sets `OPENAI_API_KEY` before a local Ray runtime can start. When connecting to an existing or external Ray cluster, provision `OPENAI_API_KEY` on every worker through that cluster's runtime or secret management before launching the demo; changing the driver environment cannot update workers that already exist. A multi-node target cluster still requires an infrastructure smoke test for shared paths, worker credentials, and capacity.

## Troubleshooting

| Symptom | Cause / contract |
| --- | --- |
| `Python version mismatch` | Run under CPython 3.12; the launcher rejects other versions. |
| `cannot import vane` | `vane-ai` is missing or installed outside the active venv. |
| Whisper model download or SOCKS proxy failure | Confirm outbound Hugging Face access and reinstall step 3 so `socksio` is present. |
| `MinIO (127.0.0.1:9000): ...` | MinIO is down, credentials differ, or the bucket contract changed. |
| `Qwen health probe ...` | `ai.health_url` is unreachable or returns non-200. |
| `review_unusable_audio` | WAV header invalid, or duration outside [1s, 900s]. |
| `review_low_quality_transcript` | Transcript shorter than `asr.min_text_chars` or ASR unsuccessful. |
| `review_invalid_analysis` | Model response violated the JSON contract; `uncertainty_reasons` names the field. |
| Fixture verification mismatch | The fixture outcomes are model-dependent; confirm the transcript quality before re-authoring expectations. |
