# 使用 Vane 发布多模态训练数据

[English](README.md) | [简体中文](README.zh-CN.md)

使用 Vane Relation 和 Python batch UDF，将文档、文本、图片和音频资产整理为带类型的发布表。每种模态在独立分支中处理，由 RayRunner 物化为类型兼容的 Parquet 后，再经 Relation filter、聚合和 writer 生成发布与拒绝记录。

默认运行使用 Apache Arrow 和 Wikimedia Commons 的 5 个固定版本公开文件。流水线会先校验资产目录、来源清单和文件哈希，然后发布 4 条记录，并拒绝 1 个分辨率过低的 SVG。轻量处理器覆盖 UTF-8 文本、SVG 尺寸和 PCM WAV 元数据。

## Vane Data 重点

- 将一个输入 Relation 按模态拆成 4 个分支，经 RayRunner 物化后合并类型兼容的 Parquet 输出。
- 使用统一 Arrow schema，通过 `map_batches` 运行 Python 处理器，并由 Vane 使用 RayRunner 默认的 `ray_task`，或显式选择任务后端。
- 使用 Relation 聚合和 writer 生成带类型的 Parquet 发布数据、可审核的 CSV 摘要和 JSON 执行清单。

## 快速开始

已验证环境使用 Python 3.12 和 uv。Vane 0.1.0 及其依赖均从 PyPI 解析。

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install 'vane-ai==0.1.0'
uv pip install -r requirements.txt
uv pip check
.venv/bin/python src/multimodal_training_data.py
```

默认结果写入 `output/multimodal_training_data`。流水线应处理 5 条记录，发布 4 条，并拒绝 1 个低分辨率校验图片。

运行聚焦测试：

```bash
.venv/bin/python -m unittest discover -s tests \
  -p 'test_multimodal_training_data.py' -v
```

## 公开数据快照

仓库中的固定快照包括：

- 2 个采用 Apache-2.0 许可的 Apache Arrow README 文件；
- 2 个采用 CC0-1.0 许可的 Wikimedia Commons SVG 文件；
- 1 个采用 CC0-1.0 许可的 Wikimedia Commons WAV 文件。

每项资产的来源、版本、作者、许可证 URL 和哈希以 `data/multimodal_training_data/public_sources.csv` 为准。仓库级 Apache 2.0 许可证不能替代单项资产的许可信息。

重新下载公开资产并确认文件字节仍与固定哈希一致：

```bash
.venv/bin/python scripts/prepare_multimodal_training_data.py --refresh
```

只有刷新步骤需要联网。上游文件一旦发生变化，脚本会直接失败，不会静默替换快照。

## 文档

- [English tutorial](docs/multimodal_training_data.en.md)
- [中文教程](docs/multimodal_training_data.zh-CN.md)

教程说明 Arrow schema、模态处理器、Vane API、执行后端、输出契约、解析范围和来源元数据。

## 仓库结构

```text
multimodal-training-data/
├── data/multimodal_training_data/   # 清单和固定公开资产
├── docs/                            # 中英文教程
├── src/                             # 可执行的 Vane 流水线
├── scripts/                         # 公开快照准备脚本
├── tests/                           # 契约和端到端测试
├── README.md
├── README.zh-CN.md
└── requirements.txt
```
