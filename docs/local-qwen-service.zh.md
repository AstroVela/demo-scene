# 本地 Qwen2.5-VL 服务搭建指南

[返回仓库首页](../README.md) · [English](local-qwen-service.md) | **简体中文**

本文档为[车辆理赔分流](../claims-disposition/README.zh-CN.md)与[招采合规审计](../procurement-compliance-audit/README.zh-CN.md)两个 Demo 搭建一套共享的、只监听本机的 OpenAI-compatible 多模态服务。两个项目中的 `runtime.yml` 均要求：

- API base URL：`http://127.0.0.1:8001/v1`
- health URL：`http://127.0.0.1:8001/health`
- served model name：`Qwen2.5-VL-3B-Instruct`
- API key：`dummy`

每个项目的环境和共享模型服务环境必须分开。下面把 vLLM、PyTorch 和模型下载工具安装到 `$HOME/.venvs/qwen2.5-vl-service`，不要安装到任一项目的 `.venv`，以免 vLLM/PyTorch/CUDA 与 Vane/Ray/custom DuckDB 的二进制依赖互相污染。

## 1. 已验证环境

发布验收使用以下配置：

| 项目 | 已验证值 |
| --- | --- |
| 操作系统 | Ubuntu 24.04 x86_64 |
| Python | CPython 3.12 |
| GPU | NVIDIA CUDA GPU，compute capability 7.5 或更高 |
| 显存 | 16 GiB |
| 可用磁盘 | 至少 25 GiB |
| vLLM | `vllm==0.25.1` |
| 模型 | `Qwen/Qwen2.5-VL-3B-Instruct` |
| 模型 revision | `66285546d2b821cf421d4f5eb2576359d3770cd3` |

16 GiB 是这两个 Demo 的已验证基线，不代表所有 Qwen/vLLM 工作负载的通用最低要求。Windows、macOS、WSL、ARM64、ROCm、Intel XPU 和 CPU-only 服务没有纳入本项目验收。

## 2. 前置检查

确认 NVIDIA 驱动、Python、磁盘和端口：

```bash
nvidia-smi
nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader
python3.12 --version
df -h "$HOME"
ss -ltnp | grep ':8001' || true
```

预期：

- Python 输出 `3.12.x`；
- GPU compute capability 不低于 `7.5`；
- GPU 总显存至少约 16 GiB；
- `$HOME` 所在文件系统至少有 25 GiB 可用空间；
- 8001 端口没有被其他进程占用。

不要求单独安装系统 CUDA Toolkit。下面的 `--torch-backend=auto` 会按照当前 NVIDIA driver 选择兼容的 PyTorch CUDA wheel，但驱动本身必须可用。

## 3. 创建独立服务环境

```bash
python3.12 -m venv "$HOME/.venvs/qwen2.5-vl-service"
source "$HOME/.venvs/qwen2.5-vl-service/bin/activate"
python -m pip install --upgrade pip
python -m pip install uv "huggingface_hub>=0.34,<2"
uv pip install --python "$VIRTUAL_ENV/bin/python" \
  --torch-backend=auto \
  "vllm==0.25.1"
```

检查实际安装：

```bash
python -c 'import torch, vllm; print("vLLM", vllm.__version__); print("Torch", torch.__version__); print("CUDA", torch.version.cuda); print("GPU available", torch.cuda.is_available())'
```

必须看到 `vLLM 0.25.1` 和 `GPU available True`，否则先处理 NVIDIA driver 或 PyTorch backend，不要继续下载和启动服务。

## 4. 下载固定模型快照

模型权重约 7.5 GB。使用固定 revision，避免上游 `main` 更新后 Demo 行为漂移：

```bash
source "$HOME/.venvs/qwen2.5-vl-service/bin/activate"
mkdir -p "$HOME/models/Qwen2.5-VL-3B-Instruct"
hf download Qwen/Qwen2.5-VL-3B-Instruct \
  --revision 66285546d2b821cf421d4f5eb2576359d3770cd3 \
  --local-dir "$HOME/models/Qwen2.5-VL-3B-Instruct"
test -f "$HOME/models/Qwen2.5-VL-3B-Instruct/config.json"
du -sh "$HOME/models/Qwen2.5-VL-3B-Instruct"
```

如果 Hugging Face 仓库要求登录，先运行 `hf auth login`，再重复相同的下载命令。

## 5. 启动服务

在一个单独终端中保持进程前台运行：

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

首次启动需要加载数 GB 权重。看到服务监听 `127.0.0.1:8001` 后再执行下一节。

不要把 `--host` 改成 `0.0.0.0` 并直接暴露到公网。vLLM 的部分非 `/v1` 运维端点并不全部受 API key 保护；公网部署需要反向代理、TLS 和正式鉴权，不在这两个 Demo 的范围内。

## 6. 完成三项服务检查

以下命令在第二个终端执行。

### 6.1 Health

```bash
curl -fsS -o /dev/null -w 'health HTTP %{http_code}\n' \
  http://127.0.0.1:8001/health
```

预期：`health HTTP 200`。

### 6.2 模型列表

```bash
curl -fsS \
  -H 'Authorization: Bearer dummy' \
  http://127.0.0.1:8001/v1/models | python3.12 -m json.tool
```

返回的 `data[].id` 必须包含 `Qwen2.5-VL-3B-Instruct`。

### 6.3 真实图片请求

下面的自包含请求会在内存中创建一张红色 PNG，并发送到 `/v1/chat/completions`。它只依赖 Python 3.12，可以在任意目录运行：

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

三项检查全部通过后，在需要验证的 Demo 目录执行 E2E：

```bash
cd claims-disposition  # 或：cd procurement-compliance-audit
python scripts/run_demo.py e2e
```

## 7. 停止和再次启动

前台运行时按 `Ctrl+C` 停止。再次启动只需激活 `$HOME/.venvs/qwen2.5-vl-service` 并重复第 5 节命令，不需要重新安装或下载模型。

## 8. 常见问题

| 现象 | 处理方式 |
| --- | --- |
| `torch.cuda.is_available()` 为 `False` | 检查 `nvidia-smi`；重新创建服务环境并让 `uv --torch-backend=auto` 选择 backend |
| `CUDA out of memory` | 停止无关 GPU 进程；保持 `--max-model-len 4096`；必要时降低 `--gpu-memory-utilization` |
| 8001 端口占用 | 用 `ss -ltnp` 找到占用者，或同时修改服务命令与相关 Demo 的 `runtime.yml` |
| `/health` 连接失败 | 确认 vLLM 仍在运行、权重已加载完成并监听 `127.0.0.1:8001` |
| `/v1/models` 名称不一致 | 恢复 `--served-model-name Qwen2.5-VL-3B-Instruct` |
| 模型下载中断 | 重复相同的固定 `hf download --revision ... --local-dir ...` 命令 |
| 本机代理干扰 loopback | 为 `localhost,127.0.0.1,::1` 设置 `NO_PROXY`；两个项目的 launcher 也会清理 loopback AI URL 的代理变量 |

升级 vLLM、模型 revision、served model name 或端口前，需要同步更新本文档和两个项目的 `runtime.yml`，并重新执行真实 E2E。未经验证不要把固定版本改成浮动版本。

## 官方参考

- [vLLM 0.25.1 NVIDIA GPU 安装](https://docs.vllm.ai/en/v0.25.1/getting_started/installation/gpu/)
- [vLLM 0.25.1 支持模型](https://docs.vllm.ai/en/v0.25.1/models/supported_models/)
- [vLLM 0.25.1 OpenAI-compatible server](https://docs.vllm.ai/en/v0.25.1/serving/online_serving/openai_compatible_server/)
- [vLLM 0.25.1 安全指南](https://docs.vllm.ai/en/v0.25.1/usage/security/)
- [Qwen2.5-VL-3B-Instruct 模型仓库](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct)
- [Hugging Face `hf download` CLI](https://huggingface.co/docs/huggingface_hub/en/package_reference/cli)
