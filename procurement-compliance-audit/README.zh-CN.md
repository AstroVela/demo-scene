# Vane 招采专家评分异常 SQL Demo

**简体中文** | [English](README.md)

一个专家在招标前推荐了“景维自动化”，评审时没有回避，又给这家供应商异常高分；剔除他的评分后，第一名从 `SUP-JW-001` 变成 `SUP-ZJ-002`。

这就是整个 Demo。它用一条短链路展示 Vane 如何让 Python 状态资源、多模态模型和确定性 SQL 在同一条 Pipeline 中协作：

- stateful UDF 复用 RapidOCR engine，从两张图片读取文本；
- AI Function 把图片和 OCR 文本交给 `Qwen2.5-VL-3B-Instruct`，只抽取事实；
- stateless UDF 严格校验 AI JSON，形成模型与规则之间的合同；
- SQL 计算评分偏差、重排名和三条审计规则。

预期结果：3 条 finding，项目状态为 `review_required`，风险专家为 `EXP-001`，winner 变化为 `SUP-JW-001 -> SUP-ZJ-002`。

![Vane 招采合规审计数据流程图](docs/vane-procurement-audit-data-flow.png)

[查看可编辑的 Excalidraw 源文件](docs/vane-procurement-audit-data-flow.excalidraw)

## 已验证环境

本仓库发布验收使用以下平台。其他平台可以自行尝试，但当前没有验证：

| 项目 | 已验证值 |
| --- | --- |
| 操作系统 | Ubuntu 24.04 x86_64（glibc 2.39） |
| Python | CPython 3.12 |
| Vane | `vane-ai==0.1.0.dev20260714234347` |
| 模型服务 | 本机 NVIDIA GPU 上的 `Qwen2.5-VL-3B-Instruct` |

TestPyPI 上的 Vane wheel 是 CPython 3.12、Linux x86_64、`manylinux_2_39` 构建，因此不承诺支持更旧 glibc、其他 Python 次版本或其他 CPU 架构。

Ubuntu 基础工具：

```bash
sudo apt update
sudo apt install -y git curl python3.12 python3.12-venv
```

## 五分钟快速开始

以下命令全部在本项目自己的 `.venv` 中执行，不依赖任何开发者本机环境。

### 1. 创建项目环境

```bash
cd procurement-compliance-audit
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### 2. 从 TestPyPI 安装固定版 Vane

```bash
python -m pip install -i https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  vane-ai==0.1.0.dev20260714234347
```

`--extra-index-url` 让 Vane 的普通依赖继续从 PyPI 获取。不要删掉固定版本，否则 launcher 会拒绝未经本 Demo 验证的运行时。

### 3. 安装项目依赖

```bash
python -m pip install -r requirements.txt
python -m pip check
```

这一步会以 editable 方式安装当前源码，并安装这些直接依赖：

- `openai==2.45.0`：调用本地 OpenAI-compatible Qwen 服务；
- `rapidocr` 与 `onnxruntime`：CPU OCR actor；
- `pillow`：图片读取；
- `pyarrow`：Vane relation/Python 数据边界；
- `pyyaml`：读取严格的 `runtime.yml`；
- `pytest`：快速测试。

### 4. 启动本地千问服务

完整的 NVIDIA、vLLM、模型下载、启动和排错步骤见：

**[本地 Qwen2.5-VL 服务搭建指南](docs/local-qwen-service.zh.md)**

README 只检查项目所需的服务合同：

```bash
curl -fsS -o /dev/null -w 'health HTTP %{http_code}\n' \
  http://127.0.0.1:8001/health
curl -fsS -H 'Authorization: Bearer dummy' \
  http://127.0.0.1:8001/v1/models | python -m json.tool
```

预期 health 为 HTTP 200，模型列表包含 `Qwen2.5-VL-3B-Instruct`。独立指南还包含一次真实图片 `/v1/chat/completions` 检查。

### 5. 运行真实 Demo

```bash
python scripts/run_demo.py
```

这个命令没有 AI mock fallback。千问不可用、图片不可读、AI JSON 不合规或 Vane API/版本不匹配时会明确失败。

成功时终端会显示：

```text
Result: review_required | 3 findings | flagged expert EXP-001
Winner recalculation: SUP-JW-001 -> SUP-ZJ-002
```

只生成两份结果文件：

```text
output/audit_findings.jsonl  # 3 行
output/audit_summary.jsonl   # 1 行
```

不启动千问也可以运行快速确定性测试：

```bash
python -m pytest tests/fast -q
```

## 四个来源文件

`fixtures/expert-score-anomaly/` 恰好包含四份业务数据：

| 文件 | Grain | 用途 |
| --- | --- | --- |
| `project.json` | 一个采购项目 | 供应商、图片 locator、原 winner、规则阈值 |
| `expert_scores.csv` | expert × supplier，共 12 行 | 4 位专家对 3 家供应商的总分 |
| `expert_recommendation.png` | 一份图片证据 | `EXP-001` 在招标前推荐景维自动化 |
| `committee_minutes.png` | 一份图片证据 | `EXP-001` 参加评审且回避状态为“否” |

所有姓名、企业和文档均为合成演示素材。

评分矩阵特意满足：

- 全部专家参与时，`SUP-JW-001` 平均分最高；
- `EXP-001` 给景维 98 分，其他专家对景维均分为 80 分，偏差为 18 分；
- 去掉 `EXP-001` 后，`SUP-ZJ-002` 平均分最高。

## 八节点数据流

```text
project.json + expert_scores.csv + 2 PNG
  ├── stg_scores
  └── stg_evidence_images
        └── int_evidence_ocr       [stateful UDF]
              └── int_evidence_ai  [Qwen multimodal AI Function]
                    └── int_conflict_facts [stateless UDF]

stg_scores + int_conflict_facts
  └── int_score_metrics            [SQL]
        └── audit_findings          [SQL, 3 rules]
              └── audit_summary    [SQL, 1 project]
```

| Relation | 物化 | Grain | 处理逻辑 |
| --- | --- | --- | --- |
| `stg_scores` | view | expert × supplier | 类型、分值和 supplier contract |
| `stg_evidence_images` | view | evidence image | 两张 PNG 的 locator |
| `int_evidence_ocr` | table | evidence image | OCR 全文、置信度、行数 |
| `int_evidence_ai` | table | evidence image | 千问原始 JSON response |
| `int_conflict_facts` | view | evidence image | 校验后的推荐/参加/回避事实 |
| `int_score_metrics` | view | project × conflict signal | peer average、score delta、两次排名 |
| `audit_findings` | table | finding | 三条确定性规则 |
| `audit_summary` | table | project | 项目级审计状态 |

`int_evidence_ai` 是唯一由 Python 物化的中间 relation。原因是当前多模态入口是 relation API，而不是只接受文本的 SQL built-in。Runner 为每张图片建立一个单行逻辑请求，直接绑定 `project_id/file_id`，不依赖模型 actor 的执行行序；返回的 `document_type` 必须与 fixture 中可信的文件 `role` 一致，否则只用同一图片强化合同后重试一次。SQL 事实节点会再次执行相同的 role 绑定，防止绕过 Python 边界。

## 三段关键 Vane API

Stateful OCR UDF：

```python
@vane.cls(actor_number=1, return_dtype="VARCHAR", name="evidence_ocr_json", gpus=0)
class EvidenceOcrActor:
    def __init__(self, allowed_root=None, engine_factory=None):
        self.engine = (engine_factory or build_rapidocr)()

    def __call__(self, local_path: str) -> str:
        value = Path(local_path).read_bytes()
        # 同一个 actor 连续处理两张图片，engine 只初始化一次。
        return normalize_ocr_observations(self.engine(value))
```

Multimodal AI Function：

```python
result = vane.ai.prompt(
    one_image_relation,
    "prompt_text",
    image_columns=["image_bytes"],
    provider="openai",
    model="Qwen2.5-VL-3B-Instruct",
    provider_options=provider_options,
    prompt_options=prompt_options,
    system_message=AUDIT_FACT_SYSTEM_MESSAGE,
    output_column="raw_response",
    num_gpus=0,
)
```

Stateless AI contract UDF：

```python
@vane.func(return_dtype="VARCHAR", name="validate_audit_fact_json")
def validate_audit_fact_json_udf(raw_response: str) -> str:
    return validate_audit_fact_json(raw_response)

vane.attach_function(
    validate_audit_fact_json_udf,
    connection=connection,
    alias="validate_audit_fact_json",
    parameters=["VARCHAR"],
    replace=True,
)
```

## 为什么 AI 只抽事实

模型只回答：图片是哪类文档、专家编号、供应商名称、是否推荐、是否参加、是否回避、证据原文和置信度。

模型不回答“是否违规”。最终结论由 SQL 产生，因此阈值、排名方法、finding ID、severity 和 recommended action 都可以审查、测试和稳定复现：

1. `EXP-001-conflict-not-recused`：推荐供应商后仍参加且未回避；
2. `EXP-002-score-bias`：对相关供应商的得分高出 peers 至少 15 分；
3. `EXP-003-award-impact`：去掉该专家后 winner 改变。

本地 8001 服务不实现 OpenAI `response_format`，千问有时会在 JSON 外增加一个
完整的 JSON code fence。Stateless contract 只规范化这一种“完整外层 fence”；任何
fence 外 prose、缺字段、未知字段、错误类型或“图片原文”占位值都会失败。最终响应
先在 AI 边界做合同预检，再由 SQL 中真实 attached 的 stateless UDF 独立校验一次。

只有两张 fixture 图片都通过 OCR 门槛并各自真实调用千问后，Pipeline 才允许继续。OCR 未覆盖任一图片会带缺失 `file_id` 直接失败且不发布输出，避免默认命令在未执行完整 AI 场景时伪装成功。如果两次调用都完成、但任一 AI confidence 低于 0.75，SQL 才不生成确定性 finding，并将 summary 标为 `insufficient_evidence`。

## 两个输出

成功运行后只生成：

```text
output/audit_findings.jsonl
output/audit_summary.jsonl
```

正常 fixture 下前者恰好三行、后者恰好一行；证据不足时 findings 为零行，summary 仍为一行且状态是 `insufficient_evidence`。写入前会校验字段、主键、枚举、计数和 evidence 引用，再用同目录临时文件原子替换。

在同一个 connection 中调试中间 relation 时，可执行：

```sql
-- queries.sql 包含全部八个只读查询
select * from int_score_metrics;
select * from audit_findings order by rule_id;
select * from audit_summary;
```

## 替换成自己的场景

- PostgreSQL / SRM：把 `fixture_loader.py` 产生的四张 Arrow 输入表换成数据库 snapshot；八个核心 relation 无需改名。
- MinIO / S3：把 `local_path` 换成对象 locator，并在 stateful actor 中读取已校验的对象 bytes。
- 企业 OCR：保留 `evidence_ocr_json(local_path)` 的 JSON contract，替换 actor 初始化和调用实现。
- 自有多模态模型：修改 `runtime.yml` 的 loopback endpoint/model，并保持 OpenAI-compatible provider 和八字段 response schema。
- 新规则：在 `audit_findings.sql` 中增加显式、可测试的 SQL 分支；不要让模型直接输出风险结论。

## 安装与运行问题

| 现象 | 处理方式 |
| --- | --- |
| `No matching distribution found for vane-ai` | 确认是 Ubuntu 24.04 x86_64、Python 3.12，并完整使用 TestPyPI 与 extra index 命令 |
| launcher 报 Python/Vane/DuckDB 版本不匹配 | 重新激活 `.venv`，按固定版本命令重装；错误信息会列出当前解释器、prefix 和 expected/actual |
| `ModuleNotFoundError: openai` | 执行 `python -m pip install -r requirements.txt` 和 `python -m pip check` |
| Qwen health 或图片请求失败 | 查看[本地千问服务搭建指南](docs/local-qwen-service.zh.md)中的端口、driver、OOM、模型名与代理排错 |
| 输出不是 3 条 finding | 默认 fixture 的 OCR 或 AI confidence 未达到门槛；检查终端错误和 Qwen 响应，不要使用 mock 输出替代 |

## local 与 ray

默认 `runtime.yml` 使用当前安装版本支持的：

```yaml
runner: local
```

需要分布式执行时改为：

```yaml
runner: ray
```

Fixture、UDF contract、AI relation 调用和七个 SQL 文件保持不变。当前发布验收只覆盖并默认使用 `local`；`ray` 是配置兼容入口，但在目标集群完成单独 smoke test 前不属于本 Demo 的已验证发布能力。

Launcher 不仅检查 API 形状，还会精确校验本 Demo 已验证的运行时标识：

| 组件 | 固定标识 |
| --- | --- |
| Vane distribution metadata (`vane-ai`) | `0.1.0.dev20260714234347` |
| `vane.__version__` | `0.1.0.dev20260714234347` |
| DuckDB Python package | `0.1.0.dev20260714234347` |
| DuckDB engine | `v1.6.0-dev121` |
| DuckDB source revision | `ca6948529b` |
| OpenAI Python client | `2.45.0` |

任一标识或所需 Vane API 不匹配都会启动失败，不会静默回退到普通 DuckDB。升级运行时时必须同时更新 launcher、设计文档和真实 E2E 验收结果。
