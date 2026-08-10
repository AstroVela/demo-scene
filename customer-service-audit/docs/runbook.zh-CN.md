# 客服录音质检操作手册（Runbook）

[返回用例](../README.zh-CN.md) · [English](runbook.md)

本手册给出客服录音质检演示的完整环境、安装、服务、配置、命令与排障契约。

## 已验证环境

| 项目 | 已验证值 |
| --- | --- |
| 操作系统 | Ubuntu 24.04 x86_64，glibc 2.39 |
| Python | CPython 3.12 |
| Vane | `vane-ai==0.1.0a1` |
| MinIO | `127.0.0.1:9000` |
| 模型服务 | Qwen（OpenAI 兼容），位于 `127.0.0.1:8001` |

经验证的 Vane 发行版已发布到公开 PyPI，提供 CPython 3.10、3.11、3.12 的 x86_64 Linux wheel，均标记 `manylinux_2_28`（glibc 2.28 及以上）。本演示的启动器仅接受 CPython 3.12。由于没有对应的原生 Vane wheel，不支持 Windows 和 macOS。

安装项目侧的 Ubuntu 工具：

```bash
sudo apt update
sudo apt install -y git curl python3.12 python3.12-venv
```

MinIO 与 Qwen 服务是外部依赖。启动器只连接它们，不负责安装、启动、停止或重启。

## 从干净检出安装

所有项目命令都在 `customer-service-audit` 目录下运行。

### 1. 创建虚拟环境

```bash
cd customer-service-audit
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### 2. 从公开 PyPI 安装 Vane

```bash
python -m pip install vane-ai
```

`pyproject.toml` 固定了已验证的 `vane-ai==0.1.0a1` 运行时，启动器会拒绝未经验证的 Vane 构建。

### 3. 安装演示

```bash
python -m pip install -r requirements.txt
python -m pip check
```

`requirements.txt` 以可编辑模式安装本源码树及其测试附加项。`pyproject.toml` 是权威来源：

| 依赖 | 用途 |
| --- | --- |
| `vane-ai==0.1.0a1` | Vane API、定制 DuckDB 与 worker |
| `openai==2.45.0` | OpenAI 兼容的 Qwen 客户端 |
| `minio` | 对象读写与 SHA-256 UDF |
| `faster-whisper` | CPU ASR：Local 由 driver 持有，Ray 为有状态 Actor |
| `pyarrow` | Relation/Python 数据边界 |
| `pyyaml` | 严格加载 `runtime.yml` |
| `pytz` | 稳定时间戳 |
| `pytest` | 快速测试 |

`pip check` 必须以 `No broken requirements found` 结束。

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

`runner: local`
: 一个由 driver 持有的 faster-whisper 引擎；每个可用的 `(bucket, object_key)` 定位符在 driver 上转写一次，不可变结果作为查找 UDF 挂载到 SQL。适合首次验证。

`runner: ray`
: `AsrTranscribeActor` 作为有状态函数挂载；whisper 模型在每个 Ray worker 上懒加载一次。需要一个通过 Vane `VANE_*` 环境变量配置的可达 Ray 集群。

## 排障

| 症状 | 原因 / 契约 |
| --- | --- |
| `Python version mismatch` | 请用 CPython 3.12 运行；启动器拒绝其他版本。 |
| `cannot import duckdb and vane` | `vane-ai` 缺失，或安装在活动 venv 之外。 |
| `MinIO (127.0.0.1:9000): ...` | MinIO 未运行、凭据不同，或桶契约已变更。 |
| `Qwen health probe ...` | `ai.health_url` 不可达，或返回非 200。 |
| `review_unusable_audio` | WAV 头无效，或时长超出 [1 秒, 900 秒]。 |
| `review_low_quality_transcript` | 转写短于 `asr.min_text_chars`，或 ASR 未成功。 |
| `review_invalid_analysis` | 模型响应违反 JSON 契约；`uncertainty_reasons` 指明字段。 |
| fixture 校验不一致 | fixture 结果依赖模型；在重新设定预期之前先确认转写质量。 |
