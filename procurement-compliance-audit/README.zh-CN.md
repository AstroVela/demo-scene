# 使用 Vane 审计招采利益冲突与评分异常

[English](README.md) | **简体中文**

一名评审专家在招标前推荐了“景维自动化”，评审时没有回避，又给这家供应商异常高分；剔除他的评分后，第一名从 `SUP-JW-001` 变成 `SUP-ZJ-002`。

本 Demo 将结构化评分与两份图片证据结合起来，提取推荐、参评和回避事实，重新计算供应商排名，并产生三条确定性审计发现：

```text
Result: review_required | 3 findings | flagged expert EXP-001
Winner recalculation: SUP-JW-001 -> SUP-ZJ-002
```

> 输出是有证据支持的复核线索，不是法律、纪律或最终合规结论。

## 为什么使用 Vane

Vane 是面向多模态数据的多模计算引擎，让评分表、文档图片、SQL、无状态 Python UDF、有状态 Actor 和 AI 模型在同一条可组合、可追踪的 Relation Pipeline 中协同执行。OCR Worker 使用 `@vane.cls` 注册，严格响应校验器使用 `@vane.func` 注册，Qwen 则通过 Vane AI API 调用。仓库默认使用 `local` Runner，同一套 fixture 已同时通过 Local 和 Ray 验证。Local 在 Driver 上创建一份 RapidOCR 引擎，对每个可信证据 locator 各执行一次，并把不可变结果暴露给 SQL；Ray 将 OCR Worker 挂载为有状态表达式，并通过 `vane.ai.prompt` 调用 Qwen。

## 架构

![Vane 招采合规审计数据流程图](docs/vane-procurement-audit-data-flow.png)

图中展示两种 Runner 共用的逻辑 Relation 边界；Local 与 Ray 的 OCR、AI 执行位置差异见下文。

```text
PostgreSQL 项目/供应商/评分/证据元数据 + MinIO 2 张 PNG 图片
  -> 类型明确的评分和证据 Relation
  -> RapidOCR（Driver 本地查询或 Ray Actor）
  -> Qwen 多模态事实提取
  -> 严格 AI 响应合同
  -> SQL 评分指标和三条审计规则
  -> audit_findings + audit_summary
```

1. 从 PostgreSQL 读取项目、供应商、专家评分和证据文件元数据，并根据其中的 `bucket/object_key` 从 MinIO 读取推荐记录和评审会议纪要两张 PNG 图片。
2. 校验项目、供应商、4 位专家对 3 家供应商的完整评分矩阵，以及证据角色和 MinIO locator，确保进入后续流程的数据结构完整且来源可信。
3. 在 `int_evidence_ocr_udf.sql` 中直接调用 `evidence_ocr_json`，再由 `int_evidence_ocr.sql` 解析图片文字、OCR 状态和置信度；只有满足质量要求的证据才会进入多模态分析。Local 使用 Driver 持有的一份 RapidOCR 引擎与不可变结果查询，Ray 使用可复用的有状态 Actor；两条路径返回相同的 OCR JSON 合同。
4. 将图片、OCR 文本和供应商上下文发送给 Qwen，提取“专家推荐了哪家供应商、是否参加评审、是否回避、对应证据原文和置信度”等结构化事实，并通过严格的 JSON 合同和证据角色进行校验。
5. 使用确定性 SQL 对比相关专家评分与其他专家平均分，并分别计算包含和剔除该专家时的供应商排名，生成“存在关联且未回避”“评分显著偏高”“剔除该专家后中标结果改变”三类审计发现。
6. 最终生成 `audit_findings.jsonl` 和 `audit_summary.jsonl`；证据充分时给出 `review_required` 及可复核的指标、阈值和证据引用，证据不足时明确标记为 `insufficient_evidence`，而不是让模型直接作出违规结论。

## 运行 Demo

本 Demo 要求 CPython 3.12，并固定公共 PyPI 上的 `vane-ai==0.1.0a1`。该版本提供面向 CPython 3.10、3.11 和 3.12 的 `manylinux_2_28_x86_64` wheel（glibc 2.28 或更新），但 Launcher 只接受本 Demo 已验证的 CPython 3.12 运行时。先按照[完整运行手册](docs/runbook.zh-CN.md)创建环境，执行 `python -m pip install vane-ai` 安装 Vane，再执行 `python -m pip install -r requirements.txt` 安装 Demo，并准备正在运行的 PostgreSQL、MinIO 和本地 Qwen 服务，然后运行：

```bash
python scripts/run_demo.py e2e
```

`runtime.yml` 默认是 `runner: local`。如需验证分布式 Actor 和 AI Relation 路径，可改为 `runner: ray` 并连接 Ray 集群；两种模式都已使用真实 fixture、OCR 和 Qwen 服务跑通。

`e2e` 先把仓库中的合成 seed 数据写入 PostgreSQL/MinIO，再让 pipeline 只从这两个服务读取输入，并执行真实 OCR 和 Qwen 推理。没有 AI mock fallback。运行后生成：

```text
output/audit_findings.jsonl  # 3 行
output/audit_summary.jsonl   # 1 行
```

## 实现文件组织与 Vane 使用位置

下图包含全部 10 个 SQL 文件。实线表示主执行流，虚线表示对可信运行时数据的其他跨阶段直接依赖。

![采购合规审计 SQL 依赖 DAG](docs/vane-procurement-audit-sql-dag.png)

紫色的 `int_evidence_ai` 节点不是 SQL 文件：`ai.py` 将通过门槛的 OCR、可信来源元数据、MinIO 图片字节和供应商别名组合起来，通过 Vane AI 创建该 Relation，再交回 SQL 校验链路。

```text
./
├── pyproject.toml
│   # 声明 Python/Runtime 依赖，其中包括公共 PyPI Vane 的精确版本。
│
├── requirements.txt
│   # 根据 pyproject.toml 安装当前源码及 Fast Test Extra。
│
├── runtime.yml
│   # 配置 Vane Runner（默认 Local）、PostgreSQL、MinIO、OCR、
│   # Qwen 和 JSONL 输出目录。
│
├── scripts/
│   └── run_demo.py
│       # 校验 CPython 3.12、Vane/DuckDB 精确标识、必需 API、
│       # 包来源和 Loopback 网络设置，再调用 CLI。
│
├── fixtures/expert-score-anomaly/
│   ├── project.json
│   ├── expert_scores.csv
│   ├── expert_recommendation.png
│   └── committee_minutes.png
│       # 本地合成 seed 数据，只用于初始化 PostgreSQL 和 MinIO；
│       # Pipeline 运行时不直接读取这些文件。
│
├── queries.sql
│   # 用于查看 OCR、AI 事实、评分指标、Finding 和 Summary 等核心 Relation。
│
├── src/procurement_audit_sql_demo/
│   ├── cli.py
│   │   # 编排 fixture、run 和 e2e，并展示审计/排名结果，
│   │   # 以及当前 Runner 实际使用的 Vane 能力。
│   │
│   ├── config.py
│   │   # 读取并严格校验 runtime.yml，生成类型明确的运行配置。
│   │
│   ├── fixture_loader.py
│   │   # 校验本地 seed 数据，将业务记录写入 PostgreSQL，
│   │   # 将推荐记录和会议纪要图片写入 MinIO。
│   │
│   ├── pg.py
│   │   # 定义项目、供应商、专家评分和证据 locator 四张原始表，
│   │   # 并按稳定顺序读取完整业务快照。
│   │
│   ├── minio_store.py
│   │   # 封装 MinIO 图片读取、上传、Bucket 初始化和 Fixture 清理。
│   │
│   ├── source_data.py
│   │   # 校验项目、供应商、4×3 评分矩阵和证据 locator，
│   │   # 再转换为类型明确的 Arrow SourceBundle。
│   │
│   ├── pipeline.py
│   │   # 同时管理 Driver DuckDB Catalog 与独立 Runner Connection，
│   │   # 通过临时 Parquet 跨越边界，并执行全部八个核心 Relation。
│   │   └── 【Vane】vane.configure 选择 Local 或 Ray；
│   │       Relation.write_parquet 将 OCR/校验投影物化回 Driver Catalog。
│   │       Local 挂载 Driver 生成的 OCR 查询；Ray 挂载 EvidenceOcrActor。
│   │
│   ├── vane_functions.py
│   │   # OCR 输出规范化和严格的 AI JSON/文档类型合同校验。
│   │   └── 【Vane】@vane.func 定义 validate_audit_fact_json；
│   │       @vane.cls 定义 EvidenceOcrActor，Local 在 Driver 实例化，Ray 挂载执行。
│   │
│   ├── ai.py
│   │   # 将 OCR 文本、供应商别名和图片组合成多模态请求，
│   │   # 校验模型事实必须与可信证据角色一致，合同失败时重试一次。
│   │   └── 【Vane】Local 使用 vane.ai.load_provider，并在 Driver 复用一份
│   │       异步 Prompter；Ray 使用 vane.ai.prompt 和 Runner 物化。
│   │
│   ├── sql/
│   │   ├── staging/
│   │   │   ├── stg_scores.sql
│   │   │   │   # 标准化有效的 PostgreSQL 专家评分，并附加规范供应商名称和别名。
│   │   │   └── stg_evidence_images.sql
│   │   │       # 选择支持 OCR 的 PNG 证据，同时保留可信项目、角色和 MinIO locator 字段。
│   │   │
│   │   ├── intermediate/
│   │   │   ├── int_evidence_ocr_udf.sql
│   │   │   │   # 通过直接 Runner SQL，对每张暂存证据图片调用 evidence_ocr_json。
│   │   │   ├── int_evidence_ocr.sql
│   │   │   │   # 将 Runner 生成的 JSON 解析成类型明确的 OCR 状态、文本、置信度和行数，并保留原始响应。
│   │   │   ├── int_conflict_validation_inputs.sql
│   │   │   │   # 将每个 Vane AI 响应重新关联到 PostgreSQL 中可信的项目、文件和证据角色身份。
│   │   │   ├── int_conflict_validation_udf.sql
│   │   │   │   # 通过直接 Runner SQL 应用严格的 AI 响应和文档类型校验器。
│   │   │   ├── int_conflict_facts.sql
│   │   │   │   # 将校验后的 JSON 解析为类型明确的合规事实，并拒绝可信角色与文档类型不一致的证据。
│   │   │   └── int_score_metrics.sql
│   │   │       # 关联两类证据、解析供应商别名、计算专家与 peers 的评分差，并重排包含或剔除该专家时的名次。
│   │   │
│   │   └── marts/
│   │       ├── audit_findings.sql
│   │       │   # 只从满足证据门槛的数据中生成未回避、评分偏高和中标影响三类确定性 Finding。
│   │       └── audit_summary.sql
│   │           # 组合项目参数、指标和 Finding 计数，生成 passed、review_required 或 insufficient_evidence 状态。
│   │
│   ├── output_writer.py
│   │   # 校验 Finding、Summary 和证据引用，再分别原子替换两个 JSONL 快照。
│   │
│   └── verify_outputs.py
│       # 发布前验证合成案例是否产生三条 Finding 和预期排名变化。
│
└── tests/fast/
    # 覆盖来源合同、OCR Actor、AI 合同、SQL DAG、Runner 编排和输出发布。
```

执行主线是 `run_demo.py → cli.py → source_data.py → pipeline.py → Vane OCR/AI/校验 → SQL Relations → verify_outputs.py → output_writer.py`。Driver 读取 PostgreSQL/MinIO、校验 Arrow `SourceBundle`、持有纯 SQL DuckDB Catalog、验证 Fixture 结果并发布 JSONL。对于 OCR 与响应校验的 `*_udf.sql` 投影，`pipeline.py` 将 Driver 输入临时落为 Parquet，通过所选 Vane Runner 执行，再把物化结果注册回 Driver Catalog。Local 使用 Driver 持有的一份 OCR 实现和一份复用的 Vane Provider Prompter，生成 OCR 查询与 AI 响应表；Ray 挂载 OCR Actor，并通过 `vane.ai.prompt` 执行 AI。下游 SQL 保持相同的解析、可信角色过滤、评分偏差、排名变化和审计规则合同。

## 审计逻辑与边界

模型只提取文档类型、专家、供应商、推荐、参评、回避、证据原文和置信度，不判断是否违规。SQL 生成：

1. `EXP-001-conflict-not-recused`：推荐供应商后仍参加评审且未回避。
2. `EXP-002-score-bias`：对相关供应商的得分比 peers 至少高 15 分。
3. `EXP-003-award-impact`：剔除该专家后 winner 改变。

两张图片都必须通过 OCR 并真实调用 Qwen。响应合同不合规时运行失败；响应合规但置信度不足时不生成 finding，并将 summary 标记为 `insufficient_evidence`。

## 适配到你的环境

- 将四张 PostgreSQL 原始表替换为自己的采购业务快照，并保持 Relation grain。
- 在 `evidence_files` 中写入自己的 MinIO/S3-compatible `bucket/object_key` locator。
- 替换 RapidOCR，同时保持 OCR JSON 边界不变。
- 在 `runtime.yml` 中接入返回相同 Schema 的 OpenAI-compatible 多模态模型。
- 为新审计规则增加显式、可测试的 SQL 分支，不要让模型直接输出风险结论。

## 文档与数据规范

- [运行手册](docs/runbook.zh-CN.md)
- [本地 Qwen2.5-VL 服务搭建指南](../docs/local-qwen-service.zh.md)
- [只读中间 Relation 查询](queries.sql)
- [英文架构图](docs/vane-procurement-audit-data-flow.en.png)

不要提交真实招采记录、证据文档、个人数据、生产凭据、模型权重或生成结果。
