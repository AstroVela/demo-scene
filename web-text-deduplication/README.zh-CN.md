# 使用 Vane 进行网页文本去重

[English](README.md) | [简体中文](README.zh-CN.md)

通过确定性的 MinHash 指纹、全局 LSH 候选、精确 Jaccard 校验和稳定图聚类，
检测完全重复和近似重复文本。流水线会输出候选诊断、审核表和去重后的代表记录。

## Vane Data 重点

- 将离线 fixture 读取为 Relation；也可以使用自定义数据源读取固定的 Common
  Crawl WARC 字节范围。
- 通过带类型的 `map_batches` 阶段计算 MinHash 指纹和精确 pair score。
- 使用 SQL 完成全局 LSH 候选关联、pair 增长诊断、递归连通分量、代表记录选择
  和结果写出。

## 示例数据

默认 fixture 包含 24 篇采用 Apache-2.0 许可、由本仓库编写的文档。数据使用
保留的 `.example` 域名，包含 6 个完全或近似重复组，以及 6 篇单例文档。
schema、SHA-256、行数、许可证、数据分类和预期结果记录在
`data/web_text_deduplication/documents_snapshot.json` 中。

可选的 Common Crawl 流程会读取指定 WARC 字节范围，再进入同一条去重流水线。
该流程需要联网且默认关闭。提取的第三方文本只写入已被 Git 忽略的
`workspace/` 目录，不会提交到仓库。

## 快速开始

需要 Python 3.10 或更高版本：

`requirements.txt` 会从公共 PyPI 安装已验证的 `vane-ai==0.1.0a1`；也可以直接执行 `pip install vane-ai` 安装 Vane。

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
VANE_RUNNER=local-fast .venv/bin/python src/web_text_deduplication.py
~~~

预期摘要：

~~~text
Source WARC records: 0
Text blocks: 24
Global pair space: 276
Candidate pair slots: 80 / 1000000
LSH candidate pairs: 18
Candidate reduction: 93.48%
Duplicate pairs: 18
Clusters: 12
Output directory: output/web_text_deduplication
~~~

18 条通过校验的边包括 6 组完全重复 pair 和 12 组近似重复 pair。流水线最终
保留 12 条代表记录。

运行聚焦测试：

~~~bash
VANE_RUNNER=local-fast .venv/bin/python -m unittest discover -s tests -p 'test_*crawl*.py' -v
VANE_RUNNER=local-fast .venv/bin/python -m unittest discover -s tests -p 'test_web_text_deduplication.py' -v
~~~

## 算法

默认数据流：

~~~text
已验证的文档 fixture
→ 规范化和 5-token shingle
→ 64 值 MinHash signature
→ 8 × 8 LSH band
→ 带保护的全局候选 self-join
→ 精确 Jaccard 接受判定
→ 递归连通分量
→ 稳定的代表记录选择
~~~

当碰撞 bucket 超过 `--max-candidate-pair-slots` 时，self-join 会在展开前失败。
MinHash 重叠率只作为诊断指标；只有精确 shingle Jaccard 达到或超过 0.7 才会
生成重复边。

少于 5 个 token 的文档使用一个有序的全文 shingle。这样可以保留 token
顺序，避免把短文本压缩成无序词集合。

## 输出

默认结果写入 `output/web_text_deduplication/`：

| 文件 | 用途 |
| --- | --- |
| `source_blocks.parquet` | 规范化后的输入 Relation |
| `fingerprinted.parquet` | 文本、shingle、MinHash 和 LSH band |
| `collision_buckets.csv` | 热点 bucket 和 pair 展开诊断 |
| `candidate_summary.csv` | 全局 pair 空间、预算和缩减率 |
| `domain_summary.csv` | 按域名统计的候选诊断 |
| `scored_pairs.parquet` | 候选 pair 的精确与近似分数 |
| `duplicate_pairs.csv` | 通过校验的重复图边 |
| `clusters.csv` | 稳定的聚类成员关系 |
| `cluster_inspection.csv` | 多成员聚类审核视图 |
| `deduped_documents.parquet` | 选中的代表记录 |
| `manifest.json` | 来源分类、快照校验、算法、计数和执行后端 |

## 可选 Common Crawl 流程

启用联网流程前，请先检查 [Common Crawl 使用条款](https://commoncrawl.org/terms-of-use)
和所选来源网站。确认参数用于避免意外抓取。

根据仓库中的 IANA 示例域名目标生成本地清单和快照：

~~~bash
VANE_RUNNER=local-fast .venv/bin/python scripts/prepare_web_text_deduplication_data.py \
  --refresh-index \
  --acknowledge-common-crawl-terms
~~~

运行已经验证的本地快照：

~~~bash
VANE_RUNNER=local-fast .venv/bin/python src/web_text_deduplication.py \
  --input workspace/web_text_deduplication/common_crawl_blocks.parquet \
  --snapshot-metadata workspace/web_text_deduplication/common_crawl_snapshot.json \
  --output-dir output/web_text_deduplication_common_crawl
~~~

自定义数据源读取固定的 WARC 字节范围，再由 batch UDF 提取不重叠的 HTML
文本块。

也可以在线重放清单中的 WARC 范围：

~~~bash
VANE_RUNNER=local-fast .venv/bin/python src/web_text_deduplication.py \
  --source common-crawl \
  --record-manifest workspace/web_text_deduplication/common_crawl_records.csv \
  --acknowledge-common-crawl-terms \
  --output-dir output/web_text_deduplication_live
~~~

生成文件保留在 `workspace/` 下，不应提交到仓库。教程还说明快照校验、自定义
输入标签和文本提取细节。

## 文档

- [English tutorial](docs/web_text_deduplication.en.md)
- [中文教程](docs/web_text_deduplication.zh-CN.md)

教程说明 Relation API、LSH 召回率权衡、pair-slot 保护、精确打分、递归聚类、
来源校验和当前范围。

## 仓库结构

~~~text
web-text-deduplication/
├── data/web_text_deduplication/  # 安全 fixture、元数据和目标列表
├── docs/                         # 中英文教程
├── src/                          # 去重流水线和 WARC helper
├── scripts/                      # fixture 和可选抓取准备脚本
├── tests/                        # 算法、失败路径和来源测试
├── README.md
├── README.zh-CN.md
└── requirements.txt
~~~
