# 网页文本去重

网页语料会同时出现完全副本、轻微修改的版本、跨站镜像和重复模板。内容哈希只能找到字节完全一致的记录，全量两两比较又会产生平方级开销。本案例使用 Vane Relation、确定性 MinHash、全局 LSH 候选、精确 Jaccard 复核和图聚类，生成可审核的去重结果。

## 示例数据

默认的 `documents.csv` 包含 24 条仓库原创文档，使用 Apache-2.0 许可证和保留的 `.example` 域名。正文由 `scripts/prepare_web_text_deduplication_fixture.py` 在仓库内生成，`documents_snapshot.json` 固定 CSV 哈希、行数、schema 和预期结果。

数据包括：

- 6 个主题，每个主题包含一条基准文档、一条跨域完全镜像和一条只替换一个 token 的修订版本；
- 6 条互不重复的单篇文档；
- 4 个保留的合成域名。

这组数据适合快速 CI，同时覆盖完全重复、近重复、跨域候选、单文档簇、代表选择和全局候选空间诊断。

可选的 Common Crawl 模式会把准备好的 WARC 字节范围读入同一条流水线。该模式需要联网并显式启用，生成的文本只保存在 `workspace/` 下。

输入契约为：

~~~text
doc_id, source, domain, crawled_at, title, body
~~~

Vane 通过 Relation 读取：

~~~python
documents = conn.read_csv(str(DEFAULT_INPUT), header=True).project(
    "doc_id, source, domain, cast(crawled_at as date) as crawled_at, "
    "coalesce(title, '') as title, coalesce(body, '') as body"
)
~~~

`doc_id` 必须非空且唯一。自定义 CSV 或 Parquet 使用同样的基础列；如果带有 WARC 来源字段，流水线会继续保留。

## 第一步：规范化并计算指纹

第一个 Batch UDF 规范化 `body`，生成 5-token shingle，以 seed 42 计算 64 个确定性的 BLAKE2b-64 MinHash 值，再拆成 8 个 band，每个 band 8 行：

~~~python
fingerprinted_rel = documents.map_batches(
    importable_fingerprint_documents_batch(),
    schema=FINGERPRINT_SCHEMA,
    batch_size=args.batch_size,
    **udf_options,
)
~~~

规范化顺序是 Unicode NFD、去组合音标、转小写、标点转空格和连续空白压缩。空正文的 band 列表为空，不会进入 LSH bucket。少于 5 个 token 的非空正文会整体形成一个有序 shingle，因此 `alpha beta` 和 `beta alpha` 不会被折叠成同一个集合。

8×8 banding 的理论候选概率是 `1-(1-s^8)^8`。在精确接受阈值 `s=0.7` 处，概率为 `0.378122`。这是 MinHash 独立性假设下的召回取舍，不是准确率结果。

## 第二步：展开 band 并限制候选增长

固定的 8 个 band 通过 SQL `UNION ALL` 和 `list_extract(f.lsh_bands, ...)` 展开：

~~~python
band_queries = [
    f"""
    select
      f.doc_id,
      d.domain,
      {band_index} as band_index,
      list_extract(f.lsh_bands, {band_index + 1}) as lsh_band
    from fingerprinted f
    join documents d using (doc_id)
    where f.shingle_count > 0
    """
    for band_index in range(8)
]
~~~

流水线会检查每条非空指纹是否正好生成 8 行 band 归属记录。`collision_buckets.csv` 列出每个碰撞桶的成员数、域名和潜在文档对数量。

自连接之前，脚本先汇总所有碰撞桶的 `member_count * (member_count - 1) / 2`。如果 `candidate_pair_slots` 超过 `--max-candidate-pair-slots`，任务会在创建候选 Relation 前失败。默认预算为 1,000,000。

## 第三步：生成全局候选

候选连接不按域名分区，因此可以找到跨站镜像：

~~~sql
select
  l.doc_id as left_doc_id,
  r.doc_id as right_doc_id,
  l.domain as left_domain,
  r.domain as right_domain,
  count(*) as shared_bands
from band_memberships l
join band_memberships r
  on l.band_index = r.band_index
 and l.lsh_band = r.lsh_band
 and l.doc_id < r.doc_id
group by l.doc_id, r.doc_id, l.domain, r.domain
~~~

`l.doc_id < r.doc_id` 去掉自身配对和对称重复；聚合会合并同一文档对在多个 band 中的碰撞。

`candidate_summary.csv` 以全局 `n(n-1)/2` 为分母计算 `candidate_reduction_ratio`。`domain_summary.csv` 只提供每个域名的内部诊断，不能替代全局基线。

## 第四步：用精确 Jaccard 复核

第二个 Batch UDF 计算：

- 完整 5-token shingle set 的精确 Jaccard；不足 5 个 token 时使用单个有序全文 shingle；
- MinHash signature overlap，作为近似误差诊断。

只有精确 Jaccard 大于等于 0.7 才建立重复边。近似分数高但精确分数不足的候选会记录为 `minhash_only_rejected`。

~~~python
scored_pairs_rel = candidate_pairs.map_batches(
    importable_score_pairs_batch(),
    schema=SCORED_PAIR_SCHEMA,
    batch_size=100,
    **udf_options,
)
~~~

LSH 只负责召回候选，Jaccard 负责最终接受；每条候选都会保留具体原因。

## 第五步：生成稳定簇

重复边组成无向图，递归 CTE 计算连通分量：

~~~sql
with recursive
edges as (
  select doc_id as src_doc_id, doc_id as dst_doc_id from documents
  union
  select left_doc_id, right_doc_id from duplicate_pairs
  union
  select right_doc_id, left_doc_id from duplicate_pairs
),
reach(src_doc_id, dst_doc_id) as (
  select src_doc_id, dst_doc_id from edges
  union
  select r.src_doc_id, e.dst_doc_id
  from reach r
  join edges e on e.src_doc_id = r.dst_doc_id
)
~~~

最小成员 `doc_id` 作为稳定簇根。代表选择独立进行：优先较新的日期，其次较多 token，最后按 `doc_id`。`cluster_inspection.csv` 列出每个多成员簇、代表、成员和最弱接受边。

## 默认结果

固定样例的输出为：

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

18 条重复边中有 6 条完全重复和 12 条近重复。24 条输入形成 6 个三成员重复簇和 6 个单文档簇，最终保留 12 个代表。

这些数字用于固定代码、样例和文档契约，不是精确率、召回率、吞吐或网页规模基准。

## 输出产物

Relation writer 直接写出审核与发布结果：

~~~python
duplicate_pairs.write_csv(str(output_dir / "duplicate_pairs.csv"))
cluster_inspection.write_csv(str(output_dir / "cluster_inspection.csv"))
collision_buckets.write_csv(str(output_dir / "collision_buckets.csv"))
domain_summary.write_csv(str(output_dir / "domain_summary.csv"))
fingerprinted.write_parquet(str(output_dir / "fingerprinted.parquet"))
representatives.write_parquet(str(output_dir / "deduped_documents.parquet"))
~~~

| 文件 | 内容 |
| --- | --- |
| `source_blocks.parquet` | 本次运行实际使用的标准化输入 |
| `fingerprinted.parquet` | 规范化文本、shingle、MinHash 和 band |
| `collision_buckets.csv` | LSH 热桶和候选槽位诊断 |
| `candidate_summary.csv` | 全局文档对空间、预算、候选和缩减率 |
| `domain_summary.csv` | 域名内部候选诊断 |
| `scored_pairs.parquet` | 每个候选的精确和近似分数 |
| `duplicate_pairs.csv` | 通过阈值的重复图边 |
| `duplicate_summary.csv` | 完全/近重复及 WARC 关系统计 |
| `clusters.csv` | 稳定簇归属 |
| `cluster_inspection.csv` | 多成员簇审核视图 |
| `deduped_documents.parquet` | 选中的代表文档 |
| `source_records.csv` | 使用本地 Common Crawl 来源时的 WARC 记录 |
| `source_summary.csv` | 输入及来源统计 |
| `manifest.json` | 来源分类、snapshot_verification、算法、行数和执行后端 |

## 运行

安装依赖并运行离线样例：

依赖文件会从公共 PyPI 解析已验证的 Vane 正式发布包；直接安装 Vane 的等价命令是 `pip install vane-ai`。

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
VANE_RUNNER=local-fast .venv/bin/python src/web_text_deduplication.py
~~~

聚焦测试：

~~~bash
VANE_RUNNER=local-fast .venv/bin/python -m unittest -v \
  tests.test_web_text_deduplication \
  tests.test_common_crawl_source
~~~

重新生成仓库原创样例：

~~~bash
.venv/bin/python scripts/prepare_web_text_deduplication_fixture.py
~~~

生成器必须重新得到固定哈希和预期结果。

## 可选 Common Crawl 路径

这条路径需要联网并显式启用。运行前请阅读 [Common Crawl 使用条款](https://commoncrawl.org/terms-of-use)，并核对所选来源网站的条款。

仓库中的目标清单只使用 IANA 示例域名。先在本地生成 WARC 记录清单和抽取快照：

~~~bash
VANE_RUNNER=local-fast .venv/bin/python scripts/prepare_web_text_deduplication_data.py \
  --refresh-index \
  --acknowledge-common-crawl-terms
~~~

生成文件位于已被 gitignore 的 `workspace/web_text_deduplication/`，并标记 `redistribution_status=local_only_rights_review_required`。这些文件不应提交到仓库。

对本地快照运行去重：

~~~bash
VANE_RUNNER=local-fast .venv/bin/python src/web_text_deduplication.py \
  --input workspace/web_text_deduplication/common_crawl_blocks.parquet \
  --snapshot-metadata workspace/web_text_deduplication/common_crawl_snapshot.json \
  --output-dir output/web_text_deduplication_common_crawl
~~~

也可以用准备好的记录清单实时读取 WARC 字节范围：

~~~bash
VANE_RUNNER=local-fast .venv/bin/python src/web_text_deduplication.py \
  --source common-crawl \
  --record-manifest workspace/web_text_deduplication/common_crawl_records.csv \
  --acknowledge-common-crawl-terms \
  --output-dir output/web_text_deduplication_live
~~~

WARC 路径检查 HTTP 206、字节长度和目标 URI，并交叉比对 WARC 中的 `payload-digest` 头与固定索引摘要。脚本还会检查 HTML 类型、最大文件大小和抽取行数。遇到嵌套文本块时，抽取器优先保留最深层文本，父节点只保留自身的直接文本；重复正文会在应用每页行数限制前去除。

## 自定义输入

任何包含六个基础字段的 CSV 或 Parquet 都可以接入：

~~~bash
VANE_RUNNER=local-fast .venv/bin/python src/web_text_deduplication.py \
  --input path/to/documents.parquet \
  --output-dir output/custom_dedup
~~~

自定义输入标记为 `user_managed`。通过 `--snapshot-metadata` 可以额外校验路径、字节数、SHA-256、行数及可选的 WARC 记录清单；校验通过也不会把自定义数据改标为 `repository_fixture`，该标签只用于仓库内默认输入与默认元数据的组合。

## 当前范围

- 离线样例用于固定运行行为，不代表真实网页分布。
- LSH 是概率算法；精确阶段可以移除候选中的误判，但无法找回从未共享 band 的文档对。
- 热桶和递归图闭包可能产生较高开销。候选槽位门禁限制了本示例的展开规模，但不代表分布式性能。
- HTML 抽取器会消除父子文本重叠，但不检查页面安全、隐私、robots 策略或内容权利。
