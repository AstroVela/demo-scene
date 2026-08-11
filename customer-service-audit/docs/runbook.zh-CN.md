# 客服录音质检操作手册（Runbook）

[返回用例](../README.zh-CN.md) · [English](runbook.md)

本手册给出客服录音质检演示的完整环境、安装、服务、配置、命令与排障契约。

## 已验证环境

| 项目 | 已验证值 |
| --- | --- |
| 操作系统 | Ubuntu 24.04 x86_64，glibc 2.39 |
| Python | CPython 3.12 |
| Vane | `vane-ai[openai]==0.1.0` |
| MinIO | `127.0.0.1:9000` |
| 模型服务 | Qwen（OpenAI 兼容），位于 `127.0.0.1:8001` |

已验证的 Vane wheel 及其依赖均从 PyPI 安装。本演示的启动器仅接受 CPython 3.12，已验证环境为 x86_64 Linux。

安装项目侧的 Ubuntu 工具：

```bash
sudo apt update
sudo apt install -y git curl python3.12 python3.12-venv
```

按照 uv 官方安装说明安装 uv，并确认 `uv --version` 可以正常执行。

MinIO 与 Qwen 服务是外部依赖。启动器只连接它们，不负责安装、启动、停止或重启。

## 从干净检出安装

所有项目命令都在 `customer-service-audit` 目录下运行。

### 1. 创建虚拟环境

```bash
cd customer-service-audit
uv venv --python 3.12 .venv
source .venv/bin/activate
```

### 2. 从 PyPI 安装 Vane

```bash
uv pip install 'vane-ai[openai]==0.1.0'
```

PyPI 提供精确的 Vane wheel 及其依赖。启动器会拒绝其他 Vane build。

### 3. 安装演示

```bash
uv pip install -r requirements.txt
uv pip check
```

`requirements.txt` 以可编辑模式安装本源码树及其测试附加项。`pyproject.toml` 是权威来源：

| 依赖 | 用途 |
| --- | --- |
| `vane-ai[openai]==0.1.0` | Vane API、引擎、AI provider 与 worker |
| `openai==2.45.0` | OpenAI 兼容的 Qwen 客户端 |
| `minio` | 对象读写与 SHA-256 UDF |
| `faster-whisper` | CPU ASR：Local 由 driver 持有，Ray 为有状态 Actor |
| `pyarrow` | Relation/Python 数据边界 |
| `pyyaml` | 严格加载 `runtime.yml` |
| `pytz` | 稳定时间戳 |
| `socksio==1.0.0` | 首次下载 Whisper 模型时提供 SOCKS proxy 支持 |
| `pytest` | 快速测试 |

`uv pip check` 不得报告缺失或不兼容的依赖。

## 准备外部服务

### MinIO

签入的 `runtime.yml` 使用如下本地合成数据契约：

| 服务 | 必需契约 |
| --- | --- |
| MinIO | access/secret 为 `vaneinsight` / `vaneinsight_dev_password`，端点 `127.0.0.1:9000`，使用 HTTP 而非 TLS |

你可以复用已有 MinIO，或按官方文档安装。如果端点或凭据不同，请先更新 `runtime.yml`。签入值是环回演示凭据，不得用于生产。

`fixture` 命令会在缺少桶时创建桶，并刷新 `recordings/` 与 `analysis/` 前缀。它不会创建 MinIO 进程或访问密钥。

用已安装的项目探测 MinIO：

```bash
python - <<'PY'
from customer_service_audit.config import load_runtime_config
from customer_service_audit.pipeline import probe_runtime

probe_runtime(load_runtime_config())
print("MinIO: OK")
PY
```

### Qwen 模型服务

`runtime.yml` 期望一个 OpenAI 兼容端点：

| 键 | 签入值 |
| --- | --- |
| `ai.base_url` | `http://127.0.0.1:8001/v1` |
| `ai.health_url` | `http://127.0.0.1:8001/health` |
| `ai.model` | `Qwen2.5-VL-3B-Instruct` |
| `ai.api_key` | `dummy`（环回服务通常忽略它） |

在任何 AI 调用被调度之前，健康检查 URL 必须返回 HTTP 200。如果你的服务暴露了不同的健康路由或运行在其他主机上，请更新 `runtime.yml`——`config.py` 会校验两个 URL 都是环回 `http://` 地址。若要从这个仅限环回的配置访问远程模型服务，请运行一个转发到远程端点的本地反向代理，并把 `ai.base_url` / `ai.health_url` 指向该代理。

## 命令

```bash
python scripts/run_demo.py fixture   # 把打包的 WAV fixture 上传到 MinIO
python scripts/run_demo.py run       # 执行完整流水线，发布 JSON
python scripts/run_demo.py verify    # 断言四个 fixture 结果
python scripts/run_demo.py e2e       # 依次执行 fixture + run + verify
```

任何命令在任何失败时都以非零码退出，成功时打印一行摘要。

## Runner 模式

签入的 `runtime.yml` 默认使用 `runner: ray`。该路径已在本地 Ray runtime 上结合 MinIO、真实 faster-whisper ASR 引擎和本地 Qwen 服务完成端到端验证。

`runner: local`
: 一个由 driver 持有的 faster-whisper 引擎；每个可用的 `(bucket, object_key)` 定位符在 driver 上转写一次，不可变结果作为查找 UDF 挂载到 SQL。仅在明确需要 Driver 执行时使用这条受支持的回退路径。

如果缓存中没有配置的 faster-whisper 模型，首次运行会从 Hugging Face 下载。Launcher 会保留远程 proxy 变量，仅通过 `NO_PROXY`/`no_proxy` 绕过 loopback Qwen。

`runner: ray`
: `AsrTranscribeActor` 作为有状态函数挂载；whisper 模型在每个 Ray worker 上懒加载一次。需要一个通过 Vane `VANE_*` 环境变量配置的可达 Ray 集群。

Pipeline 会在本地 Ray runtime 启动前设置 `OPENAI_API_KEY`。连接已有或外部 Ray 集群时，必须在启动 Demo 前通过集群的 runtime 或 secret management 为每个 worker 配置 `OPENAI_API_KEY`；修改 Driver 环境无法更新已经存在的 worker。真实多节点目标集群仍需针对共享路径、worker 凭据和资源容量执行基础设施 smoke test。

## 排障

| 症状 | 原因 / 契约 |
| --- | --- |
| `Python version mismatch` | 请用 CPython 3.12 运行；启动器拒绝其他版本。 |
| `cannot import vane` | `vane-ai` 缺失，或安装在活动 venv 之外。 |
| Whisper 模型下载或 SOCKS proxy 失败 | 确认可以访问 Hugging Face，并重新执行安装步骤 3，确保已安装 `socksio`。 |
| `MinIO (127.0.0.1:9000): ...` | MinIO 未运行、凭据不同，或桶契约已变更。 |
| `Qwen health probe ...` | `ai.health_url` 不可达，或返回非 200。 |
| `review_unusable_audio` | WAV 头无效，或时长超出 [1 秒, 900 秒]。 |
| `review_low_quality_transcript` | 转写短于 `asr.min_text_chars`，或 ASR 未成功。 |
| `review_invalid_analysis` | 模型响应违反 JSON 契约；`uncertainty_reasons` 指明字段。 |
| fixture 校验不一致 | fixture 结果依赖模型；在重新设定预期之前先确认转写质量。 |
