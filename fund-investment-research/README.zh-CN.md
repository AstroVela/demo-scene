# 使用 Vane 构建可核验的多模态基金投研流程

[English](README.md) | **简体中文**

本 Demo 用一家完全虚构的创新药公司“澜星生物”展示一条可核验的投研知识生产链：从会议录音、历史研究材料、公司公告、专家纪要和低可信群聊截图中提取事实，将事实关联到研究员已批准的投资假设条件，再由确定性 SQL 生成研究工作流状态和定向复核任务。

它不是投研聊天机器人，也不生成买卖建议、目标价或仓位意见。

| Signal | 可见证据 | SQL 工作流状态 |
| --- | --- | --- |
| `SIG-CLINICAL` | 总体 ORR 29%、3 级以上 TRAE 43%，同时保留 n=8 亚组 ORR 62.5% 的支持性反证 | `thesis_review_required` |
| `SIG-RUNWAY` | 可信财务材料给出 24 个月现金可支撑时间 | `thesis_supported` |
| `SIG-REGULATORY` | 公司材料称 Q4 2026 按计划，专家纪要称延至 Q2 2027 | `manual_review` |
| `SIG-RUMOR` | 无原始公告的低可信群聊传闻 | `insufficient_evidence` |

## 架构

![基金投研数据流](docs/vane-fund-research-data-flow.png)

本 Demo 只支持 **Ray Runner**。ASR、OCR、领域纠错 Batch Function 和多模态 AI Prompt 都经由 Ray 执行；没有 Local Runner 分支，也没有运行时 Mock 降级。

```text
PostgreSQL 可信身份与规则 + MinIO WAV/PDF/PNG
  → 来源、哈希、媒体和解码门禁
  → Ray ASR Actor → 原始转写 → Ray 词表纠错 Function
  → Ray OCR Actor → vane.ai.prompt(image_columns=["image_bytes"])
  → 严格事实/影响关系合同 + 可信身份重新绑定
  → 确定性 SQL 状态
  → 交叉引用校验 + 原子快照发布
```

![确定性状态 SQL DAG](docs/vane-fund-research-sql-dag.png)

模型可以抽取事实和提出影响关系候选，但不能写入公司身份、来源角色、可信等级或最终状态。最终状态只由 [`research_signals.sql`](src/fund_investment_research/sql/research_signals.sql) 计算。

## Demo 直接体现的四项业务动作

| 业务改善 | 现场动作 | 直接证据 |
| --- | --- | --- |
| 提高投研知识生产质量 | 对比原始 ASR、规范术语和逐项纠错；保留“6 或 16 个月”的数字不确定性 | `transcript_segments.jsonl`、`asr_corrections.jsonl`、`asr_quality_metrics.json` |
| 加深风险/机会分析 | 从临床资料展开到三个事实、两个反对关系和一个小样本支持关系 | `signal_evidence_report.md`、`research_facts.jsonl`、`thesis_impact_edges.jsonl` |
| 从头核验变为定向核验 | 区分 `source_fact`、`approved_thesis`、`model_hypothesis`、`uncertainty`，只生成指向具体判断和原始位置的任务 | `review_tasks.jsonl` |
| 加快投研方法落地 | 只在 PostgreSQL `domain_terms` 增加 `actin-4 → Nectin-4`，不改 Pipeline 即改变纠错与知识处置 | `glossary-before` / `glossary-after` 两个输出快照及 manifest |

`verify` 对这些动作做机械 PASS/FAIL 校验，但不虚构研究员工时、提升比例或性能数据。性能不在本 Demo 范围内。

## 运行

本 Demo 要求：

- CPython 3.11；
- 本地 `~/vane` 构建及其支持图片列的 `vane.ai.prompt`；
- PostgreSQL、MinIO；
- OpenAI-compatible Whisper 服务；
- OpenAI-compatible Qwen2.5-VL 服务；
- `pdftoppm`、eSpeak/pyttsx3 和 ffmpeg。

按[中文运行手册](docs/runbook.zh-CN.md)准备环境，然后在本目录执行：

```bash
.venv/bin/python scripts/run_demo.py e2e
```

Launcher 会拒绝非本地 `~/vane` 安装、错误的 Vane/DuckDB 标识、缺少 `image_columns` 的旧 AI API、非 CPython 3.11 以及非 Ray 配置。

成功时会输出：

```text
loaded 1 company, 4 thesis conditions, 7 source files, and 4 signals
published 4 research signals, 14 facts, and 4 review tasks
PASS: exact four deterministic signal states
...
```

没有 AI 固定答案兜底：对象损坏、哈希不一致、ASR/OCR/AI 服务失败、AI JSON 或语义契约不合规、SQL 失败和发布失败都会非零退出，并保留最近一次成功快照。

## 输出

成功快照位于 `output/<scenario>/current`：

- `transcript_segments.jsonl`：带时间戳的原始/纠正转写和知识状态；
- `asr_corrections.jsonl`：原始 span、规范词、词表 ID、原因和置信度；
- `research_facts.jsonl`：来源可定位的事实与不确定项；
- `thesis_impact_edges.jsonl`：模型影响假设及证据状态；
- `research_signals.jsonl`：SQL 状态、理由、优先级和下一步；
- `review_tasks.jsonl`：聚焦判断、事实 ID 和原始 locator；
- `source_processing_status.jsonl`：阶段身份、状态、尝试次数和结果位置；
- `asr_quality_metrics.json`：术语命中、数字保持和知识处置；
- `run_manifest.json`：输入、模型、Prompt、SQL、Pipeline、阶段版本及输出哈希；
- `signal_evidence_report.md`：面向研究员的可读证据包。

发布先校验主键、枚举、知识语义、单位、事实/条件引用、低可信来源隔离和报告一致性，再切换 `current` 符号链接；不会留下半份新快照。

## 演示变体

完整命令和预期结果见[现场演示手册](docs/demo-walkthrough.zh-CN.md)：

- 词表热更新：`glossary-before → glossary-after`；
- 真实坏输入与恢复：`recovery-fault → recovery-fixed → run --resume`。

合成资产生成工具、字体和运行时依赖说明见 [`NOTICE.md`](NOTICE.md)。

恢复键为：

```text
source_id + source_sha256 + stage + stage_version
```

修复临床 PDF 后，验收器要求 `--resume` 只重算 `SRC-CLINICAL` 的变化/失败阶段，其他成功结果复用。

## 代码边界

- [`domain_logic.py`](src/fund_investment_research/domain_logic.py)：普通 Python 纠错、数字门禁、事实绑定；
- [`vane_functions.py`](src/fund_investment_research/vane_functions.py)：薄 Ray Function / Actor 适配；
- [`ai.py`](src/fund_investment_research/ai.py)：本地 Vane 图片 Prompt、严格 JSON 和角色语义合同；
- [`stage_state.py`](src/fund_investment_research/stage_state.py)：阶段身份与恢复选择；
- [`pipeline.py`](src/fund_investment_research/pipeline.py)：Ray-only Relation 编排；
- [`output_writer.py`](src/fund_investment_research/output_writer.py)：交叉引用验证和原子发布；
- [`verify_outputs.py`](src/fund_investment_research/verify_outputs.py)：业务动作验收。

核心业务函数仍是可单测的普通 Python；Vane 适配层不复制业务逻辑。本仓库没有实现第二套串行 Python Pipeline。演示时可以说明，脚本方案若要做到同等可恢复和可治理，需要自行补充 Actor 生命周期、批处理、类型合同、阶段状态、幂等键、重试、冲突合并、血缘和多文件原子发布；详见[演示手册](docs/demo-walkthrough.zh-CN.md)。

所有公司、文档、音频、数值和标识均为合成数据。不要提交真实投研材料、生产凭据、模型权重、`.venv` 或生成的 `output`。
