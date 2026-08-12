# 企业多模态 Agent 证据上下文

企业 Agent 在回答“这组材料能否进入知识库”之前，至少要解决两个问题：原始文件是否真的可读，以及来自不同来源的判断能否互相印证。缺少必需模态、文件哈希变化、媒体解析失败、结论冲突或材料过期，都不适合直接交给下游 Agent。

本示例把这两层放在同一条 Vane 数据流里：

~~~text
真实公开文件快照
→ document / text / image / audio Batch UDF
→ 类型化 asset_features
→ 连接 evidence_links 生成 evidence_features
→ requirements、冲突和时效 SQL
→ agent_context + review_queue
~~~

## 案例范围

默认运行直接读取并解析文档、文本、SVG 和 WAV，不再假定 OCR、ASR 或截图解析已经由上游完成。处理结果包含来源页面、版本、许可证、内容哈希、媒体指标和风险标记。

流程止于 `agent_context` 和 `review_queue`，不包含模型调用或检索。

## 输入数据

`data/enterprise_multimodal_agent/` 包含三张场景表及其审计清单：

| 文件 | 内容 |
| --- | --- |
| cases.csv | 4 个待审核案例、业务问题和审核日期 |
| requirements.csv | 每个案例必需的证据模态 |
| evidence_links.csv | 案例与公共资产之间的链接、观察时间和断言 |
| scenario_snapshot.json | 数据分类、schema、行数、哈希和预期结果 |

`--asset-catalog` 默认指向 `data/enterprise_multimodal_agent/asset_catalog.csv`。`evidence_links.csv` 不复制文件正文，只保存 `asset_id`：

~~~text
record_id, case_id, asset_id, source_system, observed_at,
evidence_title, claim_key, claim_value
~~~

初始化代码：

~~~python
from pathlib import Path
from tempfile import TemporaryDirectory

import vane

from src._common import RunnerWorkspace
from src.enterprise_multimodal_agent import (
    DEFAULT_ASSET_CATALOG,
    DEFAULT_INPUT_DIR,
    materialize_sources,
)

conn = vane.connect()
workspace_dir = TemporaryDirectory(prefix="vane-enterprise-ray-")
workspace = RunnerWorkspace(Path(workspace_dir.name), conn)
cases, requirements, public_assets, evidence_links, modalities = (
    materialize_sources(
        conn,
        workspace,
        Path(DEFAULT_INPUT_DIR),
        Path(DEFAULT_ASSET_CATALOG),
    )
)
~~~

默认场景先通过 `scenario_snapshot.json` 校验三张 CSV。`materialize_sources` 再用 `read_csv_as_strings` 将 CSV 读入 Arrow、暂存为 Parquet，并交给 RayRunner 读取，然后执行以下完整性检查：

- 必填字段是否为空，日期是否有效，要求的模态是否合法；
- 案例 ID、要求组合、证据记录 ID 和资产 ID 是否重复；
- `claim_key` 与 `claim_value` 是否成对出现，每个案例是否至少有一项要求；
- 观察时间是否晚于 `review_due_at`；
- 要求是否引用不存在的案例，证据链接是否引用不存在的案例或资产；
- 资产是否带有 source URI、许可证、MIME type 和 64 位 SHA-256；
- 案例、资产和证据链接输入是否为空。

链接校验通过后，只有 `evidence_links` 真正引用的公共资产才进入 Batch UDF；资产清单中的无关记录不会被处理。文件仍通过 `content_path` 延迟读取，不会塞进 CSV。资产完成一次解析后才连接到案例，避免同一文件被多个案例引用时重复解码。

## 第一步：按模态解析真实文件

四个分支共享资产来源字段，但执行不同处理：

| 模态 | 当前处理 | 主要输出或风险 |
| --- | --- | --- |
| document | UTF-8 解码，保留换行，统计 token | invalid_utf8、empty_document |
| text | UTF-8 解码并规范空白 | invalid_utf8、text_too_short |
| image | 解析 SVG XML，从尺寸或 viewBox 读取宽高 | invalid_image、missing_dimensions、low_resolution |
| audio | 读取 PCM WAV 头、正采样率和帧数 | invalid_audio、audio_too_short |

每个处理函数都会读取真实文件并检查 `expected_sha256`。哈希不一致会终止批次。损坏的 UTF-8 会保留替换解码后的审核文本，同时用 `invalid_utf8` 拒绝；WAV 元数据异常（包括非正采样率）会归入 `invalid_audio`。

脚本按模态创建四条 Relation：

~~~python
stage_functions = {
    "document": "process_document_asset_batch",
    "image": "process_image_asset_batch",
    "audio": "process_audio_asset_batch",
    "text": "process_text_asset_batch",
}

branch_paths = []
for modality in modalities:
    source = public_assets.filter(
        f"modality = '{modality}'"
    ).order("record_id")
    branch_paths.append(
        workspace.write_relation(
            f"process-{modality}-asset",
            source.map_batches(
                importable_batch_function(stage_functions[modality]),
                schema=ASSET_FEATURE_SCHEMA,
                batch_size=4,
            ),
        )
    )

asset_features = conn.read_parquet(
    [str(path) for path in branch_paths]
)
~~~

RayRunner 将四个分支分别写入 Parquet 工作区，再通过多文件 Parquet 扫描合并成 5 行 `asset_features`。SQL 随后将其连接到 8 行 `evidence_links`，生成案例级 `evidence_features`。每个唯一文件只需校验和解析一次。

`evidence_features` 的公共契约包括：

- 案例关联：`record_id`、`case_id`、`asset_id`；
- 来源信息：`source_uri`、`source_page_uri`、`source_version`；
- 授权信息：`license_id`、`license_uri`；
- 内容字段：`evidence_text`、`content_sha256`、`byte_size`、`token_count`；
- 媒体字段：类型化 `media_metrics` struct；
- 门禁字段：`asset_decision`、`risk_flags`、`blocking_risk_count`。

默认样本中，Generic File SVG 得到 512×512，Audio WAV 得到 48 kHz 和 2.4 秒；Download SVG 的真实尺寸为 136×168，因此产生 low_resolution 并被拒绝。

## 第二步：检查缺失模态和冲突断言

缺失检查从 `requirements` 左连接实际证据：

~~~sql
select
  r.case_id,
  c.account_id,
  r.evidence_type as missing_evidence_type,
  'missing_required_evidence' as reason
from case_requirements r
join business_cases c using (case_id)
left join evidence_features e
  on e.case_id = r.case_id
 and e.evidence_type = r.evidence_type
where e.record_id is null
~~~

冲突检查按 `case_id` 和 `claim_key` 聚合：

~~~sql
select
  case_id,
  claim_key,
  count(distinct claim_value) as distinct_values,
  string_agg(distinct claim_value, ', ' order by claim_value) as claim_values,
  string_agg(record_id, ', ' order by record_id) as evidence_ids
from evidence_features
where claim_key <> '' and claim_value <> ''
group by case_id, claim_key
having count(distinct claim_value) > 1
~~~

结果分别写入 `evidence_gaps` 和 `evidence_conflicts`。默认场景中：

- `case-incomplete-bundle` 缺少必需的 `audio`；
- `case-wikimedia-media` 的 `media_readiness` 同时出现 `ready` 和 `blocked`。

## 第三步：生成 Agent 上下文

SQL 按案例汇总证据、来源和模态数量，以及被媒体门禁拒绝的资产、缺失、冲突、风险和过期数量。`evidence_ids`、`asset_ids`、`source_systems`、`modalities` 和 `license_ids` 保留为列表，`context_text` 按观察时间倒序排列。

状态规则是：

~~~sql
case
  when missing_evidence_count > 0
    or conflict_count > 0
    or blocking_risk_count > 0 then 'blocked'
  when stale_evidence_count > 0
    or risk_count > 0 then 'needs_review'
  else 'ready'
end
~~~

默认结果：

| 案例 | 证据 | 模态 | 缺失 | 冲突 | 拒绝资产 | 过期 | 状态 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| case-arrow-docs | 2 | 2 | 0 | 0 | 0 | 0 | ready |
| case-wikimedia-media | 3 | 2 | 0 | 1 | 1 | 0 | blocked |
| case-incomplete-bundle | 2 | 2 | 1 | 0 | 0 | 0 | blocked |
| case-stale-docs | 1 | 1 | 0 | 0 | 0 | 1 | needs_review |

## 产物

所有输出都由 RayRunner 执行。Parquet 产物使用 Relation writer；`workspace.write_csv` 会新建一条 Ray 投影，再将 Arrow 结果写为便于审核的 CSV：

~~~python
workspace.write_parquet(
    agent_context, OUTPUT_DIR / "agent_context.parquet"
)
workspace.write_parquet(
    asset_features, OUTPUT_DIR / "asset_features.parquet"
)
workspace.write_parquet(
    evidence_features, OUTPUT_DIR / "evidence_features.parquet"
)
workspace.write_csv(evidence_gaps, OUTPUT_DIR / "evidence_gaps.csv")
workspace.write_csv(
    evidence_conflicts, OUTPUT_DIR / "evidence_conflicts.csv"
)
workspace.write_csv(review_queue, OUTPUT_DIR / "review_queue.csv")
workspace.write_csv(status_summary, OUTPUT_DIR / "status_summary.csv")
~~~

| 文件 | 内容 |
| --- | --- |
| asset_features.parquet | 5 个唯一公共资产的解析结果，每个文件只处理一次 |
| evidence_features.parquet | 8 条类型化证据、媒体指标、来源、许可证、哈希和风险 |
| agent_context.parquet | 4 条案例级 Agent context |
| evidence_gaps.csv | 缺少的必需模态 |
| evidence_conflicts.csv | 冲突断言、值和证据 ID |
| review_queue.csv | 3 个非 ready 案例，blocked 排在 needs_review 前 |
| status_summary.csv | 按状态汇总案例和证据 |
| manifest.json | schema 版本、默认相对路径、场景与资产快照校验、输入哈希、模态、行数、执行后端和产物清单 |

## 运行与刷新来源

默认运行：

~~~bash
.venv/bin/python src/enterprise_multimodal_agent.py
~~~

输出：

~~~text
Business cases: 4
Public assets: 5
Evidence records: 8
Missing requirements: 1
Conflicting claims: 1
Cases requiring review: 3
Output directory: output/enterprise_multimodal_agent
~~~

重新下载并验证公共来源：

~~~bash
.venv/bin/python scripts/prepare_enterprise_agent_assets.py --refresh
~~~

自定义场景使用 `--input-dir`，自定义资产清单使用 `--asset-catalog`。Vane 0.1.0 默认使用 RayRunner，因此 `--execution-backend auto` 会解析为 `ray_task`；也可以显式指定 `ray_task` 或 `subprocess_task`。中间 Relation 通过 Parquet 工作区物化，保证分布式 UDF 获得所需的 Ray query 上下文。

默认运行中，只要场景 CSV、资产清单、来源清单或文件与固定快照不一致，脚本就会在处理前失败。`manifest.json` 对默认输入使用仓库相对路径。自定义输入仍会执行语义完整性检查，但标记为 `custom_inputs`，不视为已通过固定快照验证。

聚焦测试：

~~~bash
.venv/bin/python -m unittest -v tests.test_enterprise_multimodal_agent
~~~

## 示例边界

- 案例、要求、断言和观察日期是固定的演示数据；30 天时效窗口、512×512 图片阈值和三档状态也是示例规则。
- 处理函数只做轻量的 UTF-8、SVG 和 PCM WAV 处理，不覆盖 PDF/Office、OCR、栅格图片、ASR、说话人分离或模型推理。
- 固定快照用于离线复现，不用于说明多节点存储、吞吐、重试或故障恢复能力。

## 数据说明

- 5 个媒体文件来自 Apache Arrow 和 Wikimedia Commons，来源版本、许可证信息和 SHA-256 记录在 `data/enterprise_multimodal_agent/asset_sources.csv` 与 `asset_snapshot.json` 中。
- 4 个案例、8 项要求和 8 条证据链接是固定的演示数据，`scenario_snapshot.json` 记录其 schema、行数、哈希和预期结果。
