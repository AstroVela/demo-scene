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

Vane 是面向多模态数据的多模计算引擎，让评分表、文档图片、SQL、无状态 Python UDF、有状态 Actor 和 AI 模型在同一条可组合、可追踪的 Relation Pipeline 中协同执行。同一条 Pipeline 可以先在单机开发，再通过切换 Runner 扩展到高并发分布式执行，而不需要重写业务逻辑。

## 架构

![Vane 招采合规审计数据流程图](docs/vane-procurement-audit-data-flow.png)

[打开 PNG](docs/vane-procurement-audit-data-flow.png) · [编辑 Excalidraw 源文件](docs/vane-procurement-audit-data-flow.excalidraw)

```text
PostgreSQL 项目/供应商/评分/证据元数据 + MinIO 2 张 PNG 图片
  -> 类型明确的评分和证据 Relation
  -> 有状态 RapidOCR
  -> Qwen 多模态事实提取
  -> 严格 AI 响应合同
  -> SQL 评分指标和三条审计规则
  -> audit_findings + audit_summary
```

## Demo 分析什么

| 输入 | Grain | 用途 |
| --- | --- | --- |
| PostgreSQL `projects` / `suppliers` | 一个项目 / project × supplier | 项目、供应商、原 winner 和规则阈值 |
| PostgreSQL `expert_scores` | expert × supplier，共 12 行 | 4 位专家对 3 家供应商的评分 |
| PostgreSQL `evidence_files` | 一份证据一行 | 可信 role 与 MinIO `bucket/object_key` locator |
| MinIO 2 个 PNG 对象 | 一份图片证据一个对象 | 推荐记录与评审会议纪要的原始材料字节 |

核心 Relation 如下：

```text
stg_scores / stg_evidence_images
  -> int_evidence_ocr
  -> int_evidence_ai
  -> int_conflict_facts
  -> int_score_metrics
  -> audit_findings
  -> audit_summary
```

所有姓名、企业和文档均为合成素材。Fixture 被设计为：`EXP-001` 给景维的评分比其他专家平均分高 18 分，剔除该专家后 winner 改变。

## 运行 Demo

Demo 需要已验证的 Python/Vane 环境，以及正在运行的 PostgreSQL、MinIO 和本地 Qwen 服务。先按照[完整运行手册](docs/runbook.zh-CN.md)准备环境，然后执行：

```bash
python scripts/run_demo.py e2e
```

`e2e` 先把仓库中的合成 seed 数据写入 PostgreSQL/MinIO，再让 pipeline 只从这两个服务读取输入，并执行真实 OCR 和 Qwen 推理。没有 AI mock fallback。运行后生成：

```text
output/audit_findings.jsonl  # 3 行
output/audit_summary.jsonl   # 1 行
```

## Vane 在哪里使用

| 需求 | Vane 落地方式 |
| --- | --- |
| 在多张图片之间复用已经初始化的 OCR 引擎 | 使用 `@vane.cls` 声明有状态 Actor |
| 将图片字节和 OCR 上下文发送给 Qwen | 通过 `vane.ai.prompt` 调用多模态 AI Function |
| 约束模型到规则层之间的 JSON 边界 | 使用 `@vane.func` 声明无状态 UDF 并挂载到 SQL |
| 计算评分偏差、重新排名并生成 finding | 使用 Relation 和确定性 DuckDB SQL |

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
- [本地 Qwen2.5-VL 服务搭建指南](docs/local-qwen-service.zh.md)
- [只读中间 Relation 查询](queries.sql)
- [英文架构图](docs/vane-procurement-audit-data-flow.en.png)

不要提交真实招采记录、证据文档、个人数据、生产凭据、模型权重或生成结果。
