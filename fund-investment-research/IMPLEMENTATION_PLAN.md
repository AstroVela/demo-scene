# 基金投研场景 Demo 落地计划

## 1. 项目目标

本 Demo 以“可核验的多模态投资假设监控”为唯一业务主线：从内部投研会议录音和历史研究材料中还原研究事实，将新公告、研究材料或市场信息关联到已经由研究员确认的投资假设，最终输出带原始证据、影响链和人工复核任务的投研信号。

首版只提供一个小型、可复现的 E2E 案例，不建设通用投研问答产品，也不包含性能对比或 Benchmark。

核心业务闭环为：

```text
历史会议录音、研报和已确认投资假设
  → 专业语音转写与领域纠错
  → 带来源位置的标准化投研事实
  → 新公告或外部信号事实
  → 关联历史假设及判断条件
  → 构建支持、偏离、冲突或证据不足的影响链
  → 输出投研信号状态和研究员复核任务
```

Demo 必须验证以下四种预期状态：

1. `thesis_review_required`
2. `thesis_supported`
3. `manual_review`
4. `insufficient_evidence`

这些状态是研究工作流状态，不是买入、卖出、持仓调整或最终投资判断。

### 1.1 四项直接业务价值的 Demo 契约

Demo 不要求在首版量化研究员工时、交付人天或提升比例，但每项直接业务价值都必须对应一个能够实际运行、能够查看产物、能够指向代码的演示动作。

| 直接业务价值 | 必须能够执行的动作 | 现场可见产物 | `verify` 核心断言 |
|---|---|---|---|
| 提高投研知识生产的质量与时效 | 将一个会议片段从原始 ASR 结果推进到领域纠错、质量门禁和知识处置 | 带时间戳的原始/纠正转写、纠错记录、知识状态 | 至少一项专业术语修订可追溯；不确定数字未被静默修改 |
| 提高风险与机会信号分析的速度与深度 | 将一条新信号展开到历史观点、假设条件、支持证据、反向证据和不确定项 | 面向研究员的信号证据报告及原始资料位置 | 影响链引用有效事实，并同时保留适用的正向、反向或冲突证据 |
| 让 AI 从“从头核验”转为“定向核验” | 区分原始事实、已确认假设、模型影响假设和未解决不确定性，只生成聚焦的复核任务 | 类型明确的知识项、证据状态和 `review_tasks` | 研究员待办均能定位到具体判断、证据和原始位置 |
| 提高投研方法的落地速度 | 仅在 `domain_terms` 增加一个专业词别名并重新运行，不修改 Pipeline 代码 | 变更前后的纠错与知识处置差异 | 词表数据变更能够改变目标结果，且主体 Pipeline 与输出合同不变 |

这些业务改善并非 Vane 独占，也依赖模型、数据、研究方法和研究员参与。Demo 的第二层叙事是说明 Vane 如何用统一 Relation、显式合同、质量门禁、血缘、恢复和发布机制，使上述改善更容易稳定复现和持续扩展。

## 2. 首版范围

### 2.1 纳入范围

- PostgreSQL 中的公司、投资假设、假设条件、行业词表、资料元数据和新信号。
- MinIO 中的 WAV 会议录音、PDF 研究材料和 PNG 截图或扫描件。
- 真实 ASR、OCR 和大模型调用，不提供运行时 Mock 降级。
- 原始转写、领域纠错、关键术语质量评测和低置信度分流。
- 可直接查看的原始/纠正转写、逐项纠错记录和资料处理状态。
- 文档事实抽取、实体标准化、来源身份绑定和严格 AI 响应校验。
- 事件、指标、假设条件和投资假设之间的可追溯影响链。
- 原始事实、已确认假设、模型影响假设和未解决不确定性的显式区分。
- 面向研究员的信号证据报告，汇总支持证据、反向证据、适用条件、原始位置和待核验项。
- SQL 确定性状态计算及人工复核任务生成。
- 一个数据驱动的专业词表现场变更场景。
- 基于来源哈希和阶段版本的故障隔离及新建、变化、失败资料局部重算场景。
- 普通 Python 业务函数通过薄 Vane Function、Batch UDF 或 Actor 适配层接入。
- 全部正式 Demo 执行只使用 `ray` Runner；不保留 Local Runner 分支。
- `fixture`、`run`、`verify`、`e2e` 四个 CLI 命令。
- 中英文 README、运行手册、Demo 演示手册、数据流程图和 SQL DAG 图。

### 2.2 不纳入范围

- 性能压测、可运行的串行 Python 对照 Pipeline、资源利用率采集和 Benchmark 命令。文档可以说明脚本方案若实现同等能力需要补充哪些编排逻辑。
- 智能问数。
- 通用聊天界面或 Web UI。
- 完整向量数据库、通用 RAG 平台或在线知识库产品。
- 实时互联网爬虫和外部行情接入。
- 自动生成买卖建议、目标价、仓位或组合调整方案。
- 真实 DolphinScheduler 部署；文档中只说明其可作为外层触发器。
- 自动修改研究员已经确认的投资假设。

## 3. 合成业务案例

### 3.1 公司与历史投资假设

首版只使用一家完全虚构的医药公司，避免因为公司数量扩大而分散主线。

建议使用：

- 公司：澜星生物
- 公司代码：`SYN-BIO-001`
- 核心产品：`LX-101`
- 产品类型：Nectin-4 ADC
- 行业：创新药

由研究员确认并版本化保存的历史投资假设包含四项条件：

| 条件 ID | 条件 | 判断方式 |
|---|---|---|
| `COND-EFFICACY` | Ⅱ期试验 ORR 不低于 40% | 数值下限 |
| `COND-SAFETY` | 3 级及以上 TRAE 不高于 35% | 数值上限 |
| `COND-RUNWAY` | 现金可支撑时间不低于 18 个月 | 数值下限 |
| `COND-REGULATORY` | 关键注册申报按计划推进 | 定性条件，出现冲突时必须人工复核 |

内部投研会议录音和历史研报中应出现 Nectin-4、ADC、ORR、TRAE、DOR、PFS、BLA 等专业术语，用于验证通用 ASR 与领域纠错流程。

### 3.2 四条新信号

| Signal ID | 输入材料 | 核心事实 | 预期状态 |
|---|---|---|---|
| `SIG-CLINICAL` | 可信公司临床公告 | ORR 为 29%，3 级及以上 TRAE 为 43%，同时存在一个不足以改变整体结论的小样本亚组正向结果 | `thesis_review_required` |
| `SIG-RUNWAY` | 可信审计或中期财务材料 | 现金可支撑时间为 24 个月 | `thesis_supported` |
| `SIG-REGULATORY` | 公司正式材料与专家交流纪要 | 两个可信来源对注册申报是否延期给出不兼容描述 | `manual_review` |
| `SIG-RUMOR` | 企业微信群截图 | 声称产品被临床暂停，但无原始公告或其他可信来源 | `insufficient_evidence` |

`thesis_supported` 只表示这条新信号支持其所关联的具体假设条件，不代表整家公司或完整投资逻辑已经被证明成立。

### 3.3 合成资产

计划准备以下小型资产：

- 1 份内部投研会议 WAV 录音。
- 1 份历史研究报告 PDF。
- 1 份临床结果公告 PDF。
- 1 份中期财务材料 PDF。
- 1 份注册进度公司材料 PDF。
- 1 份专家交流纪要 PDF 或扫描图片。
- 1 份低可信企业微信群截图 PNG。
- 1 份只供评测程序使用的标准转写和专业术语标注文件。

全部内容必须为合成数据。音频应使用许可允许再分发的 TTS 方案生成，或由明确同意公开使用的人员录制，并为音频、字体、图片和生成工具补充 NOTICE。标准转写和预期结果不能作为正式 Pipeline 的运行输入。

### 3.4 可演示动作与场景变体

默认 Fixture 除了产生四种信号状态，还必须稳定包含以下可观察行为：

- 至少一个专业术语从原始 ASR 文本修订为规范词，并绑定原始片段、词表 ID、原因和置信度。
- 至少一个无法可靠确认的数字或低置信度片段进入人工复核，不能被模型静默改写。
- 临床信号同时保留主要负向证据和不足以改变整体判断的小样本正向证据。
- 监管信号保留两个可信来源的冲突，不由模型强行合并为单一事实。
- 群聊传闻能够进入核验队列，但不能独立驱动自动支持或偏离状态。

现有四个顶层命令保持不变，通过可选参数提供两个独立演示变体：

- `glossary-change`：在 PostgreSQL `domain_terms` 中增加一个别名；目标纠错和知识处置发生变化，主体 Pipeline、Prompt、SQL 和输出合同不变。
- `recovery-fault` / `recovery-fixed`：使用真实损坏/修复的合成资料或不匹配哈希，展示隔离、失败状态、恢复后只选择新建、变化或失败的阶段输入。禁止使用固定结果替代真实处理。

变体不得改变默认 E2E 的四条信号和预期状态，也不能把标准转写或预期输出注入正式事实路径。

## 4. 数据合同

### 4.1 PostgreSQL 原始表

| 表 | 粒度与用途 |
|---|---|
| `companies` | 每家公司一行，保存规范名称、行业和别名 |
| `investment_theses` | 每个经研究员确认的投资假设版本一行 |
| `thesis_conditions` | 每个可判断条件一行，保存指标、运算符、阈值、单位和有效期 |
| `domain_terms` | 每个专业术语一行，保存规范词、别名、类别和适用范围 |
| `source_files` | 每个多模态资料一行，保存来源角色、可信等级、时间、媒体类型及 MinIO locator |
| `incoming_signals` | 每条待分析的新信号一行，并关联一个或多个来源文件 |

`thesis_conditions` 应使用类型明确的列保存运算符、阈值和单位，避免把核心判断逻辑隐藏在自由文本或 JSON 中。

### 4.2 MinIO 对象合同

PostgreSQL 中的 `bucket/object_key` 是唯一可信 locator。每个对象在进入处理前必须校验：

- bucket 和 object key 是否符合规范。
- 媒体类型是否与来源角色一致。
- 对象是否存在且可读取。
- SHA-256 是否与元数据一致。
- 文件是否可正常解码或解析。
- 来源时间和可信等级是否完整。

正式 Pipeline 不直接读取 `fixtures/` 下的本地文件。

### 4.3 标准化投研事实

统一事实 Relation 建议至少包含：

- `fact_id`
- `company_id`
- `signal_id`
- `source_id`
- `fact_type`
- `entity_id`
- `metric_code`
- `value_numeric`
- `value_text`
- `unit`
- `period_start`
- `period_end`
- `source_quote`
- `source_locator`
- `knowledge_kind`
- `trust_tier`
- `confidence`
- `extraction_method`
- `model_version`
- `pipeline_version`
- `review_required`

`source_locator` 对音频使用时间范围，对 PDF 使用页码和文本位置，对图片使用文件及区域描述。

在 `research_facts` 中，`knowledge_kind` 只允许：

- `source_fact`：能够直接定位到原始资料的事实。
- `uncertainty`：证据不足、互相冲突或需要研究员判断的事项。

已确认假设继续保存在 `investment_theses` / `thesis_conditions`，模型影响假设继续保存在 `thesis_impact_edges`，不伪装成事实行。面向研究员的证据包将这些 Relation 组合为统一知识项，并标记 `source_fact`、`approved_thesis`、`model_hypothesis` 或 `uncertainty`。

影响边和证据包中的 `evidence_status` 至少包含 `supported`、`contradicted`、`unresolved` 和 `not_applicable`。模型不能把 `model_hypothesis` 升级为 `source_fact`，也不能把 `unresolved` 自动改写为确定结论。

### 4.4 阶段身份与处理状态

为展示故障隔离和局部重算，工作 Schema 中增加持久化的阶段状态 Relation。唯一身份为：

```text
source_id + source_sha256 + stage + stage_version
```

建议至少包含：

- `run_id`
- `source_id`
- `source_sha256`
- `stage`
- `stage_version`
- `status`：`pending`、`succeeded`、`quarantined` 或 `failed`
- `error_code`
- `attempt`
- `result_locator`
- `started_at`
- `completed_at`

`result_locator` 指向已经通过该阶段合同的中间结果，不能包含凭据。恢复运行通过输入与已成功阶段身份的 anti join，只选择新建、内容哈希变化、阶段版本变化或失败的资料。任何缓存复用都必须重新验证主键、哈希、Schema 与阶段版本，不能仅按文件名判断。

## 5. 逻辑 Pipeline

### 5.1 来源注册与质量门禁

1. 从 PostgreSQL 读取完整业务快照。
2. 校验主键、外键、枚举、时间和假设条件。
3. 根据可信 locator 访问 MinIO。
4. 校验对象存在性、哈希、媒体类型和可解析性。
5. 为每份资料形成阶段身份和结构化处理状态。
6. 不符合来源合同的资料不得进入自动分析，并写入隔离或失败原因。

来源级数据质量问题与系统级失败必须分开处理：

- 单份资料哈希不匹配、媒体损坏、低质量或合同不完整时，将该资料路由到 `quarantined`/`failed` 状态并阻止其进入自动事实路径；已经完成的其他资料阶段结果可以保留供恢复运行使用。
- PostgreSQL、MinIO、模型服务整体不可用，SQL 执行失败或发布失败时，整次运行非零退出，不发布不完整的新快照。

#### 5.1.1 恢复与局部重算选择

恢复运行不依赖隐式文件存在判断，而是使用来源哈希和阶段版本选择范围：

```text
当前有效输入
  LEFT ANTI JOIN
已成功阶段结果
  ON source_id + source_sha256 + stage + stage_version
  → 新建、变化、升级或失败的待处理 Relation
```

成功中间结果与恢复后新结果重新进入相同的下游合同校验和原子发布流程。默认 E2E 仍从干净状态完整运行；恢复演示通过 `run` 的可选参数触发，不增加新的顶层 CLI 命令。

### 5.2 音频分支

```text
可信 WAV
  → 音频解码和基础质量检查
  → 状态化 ASR Actor
  → 原始分段转写和时间戳
  → 专业词表及会议上下文纠错
  → 纠错合同校验
  → 关键术语、数字和低置信度片段
  → 会议事实候选
```

实现要求：

- ASR Actor 延迟初始化并复用同一个模型实例。
- `runtime.yml` 配置 ASR 模型路径、设备、计算精度、语言和批大小。
- 保留原始文本与修订文本，不能只保存最终纪要。
- 每项自动修订记录原词、规范词、词表 ID、原因和置信度。
- 不允许模型无依据改写关键数字；无法确认时必须标记人工复核。
- 标准转写只用于评测原始与纠正后的专业术语质量，不参与事实生成。
- 将分段原始/纠正文本和逐项纠错记录分别发布为可直接检查的产物。

### 5.3 文档与图片分支

```text
可信 PDF 或 PNG
  → 文本提取或 OCR
  → 文档类型与可读性判断
  → 结构化事实抽取
  → 严格 AI JSON 合同
  → 来源角色、公司身份和文件哈希重新绑定
  → 文档事实候选
```

AI 事实输出必须包含原文证据、来源位置、置信度和 `knowledge_kind`；影响关系候选必须包含 `evidence_status`。模型返回的公司、文档类型和来源身份不能覆盖 PostgreSQL 中的可信身份；模型生成的解释只能进入影响边中的 `model_hypothesis` 或事实候选中的 `uncertainty`，不能伪装为 `source_fact`。

### 5.4 统一事实与冲突识别

音频和文档候选事实进入同一 Schema 后，使用 SQL 完成：

- 公司、产品、指标和单位标准化。
- 同一时间范围内相同指标事实的聚合。
- 不同来源之间的重复、支持和冲突识别。
- 低可信来源隔离。
- 关键事实缺失检查。
- 为后续证据包组合准备跨 Relation 的原始事实、已确认假设、模型影响假设和未解决不确定性类型约束。

### 5.5 假设条件关联与影响链

影响链采用明确的关系类型：

```text
signal_event
  → changes
business_metric
  → supports / violates / conflicts_with
thesis_condition
  → strengthens / weakens / requires_review
investment_thesis
```

模型可以生成关系候选和解释草稿，但最终边必须满足：

- 关联到已存在的公司、指标和假设条件。
- 至少引用一条通过质量门禁的事实。
- 保留支持证据和反向证据。
- 明确关系是确定性规则、模型候选还是人工确认。
- 不能把相似性或模型解释伪装成已经证明的因果事实。

### 5.6 状态判定

最终状态由 SQL 生成，建议优先级如下：

1. 缺少可信事实、来源不合格或关键质量门禁未通过：`insufficient_evidence`
2. 可信事实互相冲突，或定性关系无法确定：`manual_review`
3. 可信事实明确违反强制假设条件：`thesis_review_required`
4. 可信事实明确支持所关联的假设条件，且无冲突：`thesis_supported`

如果同一信号关联多个条件，只要存在无冲突的强制条件偏离，整体状态为 `thesis_review_required`。模型不得直接输出最终状态。

### 5.7 面向研究员的定向核验包

在标准化事实、影响边和 SQL 状态生成后，构造一份只读的证据包 Relation，每条信号至少汇总：

- 新信号及其可信来源。
- 关联的历史投资假设及版本。
- 关键事实、指标和假设条件。
- 支持证据、反向证据或可信冲突。
- 模型提出的影响假设及其适用条件。
- 尚未解决的不确定项。
- PDF 页码、图片区域或录音时间点。
- 需要研究员处理的聚焦任务和下一步动作。

该 Relation 用于生成 `signal_evidence_report.md`。报告是 JSONL/SQL 结果的可读投影，不重新调用模型，也不能产生不同于 `research_signals` 的状态。

## 6. 输出合同

首版发布以下文件：

| 输出 | 内容 |
|---|---|
| `transcript_segments.jsonl` | 带时间戳、ASR 置信度、原始文本和纠正文本的会议分段 |
| `asr_corrections.jsonl` | 每项专业词修订的原词、规范词、词表 ID、原因和置信度 |
| `research_facts.jsonl` | 通过合同校验的标准化投研事实 |
| `thesis_impact_edges.jsonl` | 事件、指标、条件和投资假设之间的证据关系 |
| `research_signals.jsonl` | 每个 `signal_id × thesis_id` 的状态、理由、优先级和下一步动作 |
| `review_tasks.jsonl` | 需要研究员处理的术语、事实、冲突和假设复核任务 |
| `source_processing_status.jsonl` | 每份资料在各阶段的身份、状态、错误原因、尝试次数和结果位置 |
| `asr_quality_metrics.json` | 原始与领域纠错后的专业术语和关键数字评测结果 |
| `run_manifest.json` | 输入哈希、模型、词表、Prompt/Schema、规则、假设、Pipeline 和阶段版本，以及 Relation 行数和输出哈希 |
| `signal_evidence_report.md` | 面向研究员的信号、假设、正反证据、不确定项、原始位置和待办汇总 |

`output_writer.py` 必须在写入前校验：

- 主键唯一。
- 枚举合法。
- 数值和单位完整。
- 各 Relation 中的 `knowledge_kind` 和 `evidence_status` 取值及组合合法。
- 所有证据引用指向真实的 `source_id` 和 `fact_id`。
- 所有影响边引用有效的假设条件。
- 低可信信源没有单独驱动自动状态。
- 四个核心输出之间的交叉引用完整。
- Markdown 证据报告与结构化 `research_signals`、影响边及复核任务一致，不能二次推导不同状态。

发布采用临时文件加原子替换，任一输出失败时不能留下部分新快照。

## 7. E2E 命令与预期行为

CLI 设计与现有两个 Demo 保持一致：

```bash
.venv/bin/python scripts/run_demo.py fixture
.venv/bin/python scripts/run_demo.py run
.venv/bin/python scripts/run_demo.py verify
.venv/bin/python scripts/run_demo.py e2e
```

各命令职责：

- `fixture`：校验本地合成 seed，将业务表写入 PostgreSQL，将多模态资产写入 MinIO。
- `run`：只从 PostgreSQL 和 MinIO 读取输入，运行真实 ASR、OCR、AI、SQL 和发布流程。
- `verify`：读取已发布结果并验证四种预期状态、证据引用和关键质量条件。
- `e2e`：依次执行 fixture、run 和 verify。

四个顶层命令不变，使用可选参数执行演示动作。建议固定以下剧本：

```bash
# 词表数据变更，不修改 Pipeline 代码
.venv/bin/python scripts/run_demo.py fixture --scenario glossary-before
.venv/bin/python scripts/run_demo.py run
.venv/bin/python scripts/run_demo.py verify --scenario glossary-before
.venv/bin/python scripts/run_demo.py fixture --scenario glossary-after
.venv/bin/python scripts/run_demo.py run
.venv/bin/python scripts/run_demo.py verify --scenario glossary-after

# 真实坏输入、修复和局部恢复
.venv/bin/python scripts/run_demo.py fixture --scenario recovery-fault
.venv/bin/python scripts/run_demo.py run                  # 预期非零退出并保留阶段状态
.venv/bin/python scripts/run_demo.py fixture --scenario recovery-fixed
.venv/bin/python scripts/run_demo.py run --resume
.venv/bin/python scripts/run_demo.py verify --scenario recovery-fixed
```

`recovery-fixed` 只能修复目标合成资料及可信元数据，必须保留前一次运行已经成功的阶段状态，才能验证 `--resume` 的选择范围。所有场景参数都必须是幂等的。

默认、`glossary-before`、`glossary-after` 和恢复演示使用相互隔离的运行标识及输出目录。词表变更验证读取前后两个 manifest/输出快照进行对照；恢复演示保留故障运行的工作阶段状态，但不能覆盖最近一次成功发布的正式快照。

除四条最终信号状态外，`verify` 还应输出可读的业务价值断言：

```text
PASS: at least one domain correction is traceable to its raw audio span
PASS: uncertain numbers were not silently rewritten
PASS: the clinical signal retains applicable supporting and opposing evidence
PASS: source facts, approved theses, model hypotheses and uncertainty are separated
PASS: every review task points to a judgment, evidence and original locator
PASS: a low-trust rumor did not drive an automatic thesis state
PASS: final signal states were produced by deterministic SQL
PASS: the resume scope contains only new, changed or previously failed stage inputs  # recovery scenario only
```

默认 E2E 对恢复专属断言输出 `SKIP` 而不是伪造 PASS；只有完成故障和修复步骤后才校验恢复范围。

成功输出建议为：

```text
loaded 1 company, 4 thesis conditions, 7 source files, and 4 signals
published 4 research signals, 14 facts, and 4 review tasks
verified 4 research signals:
SIG-CLINICAL=thesis_review_required
SIG-RUNWAY=thesis_supported
SIG-REGULATORY=manual_review
SIG-RUMOR=insufficient_evidence
```

服务不可用、音频无法解码、OCR 失败、AI JSON 不符合合同、SQL 失败或发布失败时，命令必须非零退出。来源级故障可以先记录已经成功的阶段状态，但不得发布不完整的新快照；不得静默使用固定答案继续运行。

## 8. 运行配置

`runtime.yml` 计划包含：

- `runner`：固定为 `ray`，其他值配置校验失败
- `output_dir`
- PostgreSQL DSN、原始 Schema 和表名
- MinIO endpoint、bucket 和凭据
- ASR backend、模型路径、device、compute type、language、batch size
- OCR backend、device 和最低置信度
- AI provider、base URL、model、并发、超时、温度和 token 上限
- 事实与关系置信度门槛
- 允许自动使用的最低信源等级
- PostgreSQL 工作 Schema、阶段结果表和恢复开关
- ASR、OCR、事实抽取、关系生成和规则计算的独立 `stage_version`
- Prompt、AI JSON Schema、词表、规则和 Pipeline 版本

密钥不能进入 SQL Relation、日志、输出文件或 `run_manifest.json`。

## 9. 计划目录结构

```text
fund-investment-research/
├── README.md
├── README.zh-CN.md
├── IMPLEMENTATION_PLAN.md
├── pyproject.toml
├── requirements.txt
├── runtime.yml
├── queries.sql
├── fixtures/
│   └── synthetic-biotech-thesis/
├── scripts/
│   └── run_demo.py
├── docs/
│   ├── runbook.md
│   ├── runbook.zh-CN.md
│   ├── demo-walkthrough.md
│   ├── demo-walkthrough.zh-CN.md
│   ├── vane-fund-research-data-flow.*
│   └── vane-fund-research-sql-dag.*
├── src/fund_investment_research/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── contracts.py
│   ├── fixture_loader.py
│   ├── demo_scenarios.py
│   ├── pg.py
│   ├── minio_store.py
│   ├── source_data.py
│   ├── asr.py
│   ├── ai.py
│   ├── domain_logic.py
│   ├── vane_functions.py
│   ├── pipeline.py
│   ├── stage_state.py
│   ├── evidence_report.py
│   ├── output_writer.py
│   ├── verify_outputs.py
│   └── sql/
│       ├── staging/
│       ├── intermediate/
│       └── marts/
└── tests/fast/
```

## 10. 实施阶段

### 阶段一：冻结 Fixture 与输出合同

任务：

- 完成四条信号的全部合成文本、指标和预期状态。
- 确定投资假设条件的类型化 Schema。
- 确定所有来源角色和可信等级。
- 确定四项直接业务价值的演示动作和 `verify` 断言。
- 确定 `knowledge_kind`、`evidence_status` 和聚焦复核任务合同。
- 确定输出表字段、状态优先级和人工任务规则。
- 确定默认、词表变更和故障恢复三个 Fixture 场景。
- 完成音频生成方式和许可证审查。

完成标准：

- 四条信号可以完全依靠明确事实和规则解释。
- 预期状态不依赖模型自由发挥。
- 每条预期状态都有对应的正向和失败路径样本。
- 四项直接业务价值均能对应到具体输入、代码路径、输出和机械断言。

### 阶段二：项目骨架与运行配置

任务：

- 创建 Python Package、Launcher、CLI 和严格配置模型。
- 将普通 Python 领域逻辑与 Vane Function、Batch UDF、Actor 适配层分离。
- 固定与现有 Demo 一致的 Python 和 Vane 版本策略。
- 实现 PostgreSQL、MinIO 和模型服务预检。
- 建立 `fixture/run/verify/e2e` 命令框架及场景、恢复可选参数。

完成标准：

- 配置错误和服务不可用能够一次性给出不含凭据的错误。
- 配置只接受 Ray；ASR、OCR、词表 Function 和 AI Prompt 均走 Ray 执行路径。
- 至少一个普通 Python 函数可通过薄适配层进入 Vane Pipeline，且业务逻辑不重复实现。

### 阶段三：Fixture 装载与来源合同

任务：

- 生成并校验 PostgreSQL seed 数据。
- 上传 WAV、PDF 和 PNG 到 MinIO。
- 实现对象存在性、哈希、媒体类型和 locator 校验。
- 形成类型明确的 Arrow SourceBundle。
- 实现阶段身份、持久化处理状态、结果 locator 和恢复选择 Relation。
- 实现词表变更、故障注入和修复场景的幂等 Fixture 操作。

完成标准：

- Pipeline 运行时不读取本地 fixture。
- 错误哈希、错误媒体类型、缺失对象和非法外键均失败关闭。
- 已成功阶段结果只能在来源哈希、Schema 和阶段版本均匹配时复用。

### 阶段四：专业语音处理

任务：

- 实现状态化 ASR Actor。
- 实现分段转写及时间戳合同。
- 实现词表和会议上下文纠错。
- 实现纠错响应校验和低置信度复核任务。
- 实现只读标准转写评测。
- 发布可检查的原始/纠正分段与逐项纠错记录。

完成标准：

- 原始和纠正后的转写都可追溯。
- 关键数字不会被无依据自动修改。
- 评测文件不会进入正式事实处理路径。
- 词表别名数据变更能够改变目标纠错结果，不需要修改主体 Pipeline。

### 阶段五：文档处理与事实抽取

任务：

- 实现 PDF 文本提取、图片 OCR 和质量门禁。
- 构造带可信元数据的 AI 请求。
- 实现严格事实 JSON 合同及重试边界。
- 将模型事实重新绑定到可信公司、文件和哈希。
- 对原始事实、模型影响假设和不确定项执行类型约束。

完成标准：

- 所有被接受事实都有原文证据和来源位置。
- 文档类型、公司身份和来源角色不能由模型覆盖。
- 模型解释不能被发布为原始事实，未解决事项不能被自动升级为确定结论。

### 阶段六：事实关联、影响链和 SQL 状态

任务：

- 合并音频与文档事实。
- 实现实体、指标、单位和时间标准化。
- 实现冲突识别和低可信信源隔离。
- 实现假设条件关联和影响边。
- 实现四种状态的确定性 SQL 优先级。
- 构造研究员证据包 Relation 和只读 Markdown 报告。

完成标准：

- 模型不直接产生最终状态。
- 任一 `research_signal` 可以展开到条件、事实和原始资料位置。
- 四条 Fixture 信号产生四种预期状态。
- 证据报告明确区分事实、已确认假设、模型影响假设和不确定项，并与结构化状态一致。

### 阶段七：发布、验证与查询

任务：

- 实现全部输出合同和原子发布。
- 实现 `verify_outputs.py`。
- 编写 `queries.sql`，展示事实、影响链、信号和人工任务。
- 确保失败运行不会污染上一次成功快照。
- 实现故障隔离、阶段状态保留和 `--resume` 局部重算。
- 让 `verify` 输出四项业务价值及恢复范围的可读断言。

完成标准：

- `e2e` 一条命令完成装载、运行、发布和验证。
- 验证程序检查状态之外，还检查证据引用、低可信来源隔离和影响边完整性。
- 恢复场景只选择新建、变化或失败的阶段输入，修复后仍通过同一完整发布校验。

### 阶段八：文档与仓库接入

任务：

- 编写中英文 README 和运行手册。
- 编写中英文 Demo walkthrough，将每个价值点映射到现场动作、代码、输出和脚本方案需要额外承担的工程工作。
- 绘制数据流程图和 SQL DAG。
- 记录模型、TTS、字体和合成资产来源。
- 在仓库根 README 中增加 Use Case 入口。
- 检查生成结果、模型权重和凭据不会进入 Git。

完成标准：

- 新用户可以仅根据运行手册完成环境准备和 E2E。
- README 清楚说明场景边界，不把输出描述为投资建议或真实因果结论。
- Walkthrough 不声称业务价值由 Vane 独占，也不依赖不存在的 Python 对照实现或未生成的产物。

## 11. 验证策略

后续实现阶段应准备以下验证，但本计划阶段不运行测试：

- 配置、来源合同和枚举的快速单元测试。
- ASR 响应、纠错合同和专业术语评测测试。
- 原始/纠正转写、逐项修订及不确定数字分流测试。
- OCR、AI 事实合同和可信身份重新绑定测试。
- `knowledge_kind`、`evidence_status` 以及模型假设不能升级为事实的测试。
- 四种 SQL 状态及优先级测试。
- 低可信来源、证据冲突、缺失事实和无效模型 JSON 失败路径。
- 词表仅数据变更即可改变目标纠错结果的演示测试。
- 阶段身份、缓存复用条件、故障隔离和仅重算新建/变化/失败输入的恢复测试。
- 输出原子性和交叉引用测试。
- Markdown 证据报告与结构化信号、影响边和复核任务一致性测试。
- Ray Function、Actor、图片 Prompt 和 SQL 物化的真实服务集成测试。
- 真实服务 E2E 验收。

快速测试可以注入确定性测试替身，但正式 `run/e2e` 命令不能存在运行时 Mock fallback。

## 12. 主要风险与控制

| 风险 | 控制方式 |
|---|---|
| 不同 ASR 模型或硬件导致全文转写不完全一致 | E2E 验证关键术语、数字和下游合同，不要求全文逐字相同；固定模型版本和解码参数 |
| 领域纠错模型产生幻觉 | 修订必须绑定原始片段和词表；关键数字只能确认或进入人工复核 |
| 大模型事实抽取存在波动 | 温度设为 0，使用严格 JSON Schema、可信身份绑定和有限重试 |
| “因果分析”表述过度 | 统一使用事件影响链、证据关系和因果假设，不宣称证明真实因果 |
| 低可信信息污染历史结论 | 可信等级准入；群聊截图只能产生 `insufficient_evidence` 或核验任务 |
| 合成音频或字体许可证不清 | 资产进入仓库前完成许可证审查并添加 NOTICE |
| Pipeline 自动改变研究员观点 | 已确认假设只读且版本化，系统只生成状态和复核任务 |
| 恢复运行错误复用过期中间结果 | 阶段身份同时绑定来源哈希、Schema 和阶段版本；复用后重新执行下游合同与发布校验 |
| Demo walkthrough 宣称了代码中不存在的能力 | `verify` 为每个演示价值提供机械断言；walkthrough 只引用实际函数、Relation、SQL 和已发布产物 |
| 把业务改善错误表述为 Vane 独占能力 | 演示先说明业务结果，再单独说明 Vane 在合同、治理、恢复和复用上的平台增量 |

## 13. 完成定义

满足以下条件后，首版 Demo 才算完成：

- `.venv/bin/python scripts/run_demo.py e2e` 可以从干净服务状态运行成功。
- 正式运行只读取 PostgreSQL 和 MinIO，不读取本地 fixture。
- 使用真实 ASR、OCR 和 AI 服务，无静默 Mock 降级。
- 精确发布并验证四条信号及四种预期状态。
- 每条非 `insufficient_evidence` 信号均能追溯到事实、影响边和原始资料位置。
- 原始与纠正后的专业语音结果、逐项修订记录、处理状态和质量指标均作为可检查产物保留。
- 原始事实、已确认假设、模型影响假设和未解决不确定性在合同和报告中明确区分。
- 每条信号都有与结构化状态一致的研究员证据报告，聚合正反证据、适用条件、原始位置和待办。
- 低可信来源不能单独触发 `thesis_supported` 或 `thesis_review_required`。
- 模型不直接决定最终状态。
- 词表别名只通过数据变更即可改变目标纠错结果，无需修改主体 Pipeline 或输出合同。
- 故障场景能够保留合法阶段状态；修复后的 `--resume` 只选择新建、变化或失败的阶段输入。
- 普通 Python 领域函数通过薄 Vane 适配层接入，业务逻辑没有在适配层重复实现。
- 正式 Demo 中不存在 Local Runner 路径，所有 Vane 计算均使用 Ray Runner。
- 输出采用原子发布，失败运行不覆盖上一次成功结果。
- `verify` 为四项直接业务价值和恢复范围输出明确的 PASS/FAIL 断言。
- README、运行手册、Demo walkthrough、图示、数据许可证和仓库入口完整。
- Walkthrough 中每个非性能优势均能指向真实动作、函数、Relation、SQL 或输出，并仅以文字说明脚本方案需要额外承担的工程工作。
- 项目中不存在 Benchmark、性能对比或资源成本采集代码。
- 项目中不存在可运行的 Python 对照 Pipeline。
