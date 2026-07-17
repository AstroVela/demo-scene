# 理赔分流运行手册

[返回 Use Case](../README.zh-CN.md) · [English](runbook.md)

本手册记录理赔 Demo 的精确环境、安装、外部服务、配置、命令和排错合同。

## 已验证环境

| 项目 | 已验证值 |
| --- | --- |
| 操作系统 | Ubuntu 24.04 x86_64，glibc 2.39 |
| Python | CPython 3.12 |
| Vane | `vane-ai==0.1.0a1` |
| PostgreSQL | `127.0.0.1:5432` |
| MinIO | `127.0.0.1:9000` |
| 模型服务 | NVIDIA CUDA GPU 上的 Qwen2.5-VL-3B，监听 `127.0.0.1:8001` |

TestPyPI 上的 Vane wheel 面向 CPython 3.12、Linux x86_64 和 `manylinux_2_39` 构建，因此不承诺支持更旧 glibc、其他 Python 次版本或其他 CPU 架构。

安装项目侧 Ubuntu 工具：

```bash
sudo apt update
sudo apt install -y git curl python3.12 python3.12-venv
```

PostgreSQL、MinIO 和 Qwen 是外部服务。Launcher 只连接它们，不会安装、启动、停止或重启这些服务。

## 从全新代码副本安装

所有项目命令都在 `claims-disposition` 目录执行。

### 1. 创建虚拟环境

```bash
cd claims-disposition
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

虚拟环境可以使用其他目录名。Launcher 校验当前解释器和包来源，不要求目录必须叫 `.venv`。

### 2. 安装已验证的 Vane wheel

```bash
python -m pip install -i https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  vane-ai==0.1.0a1
```

两个 index 都要保留：Vane 来自 TestPyPI，普通依赖从 PyPI 解析。固定版本也要保留，因为 Launcher 会拒绝未经本 Demo 验证的 Vane 和 custom DuckDB build。

### 3. 安装 Demo

```bash
python -m pip install -r requirements.txt
python -m pip check
```

`requirements.txt` 会以 editable 方式安装源码和 test extra，依赖以 `pyproject.toml` 为准：

| 依赖 | 用途 |
| --- | --- |
| `vane-ai==0.1.0a1` | Vane API、custom DuckDB 和 worker |
| `openai==2.45.0` | OpenAI-compatible Qwen client |
| `minio` | 对象读写和 SHA-256 UDF |
| `psycopg[binary]` | PostgreSQL 输入、Fixture 和原子发布 |
| `rapidocr`、`onnxruntime` | 有状态 CPU OCR Actor |
| `numpy`、`pillow` | Fixture 图片和图片质量计算 |
| `pyarrow` | Relation/Python 数据边界 |
| `pyyaml` | 严格读取 `runtime.yml` |
| `pytz` | 稳定生成 Fixture 时间 |
| `pytest` | Fast tests |

`pip check` 必须输出 `No broken requirements found`。

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
| Runner | `local` |
| PostgreSQL DSN | `postgresql://vane_insight:***@127.0.0.1:5432/vane_insight` |
| 原始 Relation | `claims_disposition_raw.claims` |
| 输出 Relation | `claims_disposition_output.claim_disposition` |
| MinIO | `127.0.0.1:9000`，bucket `claims-disposition-fixtures` |
| OCR | RapidOCR CPU；必需字段 `claim_number`、`claimant_name`、`loss_date`；最低平均置信度 `0.70` |
| AI | OpenAI provider；`http://127.0.0.1:8001/v1`；模型 `Qwen2.5-VL-3B-Instruct`；并发 `1`；超时 `120` 秒 |

在 `runtime.yml` 中设置 `runner: local` 或 `runner: ray`，两种模式共用同一套 SQL 和 Relation Pipeline。Pipeline 将 `DocumentOcrActor` 挂载为有状态表达式 `document_ocr_json(bucket, object_key)`，`int_claim_document_ocr_udf.sql` 对每份合格证明文档调用一次。每个 `*_udf.sql` 文件都是直接 UDF 投影，由 `Relation.write_parquet()` 交给当前启用的 Vane Runner（`LocalRunner.run_write` 或 `RayRunner.run_write`）执行；紧随其后的纯 SQL 文件再解析、关联、分类或聚合物化结果。Driver 本地输入会临时落为 Parquet，结果注册回 Driver 的 DuckDB catalog，运行结束后删除 staging 目录。必须使用 `vane-ai==0.1.0a1`，因为该版本修复了 Ray 路径对逐行 UDF passthrough 列的保留；“有状态 Actor → 下游 SQL → 无状态校验 UDF → 下游 SQL”的分层形态已在两种 Runner 上通过冒烟测试。

公共物化器采用 Runner-backed write API，而不是回退到 DuckDB 直接导出，因此 Local 和 Ray 共用同一个执行边界。

加载器会校验 YAML 结构、SQL identifier、loopback URL、必需值和数值范围。诊断不会打印完整 PostgreSQL DSN、MinIO secret 或 AI key。当 AI URL 是 loopback 地址时，Launcher 会清理 HTTP proxy 变量并补充 `NO_PROXY`/`no_proxy`。

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
| `No matching distribution found for vane-ai` | 确认 Ubuntu 24.04 x86_64、CPython 3.12、glibc 2.39 或更新，并使用完整的 TestPyPI 与 extra-index 命令 |
| Python/Vane/DuckDB 版本不匹配 | 重新激活项目环境并安装固定 wheel；Launcher 会输出解释器、prefix、expected/actual 和安装命令 |
| Vane 或 DuckDB 指向当前环境之外 | 清除继承的 `PYTHONPATH`，激活 `.venv`，重新执行两步安装 |
| 缺少直接依赖 | 执行 `python -m pip install -r requirements.txt` 和 `python -m pip check` |
| PostgreSQL 连接或鉴权失败 | 检查 endpoint、database、role、密码，以及 schema/table 创建和写入权限 |
| MinIO 连接或鉴权失败 | 检查 endpoint、凭据、HTTP/TLS 模式，以及 bucket create/list/read/write/delete 权限 |
| Qwen health、模型名或图片请求失败 | 按照[本地 Qwen 指南](../../docs/local-qwen-service.zh.md)检查 driver、OOM、端口、served name、模型下载和代理 |
| `fixture load failed` | 检查 PostgreSQL 和 MinIO 权限；重跑 `fixture` 只会刷新合成快照 |
| `verification failed` | 查看命令报告的缺失、额外、重复或不匹配理赔，修复服务/Runtime 后重跑 `e2e` |

## 精确运行时标识

| 组件 | 必需标识 |
| --- | --- |
| Vane distribution metadata（`vane-ai`） | `0.1.0a1` |
| `vane.__version__` | `0.1.0a1` |
| DuckDB Python package | `0.1.0a1` |
| DuckDB engine | `v1.6.0-dev1` |
| DuckDB source revision | `398033a962` |
| OpenAI Python client | `2.45.0` |

Launcher 同时要求 `vane.func`、`vane.cls`、`vane.attach_function`、`vane.configure`、`vane.ai.prompt` 和 `duckdb.ray_cxx`。任一不匹配都会明确失败，不会静默回退到普通 DuckDB。

## 数据、凭据和隐私

- 不要提交真实理赔记录、客户照片、私人文档、生产凭据、模型权重或运行生成数据。
- `runtime.yml` 中的值只用于 loopback 合成 Demo。
- 内置车辆图片的许可信息位于相邻 NOTICE 文件。
- 运行时生成的理赔文档只包含合成 fixture 数据。
