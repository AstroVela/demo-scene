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

Vane 是面向多模态数据的多模计算引擎，让评分表、文档图片、SQL、无状态 Python UDF、有状态 Actor 和 AI 模型在同一条可组合、可追踪的 Relation Pipeline 中协同执行。OCR Worker 使用 `@vane.cls` 注册，严格响应校验器使用 `@vane.func` 注册，Qwen 则通过 Vane AI API 调用。仓库默认使用 `local` Runner，同一套 fixture 已同时通过 Local 和 Ray 验证。Local 在 Driver 上执行一次真实 RapidOCR 并把不可变结果暴露给 SQL；Ray 将 OCR Worker 挂载为有状态表达式，并通过 `vane.ai.prompt` 调用 Qwen。

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

Demo 从公共 PyPI 安装 Vane（`pip install vane-ai`），并需要正在运行的 PostgreSQL、MinIO 和本地 Qwen 服务。先按照[完整运行手册](docs/runbook.zh-CN.md)准备已验证环境，然后执行：

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

```text
<项目根目录>/
├── runtime.yml
│   # 配置 Vane Runner（默认 Local）、PostgreSQL、MinIO、OCR、
│   # Qwen 和 JSONL 输出目录。
│
├── scripts/
│   └── run_demo.py
│       # Demo 统一入口；校验 Python、Vane、DuckDB 版本后调用 CLI。
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
│   │   # 编排 fixture、run 和 e2e 命令，并展示审计结果和排名变化。
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
│   │   # 整条八节点 DAG 的主编排器：读取来源、执行 OCR 和 AI、
│   │   # 运行评分 SQL、生成两个 Mart，并发布最终 JSONL。
│   │   └── 【Vane】通过 vane.configure 选择 Local 或 Ray Runner；
│   │       将 Local OCR 结果或 Ray OCR Actor 暴露给同一个 SQL 调用，
│   │       并通过 Relation.write_parquet 物化交给 Runner 的 SQL 阶段。
│   │
│   ├── vane_functions.py
│   │   # OCR 输出规范化和严格的 AI JSON 合同校验。
│   │   └── 【Vane】validate_audit_fact_json 是无状态 Function；
│   │       EvidenceOcrActor 在 Local 直接执行，在 Ray 挂载为有状态
│   │       evidence_ocr_json SQL 表达式。
│   │
│   ├── ai.py
│   │   # 将 OCR 文本、供应商别名和图片组合成多模态请求，
│   │   # 校验模型事实必须与可信证据角色一致，合同失败时重试一次。
│   │   └── 【Vane】Local 使用公共 provider API，Ray 使用 vane.ai.prompt，
│   │       两者保持相同的请求、重试与响应表合同。
│   │
│   ├── sql/
│   │   ├── staging/
│   │   │   ├── stg_scores.sql
│   │   │   │   # 将 PostgreSQL 专家评分标准化，并关联供应商名称和别名。
│   │   │   └── stg_evidence_images.sql
│   │   │       # 选择支持 OCR 的 PNG 证据及其可信 MinIO locator。
│   │   │
│   │   ├── intermediate/
│   │   │   ├── int_evidence_ocr_udf.sql
│   │   │   │   # 将 evidence_ocr_json 作为直接交给 Runner 的 SQL 投影调用。
│   │   │   ├── int_evidence_ocr.sql
│   │   │   │   # 将 Runner 生成的 JSON 转换成类型明确的 OCR 字段。
│   │   │   ├── int_conflict_validation_inputs.sql
│   │   │   │   # 把 AI 响应绑定到 PostgreSQL 中可信的证据角色。
│   │   │   ├── int_conflict_validation_udf.sql
│   │   │   │   # 将严格响应校验器作为直接 Runner SQL UDF 执行。
│   │   │   ├── int_conflict_facts.sql
│   │   │   │   # 将 Runner 校验后的 AI JSON 转成推荐、参评、
│   │   │   │   # 回避、证据原文和置信度等类型明确的事实。
│   │   │   └── int_score_metrics.sql
│   │   │       # 匹配供应商名称和别名，计算专家与 peers 的评分差，
│   │   │       # 并重新计算包含和剔除该专家时的供应商排名。
│   │   │
│   │   └── marts/
│   │       ├── audit_findings.sql
│   │       │   # 用确定性 SQL 生成未回避、评分偏高和中标影响三类 Finding。
│   │       └── audit_summary.sql
│   │           # 汇总 Finding，并生成 passed、review_required
│   │           # 或 insufficient_evidence 项目状态。
│   │
│   ├── output_writer.py
│   │   # 校验 Finding、Summary 和证据引用，然后原子写入两个 JSONL 文件。
│   │
│   └── verify_outputs.py
│       # 验证合成案例是否稳定产生三条 Finding 和预期的排名变化。
│
└── tests/fast/
    # 覆盖来源合同、OCR Actor、AI 合同、SQL DAG、Runner 编排和输出发布。
```

执行主线是 `run_demo.py → cli.py → source_data.py → pipeline.py → Vane OCR/AI/校验 → SQL Relations → output_writer.py`。Local 将原生 OCR 和异步 provider client 保留在 Driver；Ray 挂载可复用 OCR Actor 并通过 `vane.ai.prompt` 执行 AI。SQL UDF 边界与类型化输出保持一致。每次 UDF 调用都位于清晰可见的 `*_udf.sql` 节点；下游 SQL 负责解析、可信角色过滤、评分偏差、排名变化和审计规则。

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
