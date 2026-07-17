# 招采审计运行手册

[返回 Use Case](../README.zh-CN.md) · [English](runbook.md)

本手册记录招采审计 Demo 的精确环境、安装、模型服务、配置、运行和排错合同。

## 已验证环境

| 项目 | 已验证值 |
| --- | --- |
| 操作系统 | Ubuntu 24.04 x86_64，glibc 2.39 |
| Python | CPython 3.12 |
| Vane | `vane-ai==0.1.0a1` |
| PostgreSQL | `127.0.0.1:5432`，database `vane_insight` |
| MinIO | `127.0.0.1:9000`，HTTP |
| 模型服务 | 本机 NVIDIA GPU 上的 `Qwen2.5-VL-3B-Instruct` |

TestPyPI 上的 Vane wheel 面向 CPython 3.12、Linux x86_64 和 `manylinux_2_39` 构建，因此不承诺支持更旧 glibc、其他 Python 次版本或其他 CPU 架构。

安装项目侧 Ubuntu 工具：

```bash
sudo apt update
sudo apt install -y git curl python3.12 python3.12-venv
```

## 从全新代码副本安装

所有项目命令都在 `procurement-compliance-audit` 目录执行。

### 1. 创建虚拟环境

```bash
cd procurement-compliance-audit
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### 2. 安装已验证的 Vane wheel

```bash
python -m pip install -i https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  vane-ai==0.1.0a1
```

两个 index 都要保留，让 Vane 来自 TestPyPI、普通依赖从 PyPI 解析。固定版本也要保留，因为 Launcher 会拒绝未经本 Demo 验证的 Runtime。

### 3. 安装 Demo

```bash
python -m pip install -r requirements.txt
python -m pip check
```

这一步会以 editable 方式安装源码，依赖以 `pyproject.toml` 为准：

| 依赖 | 用途 |
| --- | --- |
| `vane-ai==0.1.0a1` | Vane API、custom DuckDB 和 worker |
| `openai==2.45.0` | OpenAI-compatible Qwen client |
| `psycopg` | 读取并初始化 PostgreSQL 原始表 |
| `minio` | 读取并初始化 MinIO 原始材料对象 |
| `rapidocr`、`onnxruntime` | 有状态 CPU OCR Actor |
| `pillow` | 图片读取 |
| `pyarrow` | Relation/Python 数据边界 |
| `pyyaml` | 严格读取 `runtime.yml` |
| `pytest` | Fast tests |

`pip check` 必须报告没有损坏的依赖。

## 准备 PostgreSQL 与 MinIO

仓库中的 `runtime.yml` 默认使用以下 loopback 合同：

| 服务 | 默认合同 |
| --- | --- |
| PostgreSQL | `postgresql://vane_insight:***@127.0.0.1:5432/vane_insight` |
| MinIO | access/secret `vaneinsight` / `vaneinsight_dev_password`，`127.0.0.1:9000`，HTTP |

可以复用现有服务，也可以按 PostgreSQL 与 MinIO 官方文档安装。`fixture` 命令会创建四张原始表和 MinIO bucket，并刷新合成数据；它不会创建 server、database/role、MinIO 进程或 access key。

配置或服务准备完成后可独立初始化来源数据：

```bash
python scripts/run_demo.py fixture
```

pipeline 运行时不会再读取 `fixtures/`；该目录只为 `fixture` 命令提供可复现的合成 seed。

## 准备本地 Qwen 服务

模型服务必须使用独立环境。NVIDIA、vLLM、模型下载、启动和排错步骤见共享的[本地 Qwen2.5-VL 服务搭建指南](../../docs/local-qwen-service.zh.md)。

验证服务合同：

```bash
curl -fsS -o /dev/null -w 'health HTTP %{http_code}\n' \
  http://127.0.0.1:8001/health
curl -fsS -H 'Authorization: Bearer dummy' \
  http://127.0.0.1:8001/v1/models | python -m json.tool
```

预期 health 为 HTTP 200，模型列表包含 `Qwen2.5-VL-3B-Instruct`。独立指南还会验证一次真实图片 `/v1/chat/completions` 请求。

## 运行与验证

Launcher 提供三个命令：

```bash
python scripts/run_demo.py fixture
python scripts/run_demo.py run
python scripts/run_demo.py e2e
```

- `fixture`：把 1 个项目、3 家供应商、12 条评分和 2 条证据 locator 写入 PostgreSQL，并把 2 张 PNG 写入 MinIO。
- `run`：探测 PostgreSQL/MinIO，完全从这两个服务读取输入，再执行真实 OCR、Qwen 与 SQL DAG。
- `e2e`：顺序执行 `fixture -> run`。

首次运行使用：

```bash
python scripts/run_demo.py e2e
```

预期终端输出：

```text
Result: review_required | 3 findings | flagged expert EXP-001
Winner recalculation: SUP-JW-001 -> SUP-ZJ-002
```

命令只生成：

```text
output/audit_findings.jsonl  # 3 行
output/audit_summary.jsonl   # 1 行
```

项目没有 AI mock fallback。Qwen 不可用、图片不可读、AI JSON 不合规或 Vane Runtime 不兼容时都会明确失败。

不启动 Qwen 即可运行确定性测试：

```bash
python -m pytest tests/fast -q
```

## 合成 seed 与来源合同

`fixtures/expert-score-anomaly/` 恰好包含四份合成 seed；只有 `fixture` 命令直接读取它们：

| 文件 | Grain | 用途 |
| --- | --- | --- |
| `project.json` | 一个采购项目 | 供应商、MinIO object key、原 winner 和规则阈值 |
| `expert_scores.csv` | expert × supplier，共 12 行 | 4 位专家对 3 家供应商的评分 |
| `expert_recommendation.png` | 一张图片 | `EXP-001` 在招标前推荐景维自动化 |
| `committee_minutes.png` | 一张图片 | `EXP-001` 参加评审且没有回避 |

所有姓名、企业和文档均为合成数据。评分矩阵保证：

- 全部专家参与时，`SUP-JW-001` 平均分最高；
- `EXP-001` 给景维 98 分，其他专家平均 80 分，偏差为 18 分；
- 剔除 `EXP-001` 后，`SUP-ZJ-002` 排名第一。

运行时的权威来源是 PostgreSQL 的 `projects`、`suppliers`、`expert_scores`、`evidence_files` 四张表和 MinIO bucket `procurement-compliance-audit-fixtures`。`evidence_files.bucket/object_key` 是 PostgreSQL 到 MinIO 原始材料的可信 locator；pipeline、OCR Actor 和 AI request builder 都不读取本地路径。

## Relation 合同

| Relation | 物化 | Grain | 用途 |
| --- | --- | --- | --- |
| `stg_scores` | view | expert × supplier | 类型、分值和 supplier 合同 |
| `stg_evidence_images` | view | evidence image | 可信 locator 和 role |
| `int_evidence_ocr` | table | evidence image | 由 SQL 调用有状态 OCR 表达式得到的类型化输出 |
| `int_evidence_ai` | table | evidence image | Qwen 原始 JSON 响应 |
| `int_conflict_facts` | view | evidence image | 校验后的推荐、参评和回避事实 |
| `int_score_metrics` | view | project × conflict signal | Peer average、score delta 和两次排名 |
| `audit_findings` | table | finding | 三条确定性规则 |
| `audit_summary` | table | project | 项目级审计状态 |

`int_evidence_ai` 是唯一由 Python 业务逻辑组装的核心中间 Relation；临时 `*_udf` Relation 均由 SQL 定义并通过当前 Runner 物化。每张图片请求直接绑定 `project_id/file_id`，不依赖 Actor 执行顺序。返回的 `document_type` 必须与 Fixture 中可信的 role 一致；不一致时会使用同一张图片强化合同后重试一次，SQL 还会再次应用 role 绑定。

使用 `queries.sql` 在同一个 Connection 中检查八个 Relation：

```sql
select * from int_score_metrics;
select * from audit_findings order by rule_id;
select * from audit_summary;
```

## AI 响应与决策合同

Qwen 只返回文档类型、专家编号、供应商、推荐、参评、回避、证据原文和置信度，不判断是否违规。

本地服务可能在 JSON 外包一层完整 code fence。校验器只规范化这层完整外壳，并拒绝额外 prose、缺失或未知字段、错误类型和占位证据。响应先在 AI 边界预检，再由 SQL 中挂载的无状态 UDF 独立校验。

两张图片都必须通过 OCR 并真实调用 Qwen。缺少任一图片的 OCR 覆盖时运行失败且不发布输出。两次调用都完成但任一 AI confidence 低于 `0.75` 时，SQL 不生成 finding，并将 summary 标记为 `insufficient_evidence`。

三条确定性 finding 是：

1. `EXP-001-conflict-not-recused`
2. `EXP-002-score-bias`
3. `EXP-003-award-impact`

## 输出合同

正常 Fixture 下，`audit_findings.jsonl` 恰好三行，`audit_summary.jsonl` 恰好一行。证据不足时，findings 为零行，summary 仍为一行且状态为 `insufficient_evidence`。

写入前会校验字段、主键、枚举、计数和证据引用；每个文件都通过同目录临时文件原子替换。

## Runtime 配置

仓库中的 `runtime.yml` 定义：

| 配置项 | 默认值 |
| --- | --- |
| Runner | `local` |
| PostgreSQL 原始表 | `procurement_audit_raw.projects`、`suppliers`、`expert_scores`、`evidence_files` |
| MinIO | `127.0.0.1:9000`，bucket `procurement-compliance-audit-fixtures` |
| 输出目录 | `output` |
| OCR | RapidOCR CPU，最低置信度 `0.60` |
| AI | OpenAI provider，`http://127.0.0.1:8001/v1`；模型 `Qwen2.5-VL-3B-Instruct`；并发 `1`；超时 `120` 秒 |

兼容的分布式入口可以将：

```yaml
runner: local
```

改为：

```yaml
runner: ray
```

PostgreSQL/MinIO 来源合同保持不变，两种模式共用同一套 SQL 和 Relation Pipeline。Pipeline 将 `EvidenceOcrActor` 挂载为 `evidence_ocr_json(bucket, object_key)`；`int_evidence_ocr_udf.sql` 将它作为直接 Runner 投影对每张图片调用一次，`int_evidence_ocr.sql` 再解析物化 JSON。响应校验同样采用 `int_conflict_validation_udf.sql → int_conflict_facts.sql` 的分层。Driver 本地输入临时落为 Parquet，`Relation.write_parquet()` 将每个直接 UDF 或 AI Relation 交给当前 Runner，结果注册回 Driver 的 DuckDB catalog 供下一段纯 SQL 使用。必须使用 `vane-ai==0.1.0a1`，因为该版本修复了 Ray 路径对逐行 UDF passthrough 列的保留；分层的有状态/无状态 SQL UDF 形态已在两种 Runner 上通过冒烟测试。真实多节点目标集群仍需单独做基础设施 smoke test。

公共物化器采用 Runner-backed write API，而不是回退到 DuckDB 直接导出，因此 Local 和 Ray 共用同一个执行边界。

## 排错

| 现象 | 处理方式 |
| --- | --- |
| `No matching distribution found for vane-ai` | 确认 Ubuntu 24.04 x86_64 和 Python 3.12，并使用完整的 TestPyPI 与 extra-index 命令 |
| Python/Vane/DuckDB 版本不匹配 | 重新激活 `.venv` 并安装固定 wheel；Launcher 会报告解释器、prefix 和 expected/actual |
| 缺少直接依赖 | 执行 `python -m pip install -r requirements.txt` 和 `python -m pip check` |
| PostgreSQL 连接、鉴权或表初始化失败 | 检查 DSN、database/role、端口，以及 schema/table 的读写权限 |
| MinIO 连接、鉴权或对象读取失败 | 检查 endpoint、HTTP/TLS、access key，以及 bucket 的 list/read/write/delete 权限 |
| Qwen health 或图片请求失败 | 按照[本地 Qwen 指南](../../docs/local-qwen-service.zh.md)检查端口、driver、OOM、模型名和代理 |
| 输出不是三条 finding | 检查终端错误和 Qwen 响应；默认 Fixture 的 OCR 或 AI confidence 没有达到门槛 |

## 精确运行时标识

| 组件 | 必需标识 |
| --- | --- |
| Vane distribution metadata（`vane-ai`） | `0.1.0a1` |
| `vane.__version__` | `0.1.0a1` |
| DuckDB Python package | `0.1.0a1` |
| DuckDB engine | `v1.6.0-dev1` |
| DuckDB source revision | `398033a962` |
| OpenAI Python client | `2.45.0` |

任一标识或必需 Vane API 不匹配都会启动失败，不会静默回退到普通 DuckDB。升级 Runtime 时必须同步更新 Launcher 和真实端到端验收。

## 数据、凭据和隐私

- 不要提交真实招采记录、证据文档、个人数据、生产凭据、模型权重或生成结果。
- Fixture 中的姓名、企业、评分和文档均为合成数据。
- `runtime.yml` 只包含 loopback Demo 配置。
