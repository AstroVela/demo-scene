# 使用 Vane 构建可审计的多模态理赔分流

[English](README.md)

一笔车辆理赔通常不只存在于一张表中：案件记录保存在 PostgreSQL，受损照片和理赔材料位于 MinIO，而最终处理建议必须能够复核。本 Demo 使用一条 Vane Relation Pipeline 串联这些输入，完成材料质量检查、RapidOCR 引擎复用、真实本地 Qwen 多模态损伤事实提取、确定性的 DuckDB SQL 分流，并把每条理赔的建议原子发布回 PostgreSQL。

内置合成 fixture 覆盖四种工作流结果：

| Claim | 预期 disposition |
| --- | --- |
| `CLM-APPROVE` | `approve_for_payment` |
| `CLM-DENY` | `deny_claim` |
| `CLM-MISSING` | `request_more_materials` |
| `CLM-REVIEW` | `manual_review` |

> 这些结果是工作流处理建议，不是承保结论、责任认定、赔付金额计算，也不是受监管的最终拒赔决定。

## 为什么使用 Vane

Vane 是面向多模态数据的多模计算引擎，让结构化记录、文档、图片、SQL、无状态 Python UDF、有状态 Actor 和 AI 模型在同一条可组合、可追踪的 Relation Pipeline 中协同执行。Vane 还将 Pipeline 逻辑与执行后端解耦。本 Demo 已验证 `local` 和 `ray` 两种 Runner，仓库默认仍为 `local`。

## 架构

![Vane 理赔多模态数据流程图](docs/vane-claims-data-flow.png)

[打开 PNG](docs/vane-claims-data-flow.png) · [编辑 Excalidraw 源文件](docs/vane-claims-data-flow.excalidraw)

```text
PostgreSQL 理赔单 + MinIO 照片/文档
  -> 对象、Hash、质量和 OCR 事实
  -> 可信照片请求
  -> Qwen 多模态事实提取
  -> 确定性决策 SQL
  -> 输出合同校验
  -> PostgreSQL 原子发布
```

核心 Relation 路径如下：

```text
stg_claims / stg_claim_materials / stg_run_config
  -> int_claim_material_facts
  -> int_claim_photo_ai
  -> int_claim_damage_facts
  -> int_claim_decision_facts
  -> claim_disposition
```

除 AI Relation 外，其他转换都是普通 DuckDB `.sql` 文件，不依赖 dbt、Jinja、macro 或 `ref()`。

## Demo 做了什么

1. 从 PostgreSQL 加载 4 条合成理赔记录，从 MinIO 加载 8 个 JPEG/PNG 对象。
2. 校验 locator、Hash、图片质量、OCR 字段和理赔编号一致性。
3. 只把可信的车辆受损照片发送给 Qwen，提取损伤和不确定性事实。
4. 使用确定性 SQL 规则生成四种工作流建议之一。
5. 校验九列输出合同，并在一个事务中替换 PostgreSQL 结果快照。

## 运行 Demo

Demo 需要已验证的 Python/Vane 环境，以及正在运行的 PostgreSQL、MinIO 和 Qwen 服务。先按照[完整运行手册](docs/runbook.zh-CN.md)准备环境，然后执行：

```bash
python scripts/run_demo.py e2e
```

成功运行会输出：

```text
loaded 4 claims and 8 objects
published 4 claim dispositions
verified 4 claim dispositions: CLM-APPROVE=approve_for_payment, CLM-DENY=deny_claim, CLM-MISSING=request_more_materials, CLM-REVIEW=manual_review
```

项目没有 AI mock fallback：服务不可用、图片不可读、AI JSON 不合规、运行时不兼容、SQL 失败或发布失败都会明确返回非零。

## Vane 在哪里使用

| 需求 | Vane 落地方式 |
| --- | --- |
| 对象、Hash、图片质量、OCR 字段和 AI 合同检查 | 使用 `@vane.func` 声明无状态 UDF |
| 复用已经初始化的 RapidOCR 引擎 | 使用 `@vane.cls(actor_number=1, gpus=0)` 声明有状态 Actor |
| 将图片字节和结构化上下文发送给 Qwen | 通过 `vane.ai.prompt` 调用多模态 AI Function |
| 关联事实并执行可复核的业务规则 | 使用 Relation 和确定性 DuckDB SQL |

## 决策边界

Qwen 只提取损伤事实、置信度和证据限制，不决定支付或拒赔。SQL 按以下优先级生成建议：

1. `request_more_materials`
2. `manual_review`
3. `deny_claim`
4. `approve_for_payment`

材料缺失、证据不确定、照片冲突、目标车辆不清晰或存在高严重程度风险时，不会进入自动支付/拒赔候选。

## 适配到你的环境

- 将 PostgreSQL 快照替换为自己的理赔数据源，并保持每个 claim 一行。
- 在相同 locator/对象字节合同后替换为其他 S3-compatible 对象存储。
- 替换 RapidOCR，同时保持 OCR JSON 边界不变。
- 在 `runtime.yml` 中接入返回相同 Schema 的 OpenAI-compatible 多模态模型。
- 修改或扩展 SQL 规则，不要把最终决策移交给模型。

## 文档与数据规范

- [运行手册](docs/runbook.zh-CN.md)
- [本地 Qwen2.5-VL 服务搭建指南](docs/local-qwen-service.zh.md)
- [英文架构图](docs/vane-claims-data-flow.en.png)

所有理赔记录、文档和标识符均为合成数据。不要提交真实理赔记录、客户照片、私人文档、生产凭据、模型权重或运行生成数据。
