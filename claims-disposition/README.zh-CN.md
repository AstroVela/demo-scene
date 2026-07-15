# Claims Disposition SQL

[English](README.md)

这是一个独立、无 dbt 依赖的车辆理赔一级分流 Demo。项目从 PostgreSQL 读取理赔单，从 MinIO 读取 JPEG 车辆照片和 PNG 理赔文档，通过 Vane 完成图片质量分析、stateful RapidOCR 和真实本地 Qwen 多模态调用，再用确定性的 DuckDB SQL 形成处理建议，并把每条理赔的结果原子发布回 PostgreSQL。

内置 fixture 覆盖四种工作流输出：

| Claim | 预期 disposition |
| --- | --- |
| `CLM-APPROVE` | `approve_for_payment` |
| `CLM-DENY` | `deny_claim` |
| `CLM-MISSING` | `request_more_materials` |
| `CLM-REVIEW` | `manual_review` |

这些结果是工作流处理建议，不是承保结论、责任认定、赔付金额计算，也不是受监管的最终拒赔决定。

## 已验证环境

本仓库发布验收使用以下平台。其他平台可以自行尝试，但当前没有完成发布验收：

| 项目 | 已验证值 |
| --- | --- |
| 操作系统 | Ubuntu 24.04 x86_64，glibc 2.39 |
| Python | CPython 3.12 |
| Vane | `vane-ai==0.1.0.dev20260714234347` |
| PostgreSQL | 本机 `127.0.0.1:5432` |
| MinIO | 本机 `127.0.0.1:9000` |
| 模型服务 | NVIDIA CUDA GPU 上的 Qwen2.5-VL-3B，监听 `127.0.0.1:8001` |

TestPyPI 上的 Vane wheel 面向 CPython 3.12、Linux x86_64 和 `manylinux_2_39` 构建，因此不承诺支持更旧 glibc、其他 Python 次版本或其他 CPU 架构。

Ubuntu 项目侧基础工具：

```bash
sudo apt update
sudo apt install -y git curl python3.12 python3.12-venv
```

PostgreSQL、MinIO 和 Qwen 是外部服务，不是 pip 包。launcher 只连接这些服务，不会安装、启动、停止或重启它们。

## 从全新代码副本开始

下面所有项目命令都在仓库根目录执行。

### 1. 创建项目虚拟环境

```bash
cd claims-disposition
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

虚拟环境可以使用其他目录名。launcher 校验的是当前解释器和包来源，不会强制目录必须叫 `.venv`。

### 2. 从 TestPyPI 安装固定版 Vane

```bash
python -m pip install -i https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  vane-ai==0.1.0.dev20260714234347
```

两个 index 参数都要保留：Vane wheel 来自 TestPyPI，普通依赖从 PyPI 解析。不要删除固定版本；`scripts/run_demo.py` 会主动拒绝没有经过本 Demo 验证的 Vane 或 custom DuckDB build。

### 3. 安装 Demo 和全部直接依赖

```bash
python -m pip install -r requirements.txt
python -m pip check
```

`requirements.txt` 以 editable 方式安装当前源码和 test extra，所有直接依赖以 `pyproject.toml` 为准：

| 依赖 | 用途 |
| --- | --- |
| `vane-ai==0.1.0.dev20260714234347` | Vane API、custom DuckDB 和本地 worker |
| `openai==2.45.0` | 本地 OpenAI-compatible Qwen provider client |
| `minio` | 材料对象读写和 SHA-256 UDF |
| `psycopg[binary]` | PostgreSQL 输入、fixture 写入和结果原子发布 |
| `rapidocr`、`onnxruntime` | stateful CPU OCR actor 和推理 runtime |
| `numpy`、`pillow` | fixture 图片、图片读取和质量计算 |
| `pyarrow` | Vane relation 与 Python 数据边界 |
| `pyyaml` | 严格读取 `runtime.yml` |
| `pytz` | 稳定生成带时区的 synthetic fixture 时间 |
| `pytest` | launcher 和发布安装合同的快速测试 |

`pip check` 必须输出 `No broken requirements found`。

### 4. 准备 PostgreSQL 和 MinIO

仓库内的 `runtime.yml` 使用以下本机 synthetic Demo 合同：

| 服务 | 必需合同 |
| --- | --- |
| PostgreSQL | database `vane_insight`，用户/密码 `vane_insight` / `vane_insight_dev_password`，`127.0.0.1:5432` |
| MinIO | access/secret `vaneinsight` / `vaneinsight_dev_password`，S3 endpoint `127.0.0.1:9000`，使用 HTTP 而不是 TLS |

可以复用现有本机服务，也可以按 PostgreSQL 与 MinIO 官方文档安装。如果 endpoint 或凭据不同，先修改 `runtime.yml`。仓库里的默认值只适用于 loopback 合成数据，不能直接用于生产。

`fixture` 会创建所需的 PostgreSQL schema/table 和 MinIO bucket，并刷新合成数据；它不会创建 PostgreSQL server、database、role、MinIO server 进程或 MinIO access key。

服务启动后，用已安装的项目代码同时探测 PostgreSQL 和 MinIO：

```bash
python - <<'PY'
from claims_disposition_sql_pipeline.config import load_runtime_config
from claims_disposition_sql_pipeline.pipeline import probe_runtime

probe_runtime(load_runtime_config())
print("PostgreSQL and MinIO: OK")
PY
```

### 5. 准备本地 Qwen 服务

模型服务必须使用独立环境。NVIDIA、vLLM、固定模型下载、启动、smoke test 和排错步骤见：

**[本地 Qwen2.5-VL 服务搭建指南](docs/local-qwen-service.zh.md)**

项目要求的服务合同是：

```bash
curl -fsS -o /dev/null -w 'health HTTP %{http_code}\n' \
  http://127.0.0.1:8001/health
curl -fsS -H 'Authorization: Bearer dummy' \
  http://127.0.0.1:8001/v1/models | python -m json.tool
```

预期 health 为 HTTP 200，`data[].id` 包含 `Qwen2.5-VL-3B-Instruct`。独立指南还会验证一次真实图片 `/v1/chat/completions` 请求。

### 6. 运行真实端到端 Demo

```bash
python scripts/run_demo.py e2e
```

`e2e` 会依次执行 `fixture -> run -> verify`，任一步返回非零就停止。完整成功输出是：

```text
loaded 4 claims and 8 objects
published 4 claim dispositions
verified 4 claim dispositions: CLM-APPROVE=approve_for_payment, CLM-DENY=deny_claim, CLM-MISSING=request_more_materials, CLM-REVIEW=manual_review
```

项目没有 AI mock fallback。服务不可用、图片不可读、AI JSON 不合规、runtime 不匹配、SQL 失败或发布失败都会明确返回非零。

### 7. 运行快速确定性测试

下面的测试只验证 launcher 和公开安装合同，不会调用 Qwen，也不会修改 PostgreSQL/MinIO 数据：

```bash
python -m pytest tests/fast -q
```

## 四个公开命令

所有命令都通过当前虚拟环境运行：

```bash
python scripts/run_demo.py fixture
python scripts/run_demo.py run
python scripts/run_demo.py verify
python scripts/run_demo.py e2e
```

- `fixture` 刷新本机 synthetic snapshot：4 条 claims、4 张 JPEG 照片和 4 份动态生成的 PNG 文档。
- `run` 探测 PostgreSQL 和 MinIO，执行真实 OCR 与 Qwen 推理，运行完整 SQL DAG，校验九列结果并原子替换 PostgreSQL 输出 snapshot。
- `verify` 直接读取 PostgreSQL，并要求四条 fixture 结果完全匹配。
- `e2e` 顺序运行以上三步，是首次运行和发布验收的推荐入口。

## Runtime 配置

项目只读取根目录下的 `runtime.yml`，不会从 Docker 或环境变量自动发现服务。

| 配置项 | 默认值 |
| --- | --- |
| PostgreSQL DSN | `postgresql://vane_insight:***@127.0.0.1:5432/vane_insight` |
| PostgreSQL 原始表 | `claims_disposition_raw.claims` |
| PostgreSQL 输出表 | `claims_disposition_output.claim_disposition` |
| MinIO | `127.0.0.1:9000`，bucket `claims-disposition-fixtures` |
| OCR | RapidOCR CPU；必需字段 `claim_number`、`claimant_name`、`loss_date`；最低平均置信度 `0.70` |
| AI | OpenAI provider；`http://127.0.0.1:8001/v1`；模型 `Qwen2.5-VL-3B-Instruct`；并发 `1`；超时 `120` 秒 |

配置加载器会校验 YAML 结构、SQL identifier、loopback URL、必需值和数值范围。错误会指出不可用服务，但不会打印完整 PostgreSQL DSN、MinIO secret 或 AI key。当 AI URL 是 loopback HTTP 时，launcher 会清理 HTTP proxy 变量，并补充 `NO_PROXY`/`no_proxy`。

## Vane 在项目中的作用

该 Demo 在同一个 DuckDB pipeline 中组合了三类 Vane 能力：

- stateless UDF：用 `@vane.func` 声明 MinIO 对象/hash、图片质量、OCR 字段和 AI JSON 处理；
- stateful UDF：用 `@vane.cls(actor_number=1, gpus=0)` 初始化一次 RapidOCR engine，并在 actor 中复用；
- AI Function：`vane.ai.prompt` 把可信图片字节和结构化 prompt 发送给 Qwen，下游 SQL 再执行确定性业务规则。

完整数据流程如下。也可以单独打开中文版 [PNG](docs/vane-claims-data-flow.png)，或编辑 [Excalidraw 源文件](docs/vane-claims-data-flow.excalidraw)。

![Vane 理赔多模态数据流程图](docs/vane-claims-data-flow.png)

```text
runtime.yml
  + PostgreSQL claims
  + MinIO JPEG photos and PNG documents
        -> staging relations
        -> object / quality / OCR facts
        -> trusted photo requests
        -> Qwen multimodal AI Function
        -> damage and uncertainty facts
        -> deterministic decision SQL
        -> nine-column contract validation
        -> atomic PostgreSQL publication
```

固定关系顺序：

```text
stg_claims
  -> stg_claim_materials
  -> stg_run_config
  -> int_claim_material_facts
  -> int_claim_photo_ai
  -> int_claim_damage_facts
  -> int_claim_decision_facts
  -> claim_disposition
```

`int_claim_photo_ai` 是唯一由 Python 创建的中间表，其他转换都是普通 DuckDB `.sql` 文件，不依赖 dbt、Jinja、macro 或 `ref()`。

## 处理边界

材料阶段验证 locator、对象存在性、SHA-256、照片质量、OCR 字段、文档质量和 claim number 一致性。只有输入完整且可用的 claim 才调用 Qwen；其他 claim 仍进入确定性 decision stage。

Qwen 只抽取照片中的损伤事实、置信度和证据限制，不直接作出支付或拒赔决定。SQL 按以下优先级形成工作流建议：

1. `request_more_materials`
2. `manual_review`
3. `deny_claim`
4. `approve_for_payment`

材料缺失、模型不确定、照片冲突、目标车辆不清晰或高严重程度风险不会进入自动支付/拒赔候选。

## 数据合同

PostgreSQL raw grain 是每个 claim 一行：

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

最终输出 grain 也是每个 claim 一行：

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

writer 在打开 publication transaction 前完成九列、类型、枚举、置信度和时间戳校验。发布时在一个 PostgreSQL transaction 中删除旧 snapshot 并写入新结果，失败会 rollback，不会留下部分数据。

## 安装与运行排错

| 现象 | 处理方式 |
| --- | --- |
| `No matching distribution found for vane-ai` | 确认 Ubuntu 24.04 x86_64、CPython 3.12、glibc 2.39 或更新，并完整使用 TestPyPI 与 extra-index 命令 |
| Python/Vane/DuckDB 版本不匹配 | 重新激活项目环境并安装固定 wheel；launcher 会输出当前解释器、prefix、expected/actual 和安装命令 |
| Vane 或 DuckDB 不在当前环境 | 清除继承的 `PYTHONPATH`，激活 `.venv`，重新执行两步安装 |
| `openai`、`minio`、`psycopg` 等直接依赖缺失 | 执行 `python -m pip install -r requirements.txt` 和 `python -m pip check` |
| PostgreSQL 连接或鉴权失败 | 检查 `127.0.0.1:5432`、database/role/password 和 `runtime.yml`；账户需要 schema/table 创建和写入权限 |
| MinIO 连接或鉴权失败 | 检查 `127.0.0.1:9000`、access/secret 和 HTTP/TLS 模式；账户需要 bucket 创建、list、read、write、delete 权限 |
| Qwen health、模型名或图片请求失败 | 查看[本地 Qwen 指南](docs/local-qwen-service.zh.md)中的 driver、OOM、端口、served name、权重和代理排错 |
| `fixture load failed` | 检查 PostgreSQL 和 MinIO 权限；重跑 `fixture` 只会刷新四条 synthetic claims |
| `verification failed` | 根据命令报告检查缺失、额外、重复或 disposition 不匹配的 claim，修复服务/runtime 后重跑 `e2e` |

## 精确运行时标识

launcher 不只检查 API 形状，还会校验：

| 组件 | 必需标识 |
| --- | --- |
| Vane distribution metadata（`vane-ai`） | `0.1.0.dev20260714234347` |
| `vane.__version__` | `0.1.0.dev20260714234347` |
| DuckDB Python package | `0.1.0.dev20260714234347` |
| DuckDB engine | `v1.6.0-dev121` |
| DuckDB source revision | `ca6948529b` |
| OpenAI Python client | `2.45.0` |

同时要求 `vane.func`、`vane.cls`、`vane.attach_function`、`vane.configure`、`vane.ai.prompt` 和 `duckdb.ray_cxx`。任一不匹配都会明确失败，不会静默回退到普通 DuckDB。

## 数据、凭据和隐私

- 不要提交真实理赔记录、客户照片、私人文档、生产凭据、模型权重或生成数据。
- `runtime.yml` 中的值只用于 loopback synthetic Demo。
- 内置车辆图片的许可信息位于相邻 NOTICE 文件。
- 运行时生成的理赔文档只包含 synthetic fixture 数据。
