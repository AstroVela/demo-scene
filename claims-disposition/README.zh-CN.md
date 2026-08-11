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

Vane 是面向多模态数据的多模计算引擎，让结构化记录、文档、图片、SQL、无状态 Python UDF、有状态 Actor 和 AI 模型在同一条可组合、可追踪的 Relation Pipeline 中协同执行。Vane 还将 Pipeline 逻辑与执行后端解耦。仓库默认使用 `ray` Runner，并已在本地 Ray runtime 上端到端验证 PostgreSQL、MinIO、RapidOCR Actor 与 Qwen 完整路径。Local 仍是受支持的回退路径：它在 Driver 上创建一份 RapidOCR 引擎，对每个合格证明文档 locator 各执行一次，并把不可变结果暴露给 SQL。

## 架构

![Vane 理赔多模态数据流程图](docs/vane-claims-data-flow.png)

图中展示两种 Runner 共用的逻辑 Relation 边界；Local 与 Ray 的 OCR、AI 执行位置差异见下文。

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

1. 从 PostgreSQL 读取 4 条合成理赔记录及其材料元数据，并根据材料中的 MinIO locator 读取车辆受损照片和理赔证明文档；运行时不直接读取本地 fixture 文件。
2. 校验每份材料的文件身份、顺序、角色、媒体类型、bucket 和规范化对象路径，并检查 MinIO 对象是否存在、计算 SHA-256，避免错误或被替换的文件进入自动处理。
3. 在 `int_claim_document_ocr_udf.sql` 中直接调用 `document_ocr_json`，再由下游 SQL 提取理赔编号、申请人姓名和出险日期等字段，判断材料是否完整、清晰且与当前理赔一致。Local 使用 Driver 持有的一份 RapidOCR 引擎并挂载不可变结果查询；Ray 挂载可复用的有状态 Actor；两条路径向 SQL 返回相同的 OCR JSON 合同。
4. 仅将通过完整性、质量和 Hash 校验的照片发送给 Qwen，提取目标车辆是否清晰、是否存在损伤、损伤部位、损伤类型、严重程度、置信度和不确定性原因等结构化事实。
5. 在直接交给 Runner 的 `int_claim_damage_validation_udf.sql` 中执行模型响应合同校验，再由纯 SQL 汇总同一理赔的多张照片结果，识别模型失败、证据冲突、目标车辆不清晰、置信度不足和高严重程度风险。
6. 最后由确定性 SQL 按“补充材料、人工复核、拒赔候选、支付候选”的优先级生成工作流建议，校验九列输出合同，并在一个事务中写回 PostgreSQL；内置 fixture 用于验证四种分流结果都能稳定复现。

## 运行 Demo

本 Demo 要求 CPython 3.12，并固定 `vane-ai[openai]==0.1.0`。已验证环境使用 uv 从 PyPI 安装 Vane 及其依赖。请按照[完整运行手册](docs/runbook.zh-CN.md) 执行准确的安装命令，并准备正在运行的 PostgreSQL、MinIO 和 Qwen 服务，然后运行：

```bash
python scripts/run_demo.py e2e
```

`runtime.yml` 默认是 `runner: ray`，该路径已在本地 Ray runtime 上使用真实 fixture、OCR 和 Qwen 服务跑通。真实多节点目标集群仍需针对共享路径、worker 凭据和资源容量单独执行基础设施 smoke test。

成功运行会输出：

```text
loaded 4 claims and 8 objects
published 4 claim dispositions
verified 4 claim dispositions: CLM-APPROVE=approve_for_payment, CLM-DENY=deny_claim, CLM-MISSING=request_more_materials, CLM-REVIEW=manual_review
```

项目没有 AI mock fallback：服务不可用、图片不可读、AI JSON 不合规、运行时不兼容、SQL 失败或发布失败都会明确返回非零。

## 实现文件组织与 Vane 使用位置

下图包含全部 18 个 SQL 文件。实线表示主执行流，虚线表示跨阶段的其他直接依赖。

![理赔分流 SQL 依赖 DAG](docs/vane-claims-sql-dag.png)

紫色的 `int_claim_photo_ai` 节点不是 SQL 文件：`photo_ai.py` 通过 Vane AI 创建该 Relation，结果随后重新进入 SQL DAG，完成可信身份绑定和 Runner 校验。

```text
claims-disposition/
├── pyproject.toml
│   # 声明 Python/Runtime 依赖，其中包括 Vane 的精确版本。
│
├── requirements.txt
│   # 根据 pyproject.toml 安装当前源码及 Fast Test Extra。
│
├── runtime.yml
│   # 配置 Vane Runner（默认 Local）、PostgreSQL、MinIO、OCR 和 Qwen。
│
├── scripts/
│   └── run_demo.py
│       # 校验 CPython 3.12、Vane distribution/API/engine 精确标识、必需 API、
│       # 包来源和 Loopback 网络设置，再转交给 CLI。
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
│   │   # 同时管理 Driver DuckDB Catalog 与独立 Runner Connection，
│   │   # 通过临时 Parquet 跨越边界，并编排完整 DAG。
│   │   └── 【Vane】vane.configure 选择 Local 或 Ray；
│   │       Relation.write_parquet 将每个直接 Runner SQL 投影物化回 Driver Catalog。
│   │       Local 挂载 Driver 生成的 OCR 查询；Ray 挂载 DocumentOcrActor。
│   │
│   ├── vane_udfs.py
│   │   # 实现 MinIO 探测/Hash、图片质量、OCR 规范化、
│   │   # 文档合同和严格模型响应校验。
│   │   └── 【Vane】定义无状态 Function/挂载规格与 @vane.cls
│   │       DocumentOcrActor；Local 在 Driver 实例化，Ray 将其挂载执行。
│   │
│   ├── photo_ai.py
│   │   # 重新读取并校验照片 Hash，构造损伤分析 Prompt，
│   │   # 校验请求与响应必须绑定同一个 claim、file 和 SHA-256。
│   │   └── 【Vane】Local 使用 vane.ai.load_provider，并在 Driver 复用一份
│   │       异步 Prompter；Ray 使用 vane.ai.prompt 和 Runner 物化。
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
│   │   │   ├── int_claim_material_inputs.sql
│   │   │   │   # 将材料记录与不含凭据的运行参数组合，并在 Runner 访问前对规范化 locator 做门控。
│   │   │   ├── int_claim_object_probe_udf.sql
│   │   │   │   # 通过直接 Runner SQL，只对可信 MinIO locator 调用 minio_object_exists。
│   │   │   ├── int_claim_object_facts.sql
│   │   │   │   # 将探测结果左关联回每条材料记录，并把缺少探测结果视为对象不可用。
│   │   │   ├── int_claim_object_hash_udf.sql
│   │   │   │   # 通过直接 Runner SQL，为每个可用对象计算作为内容身份的 SHA-256。
│   │   │   ├── int_claim_photo_quality_udf.sql
│   │   │   │   # 通过直接 Runner SQL，检查可用 JPEG 损伤照片的可读性、可用性和质量。
│   │   │   ├── int_claim_document_ocr_udf.sql
│   │   │   │   # 通过直接 Runner SQL，对每份可用 PNG 证明文档调用 OCR Actor 或 Local Lookup。
│   │   │   ├── int_claim_document_fields_udf.sql
│   │   │   │   # 通过直接 Runner SQL，从每个规范化 OCR 响应中提取规则要求的字段。
│   │   │   ├── int_claim_document_quality_inputs.sql
│   │   │   │   # 绑定 OCR、提取字段、Claim 身份、必需字段和最低置信度。
│   │   │   ├── int_claim_document_quality_udf.sql
│   │   │   │   # 通过直接 Runner SQL 评估绑定后的文档合同，并输出文档可用性结果。
│   │   │   ├── int_claim_material_facts.sql
│   │   │   │   # 关联所有 UDF 输出，聚合为每个 Claim 一行，并为 AI 构造有序且已验证的照片输入。
│   │   │   ├── int_claim_damage_validation_inputs.sql
│   │   │   │   # 展开已验证照片输入，并把每个 Vane AI 响应绑定到可信 claim、file 和 SHA-256 身份。
│   │   │   ├── int_claim_damage_validation_udf.sql
│   │   │   │   # 通过直接 Runner SQL，规范化并严格校验每个不可信的损伤模型响应。
│   │   │   ├── int_claim_damage_facts.sql
│   │   │   │   # 对 Runner 校验后的逐照片结果分类并按 Claim 聚合，同时识别冲突、不确定性和严重程度风险。
│   │   │   └── int_claim_decision_facts.sql
│   │   │       # 生成四种确定性分流候选，并应用明确的规则优先级。
│   │   │
│   │   └── marts/
│   │       └── claim_disposition.sql
│   │           # 将命中的规则映射为最终九列合同，包括 disposition、原因、下一步动作和 supporting_facts_json。
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

执行主线是 `run_demo.py → cli.py → pipeline.py → Vane Function/Actor/AI → SQL Relations → output_writer.py → verify_outputs.py`。Driver 读取 PostgreSQL/MinIO、持有纯 SQL DuckDB Catalog 并发布输出。对于每个 `*_udf.sql` 投影，`pipeline.py` 将 Driver 输入临时落为 Parquet，通过所选 Vane Runner 执行投影，再把物化结果注册回 Driver Catalog。Local 使用 Driver 持有的一份 OCR 实现和一份复用的 Vane Provider Prompter，生成 OCR 查询与 AI 响应表；Ray 挂载 OCR Actor，并通过 `vane.ai.prompt` 执行 AI。两条路径保持相同的 SQL 节点和类型化合同。

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
- [本地 Qwen2.5-VL 服务搭建指南](../docs/local-qwen-service.zh.md)
- [英文架构图](docs/vane-claims-data-flow.en.png)

所有理赔记录、文档和标识符均为合成数据。不要提交真实理赔记录、客户照片、私人文档、生产凭据、模型权重或运行生成数据。
