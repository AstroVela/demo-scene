# 使用 Vane 管理企业 Agent 多模态证据

[English](README.md) | [简体中文](README.zh-CN.md)

在证据进入企业 Agent 之前，先构建可审计的多模态上下文。该流水线解析文档、
文本、图片和音频资产，将它们与业务需求关联，再通过 SQL 找出证据缺口、主张
冲突、过期观察和媒体风险。最终产物包括受治理的 Agent 上下文和按优先级排列
的审核队列；模型执行和检索不在本示例范围内。

## Vane Data 重点

- 将场景表和资产目录读取为 Relation，完成校验后通过 SQL 关联。
- 每个被引用的公开资产只经过一次带类型的 `map_batches` 分支处理，再关联到
  一个或多个业务案例。
- 使用 Relation SQL、聚合和 writer 生成证据缺口、主张冲突、受治理上下文
  和审核队列。

默认离线数据由 5 个固定版本的公开文件和 4 个合成案例组成，可稳定复现证据
缺口、冲突、时效性和审核路径。

## 快速开始

需要 Python 3.10 或更高版本。在当前目录运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
VANE_RUNNER=local-fast .venv/bin/python src/enterprise_multimodal_agent.py
```

预期摘要：

```text
Business cases: 4
Public assets: 5
Evidence records: 8
Missing requirements: 1
Conflicting claims: 1
Cases requiring review: 3
Output directory: output/enterprise_multimodal_agent
```

运行聚焦测试：

```bash
VANE_RUNNER=local-fast .venv/bin/python -m unittest discover -s tests \
  -p 'test_enterprise_multimodal_agent.py' -v
```

## 失败即关闭的校验

处理默认数据前，可执行文件会检查：

- 所有场景 CSV 的固定列、行数和 SHA-256；
- 公开资产目录、来源清单和 5 个资产文件是否与公开快照一致；
- 必填字段、日期、模态、主键和外键；
- 主张键值是否成对出现，以及观察日期是否晚于审核日期；
- 文件哈希和各模态解析规则，包括 UTF-8、SVG 和 WAV 校验，并明确拒绝无效
  UTF-8 与非正数 WAV 采样率。

只有被证据链接引用的资产会进入 batch UDF。`manifest.json` 中的默认路径使用
仓库相对路径，不会泄露开发者本机路径。

## 输出

默认运行结果写入 `output/enterprise_multimodal_agent/`：

| 文件 | 用途 |
| --- | --- |
| `asset_features.parquet` | 5 个去重后的公开资产解析结果 |
| `evidence_features.parquet` | 8 条带类型的案例与资产证据关联记录 |
| `agent_context.parquet` | 4 个受治理的案例上下文 |
| `evidence_gaps.csv` | 缺少的必需证据模态 |
| `evidence_conflicts.csv` | 冲突的主张和证据 ID |
| `review_queue.csv` | 按处理优先级排列的 blocked 和 needs-review 案例 |
| `status_summary.csv` | 各审核状态的数量 |
| `manifest.json` | 版本、已验证快照、输入哈希、计数和执行后端 |

## 文档

- [English tutorial](docs/enterprise_multimodal_agent.en.md)
- [中文教程](docs/enterprise_multimodal_agent.zh-CN.md)

教程说明 schema、Vane API、SQL 策略、输出契约、自定义输入行为和当前示例边界。

## 刷新公开资产

重新下载固定的公开来源，并核对预期哈希：

```bash
.venv/bin/python scripts/prepare_enterprise_agent_assets.py --refresh
```

这是唯一需要联网的操作。上游文件一旦发生变化，校验会直接失败，不会静默
替换快照。

## 仓库结构

```text
enterprise-agent-evidence/
├── data/enterprise_multimodal_agent/  # 固定场景和公开资产
├── docs/                              # 中英文教程
├── src/                               # 治理流水线和媒体处理 helper
├── scripts/                           # 公开快照准备脚本
├── tests/                             # 聚焦的契约和失败路径测试
├── README.md
├── README.zh-CN.md
└── requirements.txt
```
