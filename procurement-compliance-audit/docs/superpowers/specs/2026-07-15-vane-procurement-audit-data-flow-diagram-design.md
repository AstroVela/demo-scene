# Vane 招采合规审计数据流程图设计

日期：2026-07-15
状态：已确认

## 目标

在当前 Demo 项目中增加一张可编辑的 Excalidraw 数据流程图，用真实的招采专家评分异常链路说明 Vane 的价值：Vane 能把结构化评分、图片证据、Stateful UDF、多模态模型、Stateless UDF 和确定性 SQL 规则组织成一条可验证、可解释并可发布的合规审计流水线。

主要读者是尚不了解 Vane 的企业潜在用户。图应让非技术读者在约 30 秒内理解业务故事，同时让技术读者看到当前实现中真实存在的数据、relation、API、规则和输出契约。

## 核心信息

标题：

> 从招采证据到可解释的合规审计结论

副标题：

> Vane 将结构化评分、OCR、AI 事实与 SQL 规则统一在一条可验证流水线中

图要证明的核心观点是：

> 企业可以用 Vane 在同一计算图中处理业务记录与图片证据，让 AI 只提取事实，再由确定性 SQL 计算指标、执行规则并交付可审计结果。

## 范围

图中包含：

- `project.json`、`expert_scores.csv`、专家推荐记录 PNG 和评审会议纪要 PNG 四类输入；
- Vane 内的数据接入、Stateful OCR、Qwen 多模态事实提取、Stateless JSON 合同和 SQL 指标计算；
- 当前 Demo 的八个核心 relation 及其四阶段归属；
- 三条真实审计规则和项目级 `review_required` 汇总；
- `audit_findings.jsonl`、`audit_summary.jsonl`、输出契约校验和逐文件原子替换；
- OCR 覆盖不完整时 fail fast、不调用完整 AI 链路且不发布输出；
- 两次 AI 调用完成但事实置信度不足时的 `insufficient_evidence` 降级语义；
- 从该 Demo 中可以直接得到的 Vane 企业价值。

图中不展开：

- 完整 AI JSON Schema 和所有输出列；
- `int_score_metrics` 的全部 CTE；
- 每个函数参数、版本校验常量或测试用例；
- 没有被当前项目直接证明的性能、成本或外部系统集成结论。

## 输出文件

- 可编辑源文件：`docs/vane-procurement-audit-data-flow.excalidraw`
- 渲染预览：`docs/vane-procurement-audit-data-flow.png`

## 画布与阅读路径

画布沿用 Claims Demo 的横向五段式结构、白色背景和左到右阅读路径：

1. 顶部是价值总览：业务记录与图片证据 → Vane 混合计算与治理 → 可解释审计判断 → 标准化结果交付。
2. 中部是主视觉：左侧四类输入汇聚，中间是最大的 Vane 计算区域，随后是规则扇出和输出发布。
3. 底部是六项能力总结，每项能力用虚线连接回主流程中的真实机制。

正常数据流使用连续实线箭头。OCR 覆盖失败、低置信降级和发布失败使用红色虚线或红色注释，避免与成功主路径混淆。

## 主流程设计

### 1. 四份来源数据汇聚

左侧使用四路汇聚结构：

- `project.json`：项目、供应商、阈值和 evidence locator；
- `expert_scores.csv`：4 位专家 × 3 家供应商，共 12 行评分；
- `expert_recommendation.png`：`EXP-001` 推荐景维自动化；
- `committee_minutes.png`：`EXP-001` 参加评审且未回避。

下方展示一段真实、脱敏的输入样例：

```json
{
  "project_id": "PRJ-2026-001",
  "file_id": "EVD-REC-001",
  "role": "expert_recommendation",
  "local_path": "expert_recommendation.png"
}
```

### 2. Vane 混合计算流水线

Vane 区域将八个 relation 压缩成四个易理解阶段：

1. **SQL 数据接入**：`stg_scores` 与 `stg_evidence_images` 统一评分、供应商和证据定位。
2. **Stateful OCR**：`int_evidence_ocr` 使用 `EvidenceOcrActor` 复用 RapidOCR engine，生成全文、状态和置信度。
3. **AI 事实与合同**：`int_evidence_ai` 对两张图片分别调用 `vane.ai.prompt` 和 `Qwen2.5-VL`；`int_conflict_facts` 使用 `validate_audit_fact_json` 校验八字段合同，并把可信文件 `role` 绑定到 `document_type`。
4. **SQL 指标计算**：`int_score_metrics` 关联推荐、参评和未回避事实，计算 peer average、18 分评分偏差以及剔除专家后的 winner 变化。

阶段旁保留真实技术锚点：

`DuckDB SQL`、`Vane Stateful UDF`、`RapidOCR Actor`、`vane.ai.prompt`、`Qwen2.5-VL`、`Vane Stateless UDF`。

AI 阶段下方展示精简的真实事实形态：

```json
{
  "document_type": "recommendation_record",
  "expert_id": "EXP-001",
  "supplier_name": "景维自动化有限公司",
  "recommended": true,
  "confidence": 0.96
}
```

该样例明确表示模型只抽取图片事实，不直接输出违规结论、严重程度或处置动作。

### 3. 三条确定性规则与汇总

主链从 `int_score_metrics` 进入确定性 SQL 规则扇出：

1. `EXP-001-conflict-not-recused`：推荐相关供应商后仍参加评审且未回避，`high`；
2. `EXP-002-score-bias`：专家 98 分、peers 80 分，`+18 >= 15`，`medium`；
3. `EXP-003-award-impact`：剔除该专家后 winner 从 `SUP-JW-001` 变为 `SUP-ZJ-002`，`high`；
4. 项目汇总：`review_required`，3 findings，其中 2 条 high。

图中强调“AI 提取事实，SQL 负责指标、阈值、规则 ID、severity 和汇总状态”。

异常语义只保留两项：

- 0/1 张图片通过 OCR 门槛时，覆盖检查 fail fast，缺失 `file_id` 可定位，并且不发布输出；
- 两张图片都实际调用 AI 后，若任一事实置信度低于 0.75，则 findings 为零，summary 为 `insufficient_evidence`，不会伪装成 `passed`。

### 4. 双 JSONL 输出与验证

最右侧使用一条短链路表示工程交付：

> 两个标准化 JSONL → 字段/主键/计数/evidence 引用校验 → 临时文件、`fsync`、逐文件 `os.replace` → 结果验证

失败路径标注为“目标文件不替换，保留旧文件”，不把逐文件原子替换误写成跨文件数据库事务。

端到端证据样例为：

- `review_required | 3 findings`
- `flagged expert: EXP-001`
- `SUP-JW-001 -> SUP-ZJ-002`
- `audit_findings.jsonl: 3 rows`
- `audit_summary.jsonl: 1 row`

## Vane 价值总结

底部能力带保留六项：

- **多模态统一**：JSON、CSV 与 PNG 进入同一计算图；
- **SQL 编排**：普通 DuckDB SQL 组织八节点 DAG；
- **状态计算**：Stateful Actor 复用有初始化成本的 OCR engine；
- **AI 可治理**：双图片覆盖门禁、可信 role 绑定和结构化响应合同；
- **决策可解释**：AI 提取事实，SQL 计算指标并生成确定性 finding；
- **结果可交付**：输出契约、逐文件原子替换和真实 E2E 验证。

收束语为：

> 可复用模式：业务记录 + 图片证据 → 可信事实与指标 → 可解释合规结论

## 视觉语言

遵循 Excalidraw 技能统一配色：

- 主流程和 Vane 主体：蓝色；
- AI 计算与 AI 合同：紫色；
- 输入：橙色；
- 规则与判断：浅黄色；
- 高风险 finding、拦截和失败：红色；
- 成功发布与验证：绿色；
- 真实 JSON/结果样例：深色背景和绿色数据文本。

所有元素使用 `roughness: 0`、`opacity: 100` 和 `fontFamily: 3`。标题、说明和技术标签以自由文本为主，容器只用于来源、主要阶段、规则结果和输出实体。

## 准确性与边界

- 所有 relation、函数、模型名称、阈值、finding ID 和输出均以当前项目代码为准。
- 图中企业、专家和文档均为合成 Demo 数据。
- 图中结果是采购审计线索和复核建议，不替代组织内部调查、法律判断或最终定标决定。
- AI 示例只展示结构化事实，不把模型描述为最终风险决策者。
- `ray` 不在图中作为已验证能力；当前发布验收使用 `local` runner。

## 验证方式

完成图文件后执行：

1. JSON 可被 Excalidraw 与渲染脚本解析；
2. 渲染 PNG 并进行至少一轮完整视觉检查；
3. 修正文字裁切、元素重叠、箭头穿越、间距失衡和阅读顺序问题，重复渲染直至通过；
4. 对照 README、七个 SQL 文件、fixture 和真实输出复核技术准确性；
5. 确认缩略视图仍能读出“输入—混合计算—确定性规则—可靠输出”的主线；
6. 确认 `.excalidraw` 与 PNG 均位于项目 `docs/`。
