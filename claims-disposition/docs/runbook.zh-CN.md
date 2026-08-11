# 理赔分流运行手册

[返回 Use Case](../README.zh-CN.md) · [English](runbook.md)

本手册记录理赔 Demo 的精确环境、安装、外部服务、配置、命令和排错合同。

## 已验证环境

| 项目 | 已验证值 |
| --- | --- |
| 操作系统 | Ubuntu 24.04 x86_64，glibc 2.39 |
| Python | CPython 3.12 |
| Vane | `vane-ai[openai]==0.1.0` |
| PostgreSQL | `127.0.0.1:5432` |
| MinIO | `127.0.0.1:9000` |
| 模型服务 | NVIDIA CUDA GPU 上的 Qwen2.5-VL-3B，监听 `127.0.0.1:8001` |

已验证的 Vane wheel 及其依赖均从 PyPI 安装。本 Demo 在上表所示的 CPython 3.12 x86_64 Linux 环境完成安装与验证；源码构建和其他 CPU 架构未验证。

安装项目侧 Ubuntu 工具：

```bash
sudo apt update
sudo apt install -y git curl python3.12 python3.12-venv
```

按照 uv 官方安装说明安装 uv，并确认 `uv --version` 可以正常执行。

PostgreSQL、MinIO 和 Qwen 是外部服务。Launcher 只连接它们，不会安装、启动、停止或重启这些服务。

## 从全新代码副本安装

所有项目命令都在 `claims-disposition` 目录执行。

### 1. 创建虚拟环境

```bash
cd claims-disposition
uv venv --python 3.12 .venv
source .venv/bin/activate
```

虚拟环境可以使用其他目录名。Launcher 校验当前解释器和包来源，不要求目录必须叫 `.venv`。

### 2. 从 PyPI 安装 Vane

```bash
uv pip install 'vane-ai[openai]==0.1.0'
```

PyPI 提供精确的 Vane wheel 及其依赖。Launcher 的运行时标识校验会拒绝其他 Vane build。

### 3. 安装 Demo

```bash
uv pip install -r requirements.txt
uv pip check
```

`requirements.txt` 会以 editable 方式安装源码和 test extra，依赖以 `pyproject.toml` 为准：

| 依赖 | 用途 |
| --- | --- |
| `vane-ai[openai]==0.1.0` | Vane API、引擎、AI provider 和 worker |
| `openai==2.45.0` | OpenAI-compatible Qwen client |
| `socksio==1.0.0` | OpenAI HTTP client 使用的 SOCKS 代理支持 |
| `minio` | 对象读写和 SHA-256 UDF |
| `psycopg[binary]` | PostgreSQL 输入、Fixture 和原子发布 |
| `rapidocr`、`onnxruntime` | CPU OCR：Local 由 Driver 持有，Ray 使用有状态 Actor |
| `numpy`、`pillow` | Fixture 图片和图片质量计算 |
| `pyarrow` | Relation/Python 数据边界 |
| `pyyaml` | 严格读取 `runtime.yml` |
| `pytz` | 稳定生成 Fixture 时间 |
| `pytest` | Fast tests |

`uv pip check` 不得报告缺失或不兼容的依赖。

## 准备外部服务

### PostgreSQL 和 MinIO

仓库中的 `runtime.yml` 使用以下本机合成数据合同：

| 服务 | 必需合同 |
| --- | --- |
| PostgreSQL | database `vane_insight`，用户/密码 `vane_insight` / `vane_insight_dev_password`，`127.0.0.1:5432` |
| MinIO | access/secret `vaneinsight` / `vaneinsight_dev_password`，endpoint `127.0.0.1:9000`，使用 HTTP |

可以复用现有服务，也可以按照 PostgreSQL 和 MinIO 官方文档安装。如果 endpoint 或凭据不同，先修改 `runtime.yml`。仓库默认值只适用于 loopback Demo，不能用于生产。

`fixture` 命令会创建所需 PostgreSQL schema/table 和 MinIO bucket，并刷新合成快照；它不会创建 server、PostgreSQL database/role、MinIO 进程或 access key。

使用已经安装的项目代码探测两个服务：

```bash
python - <<'PY'
from claims_disposition_sql_pipeline.config import load_runtime_config
from claims_disposition_sql_pipeline.pipeline import probe_runtime

probe_runtime(load_runtime_config())
print("PostgreSQL and MinIO: OK")
PY
```

### 本地 Qwen 服务

模型服务需要使用独立环境，完整步骤见[本地 Qwen2.5-VL 服务搭建指南](../../docs/local-qwen-service.zh.md)。项目要求：

```bash
curl -fsS -o /dev/null -w 'health HTTP %{http_code}\n' \
  http://127.0.0.1:8001/health
curl -fsS -H 'Authorization: Bearer dummy' \
  http://127.0.0.1:8001/v1/models | python -m json.tool
```

预期 health 为 HTTP 200，`data[].id` 包含 `Qwen2.5-VL-3B-Instruct`。独立指南还会验证一次真实图片 `/v1/chat/completions` 请求。

## 运行与验证

Launcher 提供四个命令：

```bash
python scripts/run_demo.py fixture
python scripts/run_demo.py run
python scripts/run_demo.py verify
python scripts/run_demo.py e2e
```

- `fixture` 刷新 4 条理赔、4 张 JPEG 照片和 4 份动态生成的 PNG 文档。
- `run` 探测服务，执行真实 OCR/Qwen 推理和 SQL DAG，校验输出并原子替换 PostgreSQL 快照。
- `verify` 直接读取 PostgreSQL，并要求四条 fixture 结果完全匹配。
- `e2e` 顺序执行 `fixture -> run -> verify`，任一步返回非零就停止。

首次运行：

```bash
python scripts/run_demo.py e2e
```

预期输出：

```text
loaded 4 claims and 8 objects
published 4 claim dispositions
verified 4 claim dispositions: CLM-APPROVE=approve_for_payment, CLM-DENY=deny_claim, CLM-MISSING=request_more_materials, CLM-REVIEW=manual_review
```

不启动 Qwen、也不修改 PostgreSQL/MinIO 数据即可运行 Launcher 和安装合同测试：

```bash
python -m pytest tests/fast -q
```

项目没有 AI mock fallback。服务不可用、图片不可读、AI JSON 不合规、运行时不兼容、SQL 失败或发布失败都会明确返回非零。

## Runtime 配置

`runtime.yml` 是唯一 Runtime 配置来源；应用不会从 Docker 或环境变量自动发现服务。

| 配置项 | 默认值 |
| --- | --- |
| Runner | `ray` |
| PostgreSQL DSN | `postgresql://vane_insight:***@127.0.0.1:5432/vane_insight` |
| 原始 Relation | `claims_disposition_raw.claims` |
| 输出 Relation | `claims_disposition_output.claim_disposition` |
| MinIO | `127.0.0.1:9000`，bucket `claims-disposition-fixtures` |
| OCR | RapidOCR CPU；必需字段 `claim_number`、`claimant_name`、`loss_date`；最低平均置信度 `0.70` |
| AI | OpenAI provider；`http://127.0.0.1:8001/v1`；模型 `Qwen2.5-VL-3B-Instruct`；并发 `1`；超时 `120` 秒 |

仓库默认的 `runner: ray` 路径已在本地 Ray runtime 上使用 `vane-ai[openai]==0.1.0`、PostgreSQL、MinIO、真实 RapidOCR 和本地 Qwen 服务完成端到端验证。真实多节点目标集群仍需针对共享路径、worker 凭据和资源容量执行基础设施 smoke test。仅在明确需要 Driver 持有的受支持回退路径时设置 `runner: local`。

Local 模式下，Pipeline 在 Driver 上创建一份 `DocumentOcrActor` 实现，对每个合格证明文档 locator 执行一次，再将不可变结果挂载为 `document_ocr_json(bucket, object_key)`。模型则通过 Vane 公共 provider API 实例化，并在 Driver 上复用一个异步 client。这样原生 ONNX session 与异步 provider client 不会跨越 LocalRunner 的 subprocess 边界。

Ray 模式下，`DocumentOcrActor` 挂载为有状态 `document_ocr_json(bucket, object_key)` 表达式，Qwen 通过 `vane.ai.prompt` 执行。OCR 引擎在隔离的 Actor worker 内延迟初始化。Launcher 还会在操作者没有显式设置时使用 `VANE_UDF_UNREGISTER_TIMEOUT_MS=60000`，为 Ray 原生 OCR worker 留出足够的清理时间。

Pipeline 会在本地 Ray runtime 启动前设置 `OPENAI_API_KEY`。连接已有或外部 Ray 集群时，必须在启动 Demo 前通过集群的 runtime 或 secret management 为每个 worker 配置 `OPENAI_API_KEY`；修改 Driver 环境无法更新已经存在的 worker。

两种模式下，`int_claim_document_ocr_udf.sql` 都对每份合格文档调用相同表达式，每个 `*_udf.sql` 文件也仍是直接交给 Runner 的投影；后续纯 SQL 解析、关联、分类或聚合相同的物化合同。Driver 输入会临时落为 Parquet，结果注册回 Driver 的 DuckDB catalog。切换 Runner 只改变执行位置，不改变 SQL 与输出合同。

加载器会校验 YAML 结构、SQL identifier、loopback URL、必需值和数值范围。诊断不会打印完整 PostgreSQL DSN、MinIO secret 或 AI key。当 AI URL 是 loopback 地址时，Launcher 会补充 `NO_PROXY`/`no_proxy`，并保留远程依赖所需的 proxy 配置。

## 数据合同

PostgreSQL 原始 Grain 是每个 claim 一行：

```sql
create schema if not exists claims_disposition_raw;

create table if not exists claims_disposition_raw.claims (
  claim_id text primary key,
  scenario text not null,
  description text not null,
  submitted_at timestamptz not null,
  is_test_claim boolean not null,
  materials_json jsonb not null
);
```

`materials_json` 是按顺序保存的 MinIO locator 数组，只支持 `damage_photo + image/jpeg` 和 `supporting_document + image/png`。

最终输出 Grain 也是每个 claim 一行：

```sql
create schema if not exists claims_disposition_output;

create table if not exists claims_disposition_output.claim_disposition (
  claim_id text primary key,
  disposition text not null,
  disposition_confidence numeric(4, 2) not null,
  primary_reason_code text not null,
  reason_summary text not null,
  next_action text not null,
  supporting_facts_json jsonb not null,
  created_by text not null,
  decided_at timestamptz not null
);
```

Writer 会在打开发布事务前校验九列、类型、枚举、置信度和时间戳。发布时在一个 PostgreSQL 事务中删除旧快照并写入新结果，失败会回滚，不会留下部分数据。

## 排错

| 现象 | 处理方式 |
| --- | --- |
| `No matching distribution found for vane-ai` | 确认使用 CPython 3.12 x86_64 Linux，然后重新执行安装步骤 2 中完整的 PyPI uv 命令 |
| Python/Vane/引擎版本不匹配 | 重新激活项目环境并执行两步 uv 安装；Launcher 会输出解释器、prefix、expected/actual 和精确安装命令 |
| Ray 无法分配内存或满足 query demand | 停止遗留 Ray 进程、释放宿主机内存，或连接具备足够 CPU、heap 和 object store 的目标 Ray 集群 |
| Vane 指向当前环境之外 | 清除继承的 `PYTHONPATH`，激活 `.venv`，重新执行两步安装 |
| 缺少直接依赖 | 重新执行安装步骤 3，再运行 `uv pip check` |
| PostgreSQL 连接或鉴权失败 | 检查 endpoint、database、role、密码，以及 schema/table 创建和写入权限 |
| MinIO 连接或鉴权失败 | 检查 endpoint、凭据、HTTP/TLS 模式，以及 bucket create/list/read/write/delete 权限 |
| Qwen health、模型名或图片请求失败 | 按照[本地 Qwen 指南](../../docs/local-qwen-service.zh.md)检查 driver、OOM、端口、served name、模型下载和代理 |
| `fixture load failed` | 检查 PostgreSQL 和 MinIO 权限；重跑 `fixture` 只会刷新合成快照 |
| `verification failed` | 查看命令报告的缺失、额外、重复或不匹配理赔，修复服务/Runtime 后重跑 `e2e` |

## 精确运行时标识

| 组件 | 必需标识 |
| --- | --- |
| Vane distribution metadata（`vane-ai`） | `0.1.0` |
| `vane.__version__` | `0.1.0` |
| Vane engine | `v1.5.0-vane.b1c745e9c4` |
| Vane source revision | `0c2adbf409` |
| OpenAI Python client | `2.45.0` |

Launcher 同时要求 `vane.func`、`vane.cls`、`vane.attach_function`、`vane.configure`、`vane.ai.load_provider`、`vane.ai.prompt` 和 `vane.ray_cxx`。任一不匹配都会明确失败，不会静默使用不兼容的运行时。

## 数据、凭据和隐私

- 不要提交真实理赔记录、客户照片、私人文档、生产凭据、模型权重或运行生成数据。
- `runtime.yml` 中的值只用于 loopback 合成 Demo。
- 内置车辆图片的许可信息位于相邻 NOTICE 文件。
- 运行时生成的理赔文档只包含合成 fixture 数据。
