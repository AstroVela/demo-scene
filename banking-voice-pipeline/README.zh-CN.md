# 使用 Vane 与 Jev 分析银行客服语音

[English](README.md) | [简体中文](README.zh-CN.md)

把银行客服短录音变成可复核的业务字段：CPU 解码音频，GPU 上的 Whisper 负责转写，原生 `Relation.jev()` 产出结构化判断，确定性的 SQL 再映射到处理队列。关键词基线与 Jev 判断共享同一份转写结果，只有最终结果会落盘。

## Vane 重点

- 在同一条 `Relation` 链上组合 CPU Task、GPU Actor 与 Jev Actor；批次、重试和数据搬运由执行层负责。
- `Relation.jev()` 负责请求组织、并发控制和行与答案对齐；`state()` 对失败或可疑的转写返回 `NULL`，这些行不会发往外部服务。
- 只写出 `results.parquet` 与 `review.csv`；CSV 导出和评测读取已保存的最终结果，不会重跑 Whisper 或 Jev。

## 快速开始

目标环境为 Python 3.12、uv 和一块 CUDA GPU。Jev 支持目前只发布在 TestPyPI 的 `vane-ai` dev 构建上，因此固定的 Vane 版本与 `typesafe` SDK 需要单独安装：

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install --index-strategy unsafe-best-match \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  'vane-ai[typesafe]==0.3.0.dev8' 'typesafe-sdk==0.7.0'
uv pip install -r requirements.txt
uv pip check

export TYPESAFE_API_KEY="your-api-key"
.venv/bin/python src/banking_voice_pipeline.py \
  --output-dir output/banking_voice_pipeline \
  --limit 112
```

输出目录必须是新目录。运行会下载固定 revision 的 MInDS-14 中文子集和 `faster-whisper-small`，结束时打印关键词基线与 Jev 的意图指标。CUDA 环境需要把 cuBLAS 12 和 cuDNN 9 加入 `LD_LIBRARY_PATH`，见 [faster-whisper GPU 说明](https://github.com/SYSTRAN/faster-whisper#gpu)。

## 输出

| 文件 | 用途 |
| --- | --- |
| `results.parquet` | 最终结果：转写片段、关键词基线、Jev 原始响应和业务字段 |
| `review.csv` | 去掉 `response` 和 `segments` 的复核表，从已保存的最终结果导出 |

| 字段 | 业务含义 |
| --- | --- |
| `intent`、`queue` | 客户诉求及建议处理队列 |
| `urgency`、`dissatisfaction` | 尚未解决事项的紧急程度、文字表达中的不满程度（1–5） |
| `needs_human` | 是否仍有需要工作人员处理、核实或回复的事项 |
| `review_required`、`review_reason` | 是否需要复核，以及复核原因 |

## 测试

离线单元测试覆盖关键词基线、转写质量检查、队列映射、评测指标、问题定义和 CPU 音频解码，不需要网络、GPU 或 API key：

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## 目录结构

```text
banking-voice-pipeline/
├── src/banking_voice_pipeline.py   # 单文件流水线：解码、ASR、Jev、SQL 映射与评测
├── tests/                          # 离线单元测试
├── requirements.txt                # ASR、数据与 Jev SDK 依赖
└── output/                         # 运行时生成，已被 Git 忽略
```

## 数据与边界

- 数据为 [PolyAI MInDS-14](https://huggingface.co/datasets/PolyAI/minds14) 中文子集（CC-BY-4.0），共 502 条短录音，示例按路径哈希选取其中 112 条；不会提交到仓库。
- 每条录音是客户的一段短表达，不是完整多轮通话；没有实现说话人分离。
- `needs_human` 使用的 `0.5` 阈值未经校准，意图之外的字段也没有真实标签。
- 流水线只输出字段，不触发任何业务操作；所有结果都带 `review_required`。
