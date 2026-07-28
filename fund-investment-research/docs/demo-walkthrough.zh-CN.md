# 基金投研 Demo 现场演示手册

## 演示目标

这套讲解不展示性能数字，也不要求把业务价值量化成提升比例。每个优势都必须落到一个真实执行动作、一段代码逻辑和一个可查看产物。

建议先完成默认 E2E：

```bash
.venv/bin/python scripts/run_demo.py e2e
```

然后保持 `output/default/current` 可供现场查看。

## 0. 先讲清楚边界（1 分钟）

开场可以直接说：

> 这是研究工作流状态，不是投资结论。模型只抽取资料中的事实和提出影响关系候选；公司、来源和可信等级来自 PostgreSQL，最终状态来自 SQL。整个 Demo 只跑 Ray Runner，并且使用支持图片输入的本地 Vane。

指向：

- [`scripts/run_demo.py`](../scripts/run_demo.py)：本地 Vane、图片 API 和 Ray 能力检查；
- [`ai.py`](../src/fund_investment_research/ai.py)：`image_columns=["image_bytes"]` 和严格响应合同；
- [`research_signals.sql`](../src/fund_investment_research/sql/research_signals.sql)：最终状态优先级；
- [`run_manifest.json`](../output/default/current/run_manifest.json)：实际 Runner、模型和版本身份。

## 1. 投研知识生产：从原始录音到可追溯知识（3 分钟）

### 现场动作

```bash
sed -n '1,3p' output/default/current/transcript_segments.jsonl
sed -n '1,20p' output/default/current/asr_corrections.jsonl
cat output/default/current/asr_quality_metrics.json
```

重点指出：

1. Whisper 原始文本仍保留；
2. `LX101 → LX-101`、`actin-4 → Nectin-4` 等修订都有原始 span、`term_id`、原因和置信度；
3. “DOR 是 6 还是 16 个月”没有被静默猜成一个值，而是形成 `uncertainty` 事实和 `TASK-ASR-DOR-001`；
4. 数字 token 前后相同。

### 对应代码

- `configured_asr_actor`：Ray Actor 调用真实 Whisper 服务；
- `apply_domain_glossary`：普通 Python 数据驱动纠错；
- `configured_glossary_function`：把同一函数作为 Ray Batch Function 执行；
- `extract_number_tokens` / `has_uncertain_number`：数字保护和不确定项门禁；
- `_quality_metrics`：生成现场可读质量处置。

### 如果用普通 Python 脚本

演示时可以说明：

> 脚本当然也能调用 Whisper 和做字符串替换；如果要得到这里同等行为，还需要自行管理模型 Worker 生命周期与复用、批次调度、每个片段的稳定 ID、原始/修订双份保存、逐项纠错事件、数字不变量、失败重试和输出合同。这个 Demo 不再实现第二套脚本 Pipeline，只把这些额外编排责任讲清楚。

## 2. 风险/机会分析：保留主要结论与反向证据（3 分钟）

### 现场动作

打开：

```bash
sed -n '1,80p' output/default/current/signal_evidence_report.md
rg 'SIG-CLINICAL|SUBGROUP_ORR' \
  output/default/current/research_facts.jsonl \
  output/default/current/thesis_impact_edges.jsonl \
  output/default/current/research_signals.jsonl
```

讲清三层：

1. 来源事实：总体 ORR 29%、TRAE 43%、n=8 亚组 ORR 62.5%；
2. 模型影响假设：总体疗效与安全性是 `contradicted`，小亚组是 `supported`；
3. SQL 判断：总体强制条件被可信数值违反，所以状态仍为 `thesis_review_required`，小样本支持性反证没有被丢掉，也没有反过来覆盖总体条件。

监管信号可继续展示：

```bash
rg 'SRC-REG|SIG-REGULATORY' \
  output/default/current/research_facts.jsonl \
  output/default/current/research_signals.jsonl
```

公司正式材料和专家纪要保留为两个不兼容状态，SQL 生成 `manual_review`，模型不能把冲突强行合并。

### 对应代码

- `extract_document_with_vane`：图片字节 + OCR 文本进入本地 Vane AI；
- `ROLE_REQUIRED_METRICS` / `ROLE_REQUIRED_IMPACTS`：缺少亚组支持性关系时合同失败；
- `bind_ai_facts`：把模型观察重新绑定到 PostgreSQL 的可信来源身份；
- `research_signals.sql`：可信度、冲突、违反、支持的确定性优先级。

### 如果用普通 Python 脚本

> 脚本方案需要自行维护文档批次、图片/文本请求绑定、JSON Schema 和角色语义重试、事实去重、单位与指标标准化、来源可信度 join、冲突集合、条件计算和稳定排序。尤其要避免“模型输出了最终状态”后直接采用，还要另建一个可测试的规则层。

## 3. 定向核验：只把判断点和证据交给研究员（2 分钟）

### 现场动作

```bash
cat output/default/current/review_tasks.jsonl
```

每条任务都包含：

- `judgment_id`：要判断什么；
- `evidence_fact_ids`：看哪几条证据；
- `source_locator`：回到 PDF 页码或录音时间；
- `recommended_action`：下一步只核验什么。

再指出证据报告中的四类语义：

- `source_fact`
- `approved_thesis`
- `model_hypothesis`
- `uncertainty`

低可信传闻只进入 `source_verification`，不能单独驱动 `thesis_supported` 或 `thesis_review_required`。

### 对应代码

- `_review_tasks`：按具体判断生成聚焦任务；
- `output_writer._validate_outputs`：验证任务引用和知识/证据语义；
- `render_evidence_report`：只读投影结构化结果，不二次调用模型。

### 如果用普通 Python 脚本

> 除了生成一段摘要，脚本还要实现类型隔离、事实/假设主键、跨文件引用完整性、任务幂等键、原始位置保存、低可信来源门禁和报告与结构化结果的一致性检查，否则研究员仍要从头翻所有材料。

## 4. 方法落地：只改词表数据（3 分钟）

### 现场动作

```bash
.venv/bin/python scripts/run_demo.py fixture --scenario glossary-before
.venv/bin/python scripts/run_demo.py run
.venv/bin/python scripts/run_demo.py verify --scenario glossary-before

.venv/bin/python scripts/run_demo.py fixture --scenario glossary-after
.venv/bin/python scripts/run_demo.py run
.venv/bin/python scripts/run_demo.py verify --scenario glossary-after
```

变更前：

- `actin-4` 未被修正；
- `knowledge_status=review_required`。

变更后：

- 出现 `TERM-TARGET-001`；
- `Nectin-4` 命中；
- `knowledge_status=accepted`；
- 两个 manifest 的 `pipeline_sha256` 相同。

对应逻辑在 `fixture_loader._fixture_rows`、`glossary_fingerprint` 和 `_run_asr` 的 correction stage version 中。词表哈希进入阶段身份，所以方法数据变化会精确使纠错阶段失效，不需要修改 Pipeline、Prompt 或 SQL。

### 如果用普通 Python 脚本

> 也可以从数据库读取词表；为了安全上线，还要自行实现词表快照、版本哈希、缓存失效、影响范围选择、前后结果对比、可回滚发布和 manifest 记录。这里展示的是这些动作如何成为统一阶段合同，而不是声称“词表只能用 Vane 做”。

## 5. 故障恢复与局部重算（3 分钟）

### 现场动作

```bash
.venv/bin/python scripts/run_demo.py fixture --scenario recovery-fault
.venv/bin/python scripts/run_demo.py run
```

运行应非零退出。损坏的是实际写入 MinIO 的临床 PDF 字节，不是 Mock 异常。临床来源被隔离，不发布不完整快照；其他来源成功的阶段状态已经写入 PostgreSQL。

修复并恢复：

```bash
.venv/bin/python scripts/run_demo.py fixture --scenario recovery-fixed
.venv/bin/python scripts/run_demo.py run --resume
.venv/bin/python scripts/run_demo.py verify --scenario recovery-fixed
```

查看：

```bash
cat output/recovery/current/run_manifest.json
cat output/recovery/current/source_processing_status.jsonl
```

验收要求 `resume_recomputed_source_ids` 只有 `SRC-CLINICAL`。

### 对应代码

- `StageStateStore`：持久化 `source_id + sha256 + stage + version`；
- `_cached_or_pending`：恢复 anti join 的选择边界；
- `fetch_and_validate_source`：真实对象哈希与解码门禁；
- `publish_outputs`：所有输出通过合同后才原子切换 `current`。

### 如果用普通 Python 脚本

> 需要自行实现阶段状态数据库、唯一幂等键、成功结果 locator、失败/隔离语义、版本升级失效、变化输入 anti join、缓存结果重新校验、下游 merge，以及多文件快照的原子提交。这正是演示故障恢复时可以指向的代码逻辑；不需要为了说明它再写一份 Python 对照 Pipeline。

## 6. 结束时运行机械验收

```bash
.venv/bin/python scripts/run_demo.py verify --scenario default
```

默认验收应全部 PASS，恢复专属断言显示 SKIP。只有完成故障/修复场景后，恢复断言才会 PASS。

最后强调：

> 四项业务改善依赖数据、模型、研究方法和研究员，不是 Vane 独占。Vane 在这个 Demo 中体现的是统一 Relation、Ray Worker、显式合同、可追溯身份、局部恢复和原子发布，让这些动作更容易稳定复现和扩展。
