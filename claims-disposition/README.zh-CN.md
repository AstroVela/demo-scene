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

确定性聚合与决策规则保存在普通 DuckDB `.sql` 文件中；材料处理、AI 调用和响应校验由 Python 编排器通过 Vane Runner 执行。项目不依赖 dbt、Jinja、macro 或 `ref()`。

## Demo 做了什么

1. 从 PostgreSQL 读取 4 条合成理赔记录及其材料元数据，并根据材料中的 MinIO locator 读取车辆受损照片和理赔证明文档；运行时不直接读取本地 fixture 文件。
2. 校验每份材料的文件身份、顺序、角色、媒体类型、bucket 和规范化对象路径，并检查 MinIO 对象是否存在、计算 SHA-256，避免错误或被替换的文件进入自动处理。
3. 通过 Vane Runner 对车辆照片执行质量分析，对证明文档复用 RapidOCR 引擎，并提取理赔编号、申请人姓名和出险日期等字段，判断材料是否完整、清晰且与当前理赔一致。
4. 仅将通过完整性、质量和 Hash 校验的照片发送给 Qwen，提取目标车辆是否清晰、是否存在损伤、损伤部位、损伤类型、严重程度、置信度和不确定性原因等结构化事实。
5. 对模型响应执行严格合同校验，再汇总同一理赔的多张照片结果，识别模型失败、证据冲突、目标车辆不清晰、置信度不足和高严重程度风险等不能自动处理的情况。
6. 最后由确定性 SQL 按“补充材料、人工复核、拒赔候选、支付候选”的优先级生成工作流建议，校验九列输出合同，并在一个事务中写回 PostgreSQL；内置 fixture 用于验证四种分流结果都能稳定复现。

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

## 实现文件组织与 Vane 使用位置

```text
claims-disposition/
├── runtime.yml
│   # 配置 Vane Runner（local/ray）、PostgreSQL、MinIO、OCR 和 Qwen。
│
├── scripts/
│   └── run_demo.py
│       # Demo 统一入口；校验 Python、Vane、DuckDB 版本后转交给 CLI。
│
├── src/claims_disposition_sql_pipeline/
│   ├── cli.py
│   │   # 编排 fixture、run、verify 和 e2e 四种命令。
│   │
│   ├── config.py
│   │   # 读取并严格校验 runtime.yml，生成类型明确的运行配置。
│   │
│   ├── fixture_loader.py
│   │   # 生成合成理赔材料，并分别写入 PostgreSQL 和 MinIO。
│   │   # Fixture 只负责初始化数据，不是 Pipeline 的运行时数据源。
│   │
│   ├── pg.py
│   │   # 定义 PostgreSQL 原始表和输出表，读取理赔快照并探测连接。
│   │
│   ├── minio_store.py
│   │   # 封装 MinIO 对象读取、存在性检查、SHA-256、上传和清理。
│   │
│   ├── pipeline.py
│   │   # 整条 DAG 的主编排器：读取 PostgreSQL、注册 SQL 输入、
│   │   # 调度材料处理、AI、决策 SQL，并把最终结果交给发布模块。
│   │   └── 【Vane】通过 vane.configure 选择 Local 或 Ray Runner；
│   │       使用 map_batches 执行材料处理和模型响应校验；
│   │       使用 Relation.write_parquet 统一两种 Runner 的物化路径。
│   │
│   ├── vane_udfs.py
│   │   # 图片质量分析、文档字段提取、材料质量判断和 AI JSON 校验。
│   │   └── 【Vane】定义无状态 Function、可复用 RapidOCR 的
│   │       DocumentOcrActor，以及由 Runner 执行的 batch actor。
│   │
│   ├── photo_ai.py
│   │   # 重新读取并校验照片 Hash，构造损伤分析 Prompt，
│   │   # 校验请求与响应必须绑定同一个 claim、file 和 SHA-256。
│   │   └── 【Vane】通过 vane.ai.prompt 调用 Qwen 多模态模型，
│   │       再通过当前 Runner 物化模型响应。
│   │
│   ├── sql/
│   │   ├── staging/
│   │   │   ├── stg_claims.sql
│   │   │   │   # 将 PostgreSQL 理赔快照转换为类型明确的 Claim Relation。
│   │   │   ├── stg_claim_materials.sql
│   │   │   │   # 将 materials_json 展开为逐文件记录，并校验角色、
│   │   │   │   # 媒体类型、重复标识和规范化 MinIO locator。
│   │   │   └── stg_run_config.sql
│   │   │       # 将不含凭据的 OCR、模型和运行参数暴露给 SQL。
│   │   │
│   │   ├── intermediate/
│   │   │   ├── int_claim_material_facts.sql
│   │   │   │   # 定义材料处理的逻辑合同，并把 Vane Runner 产生的
│   │   │   │   # 逐文件对象、Hash、质量和 OCR 事实聚合为一条 Claim 记录。
│   │   │   ├── int_claim_damage_facts.sql
│   │   │   │   # 将 Runner 校验后的逐照片损伤事实聚合到 Claim 级别，
│   │   │   │   # 识别结果冲突、不确定性和高严重程度风险。
│   │   │   └── int_claim_decision_facts.sql
│   │   │       # 使用确定性 SQL 生成补充材料、人工复核、
│   │   │       # 拒赔候选和支付候选，并明确规则优先级。
│   │   │
│   │   └── marts/
│   │       └── claim_disposition.sql
│   │           # 生成最终九列输出，包括 disposition、原因、
│   │           # 下一步动作和 supporting_facts_json。
│   │
│   ├── output_writer.py
│   │   # 校验最终输出合同，并在一个事务中替换 PostgreSQL 结果快照。
│   │
│   ├── verify_outputs.py
│   │   # 验证四条合成理赔是否稳定得到四种预期分流结果。
│   │
│   └── assets/
│       # Demo 使用的合成车辆照片及其来源说明。
│
└── tests/fast/
    # 覆盖配置、Runner 编排、SQL 路径、发布合同和发行包结构。
```

执行主线是 `run_demo.py → cli.py → pipeline.py → Vane Function/Actor/AI → SQL Relations → output_writer.py → verify_outputs.py`。Vane 负责可切换的执行后端、批处理 Actor、多模态模型调用和 Relation 物化；SQL 文件保留材料聚合与最终决策逻辑，使模型只提取事实，不直接决定支付或拒赔。

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
