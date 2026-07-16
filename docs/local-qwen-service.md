# Local Qwen2.5-VL Service Setup Guide

[Back to the repository](../README.md) · **English** | [简体中文](local-qwen-service.zh.md)

This guide creates the loopback-only OpenAI-compatible multimodal service shared by the [claims disposition](../claims-disposition/README.md) and [procurement compliance audit](../procurement-compliance-audit/README.md) demos. Both checked-in `runtime.yml` files expect:

- API base URL: `http://127.0.0.1:8001/v1`
- health URL: `http://127.0.0.1:8001/health`
- served model name: `Qwen2.5-VL-3B-Instruct`
- API key: `dummy`

Keep each project environment and the shared model-service environment separate. Install vLLM, PyTorch, and the model download tools into `$HOME/.venvs/qwen2.5-vl-service`, not either project's `.venv`. This avoids dependency conflicts between vLLM/PyTorch/CUDA and Vane/Ray/custom DuckDB.

## 1. Verified environment

The release validation used:

| Item | Verified value |
| --- | --- |
| Operating system | Ubuntu 24.04 x86_64 |
| Python | CPython 3.12 |
| GPU | NVIDIA CUDA GPU, compute capability 7.5 or newer |
| GPU memory | 16 GiB |
| Free disk | At least 25 GiB |
| vLLM | `vllm==0.25.1` |
| Model | `Qwen/Qwen2.5-VL-3B-Instruct` |
| Model revision | `66285546d2b821cf421d4f5eb2576359d3770cd3` |

The 16 GiB figure is the verified baseline for these demos, not a universal minimum for every Qwen/vLLM workload. Windows, macOS, WSL, ARM64, ROCm, Intel XPU, and CPU-only serving are not release-tested here.

## 2. Preflight

Check the NVIDIA driver, Python, free disk, and port:

```bash
nvidia-smi
nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader
python3.12 --version
df -h "$HOME"
ss -ltnp | grep ':8001' || true
```

Expected:

- Python reports `3.12.x`;
- compute capability is at least `7.5`;
- total GPU memory is approximately 16 GiB or more;
- the filesystem containing `$HOME` has at least 25 GiB free;
- port 8001 is not owned by another process.

A separate system CUDA Toolkit is not required. The `--torch-backend=auto` command below selects a compatible PyTorch CUDA wheel for the installed NVIDIA driver, but the driver itself must already work.

## 3. Create the isolated service environment

```bash
python3.12 -m venv "$HOME/.venvs/qwen2.5-vl-service"
source "$HOME/.venvs/qwen2.5-vl-service/bin/activate"
python -m pip install --upgrade pip
python -m pip install uv "huggingface_hub>=0.34,<2"
uv pip install --python "$VIRTUAL_ENV/bin/python" \
  --torch-backend=auto \
  "vllm==0.25.1"
```

Verify the installed runtime:

```bash
python -c 'import torch, vllm; print("vLLM", vllm.__version__); print("Torch", torch.__version__); print("CUDA", torch.version.cuda); print("GPU available", torch.cuda.is_available())'
```

Continue only when this prints `vLLM 0.25.1` and `GPU available True`.

## 4. Download the pinned model snapshot

The weights are approximately 7.5 GB. Pinning the revision prevents upstream `main` changes from silently changing Demo behavior.

```bash
source "$HOME/.venvs/qwen2.5-vl-service/bin/activate"
mkdir -p "$HOME/models/Qwen2.5-VL-3B-Instruct"
hf download Qwen/Qwen2.5-VL-3B-Instruct \
  --revision 66285546d2b821cf421d4f5eb2576359d3770cd3 \
  --local-dir "$HOME/models/Qwen2.5-VL-3B-Instruct"
test -f "$HOME/models/Qwen2.5-VL-3B-Instruct/config.json"
du -sh "$HOME/models/Qwen2.5-VL-3B-Instruct"
```

If Hugging Face requires authentication, run `hf auth login` and repeat the same download command.

## 5. Start the service

Keep this foreground process running in a separate terminal:

```bash
source "$HOME/.venvs/qwen2.5-vl-service/bin/activate"
CUDA_VISIBLE_DEVICES=0 vllm serve "$HOME/models/Qwen2.5-VL-3B-Instruct" \
  --served-model-name Qwen2.5-VL-3B-Instruct \
  --host 127.0.0.1 \
  --port 8001 \
  --api-key dummy \
  --dtype bfloat16 \
  --max-model-len 4096 \
  --gpu-memory-utilization 0.85 \
  --limit-mm-per-prompt '{"image": 1}' \
  --generation-config vllm
```

The first start loads several gigabytes of weights. Wait until the process listens on `127.0.0.1:8001`.

Do not change the host to `0.0.0.0` and expose this demo server directly to the public internet. Some non-`/v1` operational vLLM endpoints are not fully protected by the API key. Public deployment requires a reverse proxy, TLS, and production authentication, which are outside these demos.

## 6. Run all three service checks

Run these commands in a second terminal.

### 6.1 Health

```bash
curl -fsS -o /dev/null -w 'health HTTP %{http_code}\n' \
  http://127.0.0.1:8001/health
```

Expected: `health HTTP 200`.

### 6.2 Served model

```bash
curl -fsS \
  -H 'Authorization: Bearer dummy' \
  http://127.0.0.1:8001/v1/models | python3.12 -m json.tool
```

The returned `data[].id` must contain `Qwen2.5-VL-3B-Instruct`.

### 6.3 Real image request

This self-contained request creates a small red PNG in memory and sends it to `/v1/chat/completions`. It requires only Python 3.12 and can run from any directory:

```bash
python3.12 - <<'PY'
import base64
import json
import struct
import zlib
from urllib.request import Request, urlopen


def png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(kind + data)
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", checksum)
    )


width = height = 64
scanlines = b"".join(
    b"\x00" + b"\xff\x00\x00" * width for _ in range(height)
)
image = (
    b"\x89PNG\r\n\x1a\n"
    + png_chunk(
        b"IHDR",
        struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),
    )
    + png_chunk(b"IDAT", zlib.compress(scanlines))
    + png_chunk(b"IEND", b"")
)
image_url = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
payload = {
    "model": "Qwen2.5-VL-3B-Instruct",
    "messages": [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "What is the dominant color? Return one lowercase English word.",
                },
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ],
    "temperature": 0,
    "max_tokens": 16,
}
request = Request(
    "http://127.0.0.1:8001/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": "Bearer dummy",
        "Content-Type": "application/json",
    },
    method="POST",
)
with urlopen(request, timeout=120) as response:
    result = json.load(response)
content = result["choices"][0]["message"]["content"]
assert content and "red" in content.casefold(), content
print(content)
PY
```

After all checks pass, run the E2E command from the demo you want to validate:

```bash
cd claims-disposition  # or: cd procurement-compliance-audit
python scripts/run_demo.py e2e
```

## 7. Stop and restart

Press `Ctrl+C` in the foreground service terminal to stop it. To restart, activate `$HOME/.venvs/qwen2.5-vl-service` and repeat the command in section 5. You do not need to reinstall packages or download the model again.

## 8. Troubleshooting

| Symptom | Action |
| --- | --- |
| `torch.cuda.is_available()` is `False` | Check `nvidia-smi`; recreate the service environment and let `uv --torch-backend=auto` select the backend |
| `CUDA out of memory` | Stop unrelated GPU processes; keep `--max-model-len 4096`; reduce `--gpu-memory-utilization` if required |
| Port 8001 is occupied | Use `ss -ltnp` to identify the owner, or change both the service command and the affected demo's `runtime.yml` |
| `/health` cannot connect | Confirm vLLM is still running, model loading has completed, and it listens on `127.0.0.1:8001` |
| `/v1/models` has a different ID | Restore `--served-model-name Qwen2.5-VL-3B-Instruct` |
| Model download stopped | Repeat the same pinned `hf download --revision ... --local-dir ...` command |
| A proxy intercepts loopback traffic | Add `localhost,127.0.0.1,::1` to `NO_PROXY`; both project launchers also clear proxy variables for the loopback AI URL |

Before upgrading vLLM, the model revision, served name, or port, update this guide and both `runtime.yml` files, then repeat the real E2E validation. Do not replace pinned versions with floating versions without validation.

## Official references

- [vLLM 0.25.1 NVIDIA GPU installation](https://docs.vllm.ai/en/v0.25.1/getting_started/installation/gpu/)
- [vLLM 0.25.1 supported models](https://docs.vllm.ai/en/v0.25.1/models/supported_models/)
- [vLLM 0.25.1 OpenAI-compatible server](https://docs.vllm.ai/en/v0.25.1/serving/online_serving/openai_compatible_server/)
- [vLLM 0.25.1 security guidance](https://docs.vllm.ai/en/v0.25.1/usage/security/)
- [Qwen2.5-VL-3B-Instruct model repository](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct)
- [Hugging Face `hf download` CLI](https://huggingface.co/docs/huggingface_hub/en/package_reference/cli)
