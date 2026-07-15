# 本地 Qwen2.5-VL 服务搭建指南

本文档为招采审计 Demo 搭建一个只监听本机的 OpenAI-compatible 多模态服务。项目默认连接：

- API base URL：`http://127.0.0.1:8001/v1`
- health URL：`http://127.0.0.1:8001/health`
- 模型名：`Qwen2.5-VL-3B-Instruct`
- API key：`dummy`

项目 Python 环境与模型服务环境必须分开。下面把 vLLM、PyTorch 和模型下载工具安装到 `$HOME/.venvs/procurement-qwen`，不要安装到项目 `.venv`。

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

16 GiB 是本 Demo 的已验证基线，不代表所有 Qwen/vLLM 工作负载的通用最低要求。Windows、macOS、WSL、ARM64、ROCm、Intel XPU 和 CPU-only 没有纳入本项目验收。

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
python3.12 -m venv "$HOME/.venvs/procurement-qwen"
source "$HOME/.venvs/procurement-qwen/bin/activate"
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

必须看到 `vLLM 0.25.1` 和 `GPU available True`。如果为 `False`，先处理 NVIDIA driver/PyTorch backend，不要继续下载和启动服务。

## 4. 下载固定模型快照

模型权重约 7.5 GB。使用固定 revision，避免上游 `main` 更新后结果漂移：

```bash
source "$HOME/.venvs/procurement-qwen/bin/activate"
mkdir -p "$HOME/models/Qwen2.5-VL-3B-Instruct"
hf download Qwen/Qwen2.5-VL-3B-Instruct \
  --revision 66285546d2b821cf421d4f5eb2576359d3770cd3 \
  --local-dir "$HOME/models/Qwen2.5-VL-3B-Instruct"
test -f "$HOME/models/Qwen2.5-VL-3B-Instruct/config.json"
du -sh "$HOME/models/Qwen2.5-VL-3B-Instruct"
```

如果 Hugging Face 仓库要求登录，先运行 `hf auth login`，再重复 `hf download`。

## 5. 启动服务

在一个单独终端中保持进程前台运行：

```bash
source "$HOME/.venvs/procurement-qwen/bin/activate"
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

首次启动需要加载约数 GB 权重。看到服务监听 `127.0.0.1:8001` 后，再执行下一节。

安全提示：不要把 `--host` 改成 `0.0.0.0` 并直接暴露到公网。vLLM 的部分非 `/v1` 运维端点并不全部受 API key 保护；公网部署需要额外的反向代理、TLS 和正式鉴权，不在本 Demo 范围内。

## 6. 三项服务检查

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

OpenAI client 会把下面的多模态请求发送到 `/v1/chat/completions`。

先按项目 [README](../README.md) 创建项目 `.venv`，然后从项目根目录运行：

```bash
source .venv/bin/activate
python - <<'PY'
import base64
from pathlib import Path

from openai import OpenAI

image_path = Path("fixtures/expert-score-anomaly/expert_recommendation.png")
image_url = "data:image/png;base64," + base64.b64encode(
    image_path.read_bytes()
).decode("ascii")
client = OpenAI(
    api_key="dummy",
    base_url="http://127.0.0.1:8001/v1",
    timeout=120.0,
)
response = client.chat.completions.create(
    model="Qwen2.5-VL-3B-Instruct",
    messages=[
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "读取图片中的专家编号，只返回编号。"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }
    ],
    temperature=0,
    max_tokens=64,
)
content = response.choices[0].message.content
assert content and "EXP-001" in content, content
print(content)
PY
```

三项检查全部通过后，回到项目根目录执行：

```bash
python scripts/run_demo.py
```

## 7. 停止和再次启动

前台运行时按 `Ctrl+C` 停止。再次启动时不需要重新安装或下载，重复第 5 节命令即可。

## 8. 常见问题

| 现象 | 处理方式 |
| --- | --- |
| `torch.cuda.is_available()` 为 `False` | 检查 `nvidia-smi`，重新创建服务环境并让 `uv --torch-backend=auto` 选择 backend |
| `CUDA out of memory` | 确认没有其他 GPU 进程；保持 `--max-model-len 4096` 和 `--gpu-memory-utilization 0.85`；必要时降低后者 |
| 8001 端口占用 | 用 `ss -ltnp` 找到占用进程并停止，或同时修改服务命令与项目 `runtime.yml` |
| `/health` 连接失败 | 确认 vLLM 进程仍在运行、权重已加载完成并监听 `127.0.0.1:8001` |
| `/v1/models` 名称不一致 | 确认启动参数含 `--served-model-name Qwen2.5-VL-3B-Instruct` |
| 模型下载中断 | 使用相同 `hf download --revision ... --local-dir ...` 命令继续下载 |
| 本机代理干扰 loopback | 为 `localhost,127.0.0.1,::1` 设置 `NO_PROXY`；项目 launcher 也会清理 loopback 请求的代理变量 |

升级 vLLM、模型 revision、served model name 或端口前，需要同步更新本文档、`runtime.yml`，并重新执行真实 E2E。不要在没有验收的情况下把固定版本改成浮动版本。

## 官方参考

- [vLLM 0.25.1 NVIDIA GPU 安装](https://docs.vllm.ai/en/v0.25.1/getting_started/installation/gpu/)
- [vLLM 0.25.1 支持模型](https://docs.vllm.ai/en/v0.25.1/models/supported_models/)
- [vLLM 0.25.1 OpenAI-compatible server](https://docs.vllm.ai/en/v0.25.1/serving/online_serving/openai_compatible_server/)
- [vLLM 0.25.1 安全指南](https://docs.vllm.ai/en/v0.25.1/usage/security/)
- [Qwen2.5-VL-3B-Instruct 模型仓库](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct)
- [Hugging Face `hf download` CLI](https://huggingface.co/docs/huggingface_hub/en/package_reference/cli)
