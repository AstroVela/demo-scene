# 多模态训练数据发布

训练资产进入训练任务前，通常需要完成解码、质量检查、授权检查和格式统一。文档、图片、音频和文本的检查方式不同，但下游需要一份稳定的数据契约，才能知道哪些记录可以发布、哪些记录被拒绝，以及拒绝原因是什么。

如果把所有模态塞进同一个 UDF，处理逻辑和输出 schema 很容易纠缠在一起。本示例使用四条独立的 Arrow batch UDF 分支处理不同模态，再通过 Relation `union` 合并成统一的类型化结果。发布门禁只读取公共字段，不需要理解每种媒体的解析过程。

## 案例范围

输入是一份整理好的训练资产清单。示例通过 Relation 读取数据，为每种模态运行独立的 Batch UDF 分支，再应用统一发布规则，写出发布清单、拒绝记录和模态汇总。

默认输入包含 Apache Arrow 和 Wikimedia Commons 的 5 个公开文件。创建 Relation 前，脚本会根据固定快照校验资产清单、来源清单和文件哈希。当前处理范围是 UTF-8/Markdown、SVG 和 PCM WAV，不包括 OCR、ASR、语义匹配、embedding 或模型自动标注。

### 示例数据

准备脚本从公开 URI 下载这 5 个文件，并用预期 SHA-256 固定内容。日常运行直接读取仓库快照，不需要联网，同时保留 `source_uri`、来源页面、版本和许可证信息。`manifest.json` 记录校验状态，以及资产清单、来源清单和快照元数据的哈希。

固定快照让文件处理和发布结果可以复现。这 5 条记录只是示例输入，不代表真实训练数据分布。

```text
public_sources.csv
→ prepare_multimodal_training_data.py 下载并校验
→ assets/ + training_assets.csv + public_snapshot.json
→ multimodal_training_data.py 解析、门禁、汇总
→ training_release + rejected_records + manifest
```

## 初始化

下面的配置对应完整脚本的默认值。`UDF_OPTIONS` 为空时由 Vane 选择 Batch UDF 执行后端；需要固定时，可以传入 `{"execution_backend": "subprocess_task"}` 或对应的 Ray task 配置。

```python
from pathlib import Path

import vane

from examples.multimodal_training_data import (
    SUPPORTED_MODALITIES,
    TRAINING_FEATURE_SCHEMA,
    importable_batch_function,
    project_raw_assets,
    validate_input_path,
    validate_modalities,
)

INPUT_PATH = Path("data/multimodal_training_data/training_assets.csv")
OUTPUT_DIR = Path("output/multimodal_training_data")
BATCH_SIZE = 2
UDF_OPTIONS = {}

conn = vane.connect()
```

## 输入数据

默认资产清单位于 `data/multimodal_training_data/training_assets.csv`。每行描述一项训练资产：

| 字段 | 含义 |
| --- | --- |
| `record_id` | 资产唯一标识 |
| `modality` | `document`、`image`、`audio` 或 `text` |
| `source_uri` | 原始资产位置 |
| `license_id` | 发布策略要求的授权标识 |
| `split` | 数据集划分 |
| `mime_type` | 媒体类型 |
| `text` | 文本正文或媒体说明 |
| `content_path` | 仓库内经过哈希校验的公开资产快照路径 |
| `expected_sha256` | 下载源内容的预期 SHA-256 |
| `content_base64` | 自定义或合成样例可选的内嵌内容 |
| `metadata_json` | 模态相关的补充元数据 |

脚本从 Relation reader 开始，并在读取阶段把可空字符串转为空字符串或空 JSON：

```python
input_path = validate_input_path(INPUT_PATH)
input_relation = conn.read_csv(str(input_path), header=True)
raw_assets_rel = project_raw_assets(input_relation)

raw_assets_rel.order("record_id").to_table("raw_assets")
raw_assets = conn.sql("select * from raw_assets")
modalities = validate_modalities(raw_assets)
```

`validate_modalities` 只收集去重后的模态名称，并拒绝 `audio`、`document`、`image`、`text` 以外的值；不会把完整资产 Relation 收集到 Python。默认清单包含 5 条公开记录，覆盖四种模态。

公开来源及其固定版本、许可证页面和预期哈希记录在 `data/multimodal_training_data/public_sources.csv`；文件位于 `data/multimodal_training_data/assets/`，快照审计信息位于 `public_snapshot.json`。处理前，脚本会校验资产清单和来源清单的哈希、资产数量、模态列表、路径、字节数和文件哈希。处理函数读取每项资产时还会再次校验 SHA-256，并保留原始 `source_uri`。

默认快照使用以下来源：

| 记录 | 来源 | 许可证 | 用途 |
| --- | --- | --- | --- |
| `arrow-project-readme` | [Apache Arrow `apache-arrow-18.1.0` README](https://github.com/apache/arrow/blob/apache-arrow-18.1.0/README.md) | [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) | 文档解码与行数统计 |
| `arrow-python-readme` | [Apache Arrow Python README](https://github.com/apache/arrow/blob/apache-arrow-18.1.0/python/README.md) | [Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) | 文本规范化与 token 统计 |
| `wikimedia-generic-file` | [Wikimedia Commons 512×512 SVG](https://commons.wikimedia.org/w/index.php?title=File:Generic_File.svg&oldid=1132223570) | [CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/) | 可发布图片 |
| `wikimedia-download-icon` | [Wikimedia Commons 136×168 SVG](https://commons.wikimedia.org/w/index.php?title=File:Download.svg&oldid=1012581370) | [CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/) | 低分辨率拒绝路径 |
| `wikimedia-audio` | [Wikimedia Commons 2.4 秒 WAV](https://commons.wikimedia.org/w/index.php?title=File:Audio.wav&oldid=1135746966) | [CC0-1.0](https://creativecommons.org/publicdomain/zero/1.0/) | WAV 指标提取 |

重新下载并验证公开来源：

```bash
.venv/bin/python scripts/prepare_multimodal_training_data.py --refresh
```

脚本限制单项资产不超过 5 MiB，并要求下载内容与 `public_sources.csv` 中的 SHA-256 完全一致。上游内容变化会使刷新失败，不会改写固定快照。

## 第一步：按模态处理资产

四种模态共享输入列，但执行不同的检查：

| 模态 | 默认解析 | 风险标记 |
| --- | --- | --- |
| `document` | 将文件内容按 UTF-8 解码并统计行数 | 无效 UTF-8 或空内容产生 `invalid_utf8`、`empty_document` |
| `image` | 解析 SVG XML，读取显式尺寸或 `viewBox` | 无效格式、缺少尺寸或低于 512×512 |
| `audio` | 读取 WAV 头中的采样率和帧数 | 无效 WAV、非正采样率或短于 0.005 秒 |
| `text` | 解码 UTF-8、规范空白并统计 token | 无效 UTF-8 或少于 4 个 token |

### 读取并校验文件

记录优先读取 `content_path` 指向的快照；自定义资产清单仍可使用 Base64，没有文件或 Base64 时才使用 `text` 的 UTF-8 字节。只要提供 `expected_sha256`，处理前就会校验内容：

```python
def decode_payload(row):
    content_path = str(row.get("content_path") or "").strip()
    if content_path:
        path = Path(content_path)
        if not path.is_absolute():
            path = REPO_ROOT / path
        payload = path.read_bytes()
    else:
        encoded = str(row.get("content_base64") or "").strip()
        payload = (
            base64.b64decode(encoded, validate=True)
            if encoded
            else str(row.get("text") or "").encode("utf-8")
        )

    expected = str(row.get("expected_sha256") or "").strip()
    if expected and hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError("payload SHA-256 mismatch")
    return payload
```

文件缺失、Base64 无效或哈希不一致会让当前 batch 失败，因为这些情况表示快照完整性已经破坏。解析和质量问题则保留为行级风险标记。

### 执行模态规则

每个处理函数只填写本模态适用的指标。以下代码展示了四条分支的关键条件：

```python
def decode_utf8(payload, flags):
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        flags.append("invalid_utf8")
        return payload.decode("utf-8", errors="replace")

# document
text = decode_utf8(payload, flags).strip()
if not text:
    flags.append("empty_document")

# image: parse SVG XML and read dimensions
if mime_type == "image/svg+xml":
    metrics["width"], metrics["height"] = svg_dimensions(payload)
    if not metrics["width"] or not metrics["height"]:
        flags.append("missing_dimensions")
    elif metrics["width"] < 512 or metrics["height"] < 512:
        flags.append("low_resolution")
else:
    flags.append("invalid_image")

# audio
with wave.open(io.BytesIO(payload), "rb") as audio_file:
    sample_rate = audio_file.getframerate()
    if sample_rate <= 0:
        raise wave.Error("sample rate must be positive")
    duration = audio_file.getnframes() / float(sample_rate)
    if duration < 0.005:
        flags.append("audio_too_short")

# text
payload = decode_payload(row)
text = " ".join(decode_utf8(payload, flags).split())
if len(tokenize(text)) < 4:
    flags.append("text_too_short")
```

无效 UTF-8 会保留一份替换解码后的审核文本，但记录会带上 `invalid_utf8` 并被拒绝。音频函数会捕获 `EOFError` 和 `wave.Error`，非正采样率也会归入 `invalid_audio`。图片函数使用标准库 XML 解析器验证 SVG 根元素，并支持整数、小数像素尺寸和 `viewBox` 回退；PNG 和 JPEG 解码不在当前范围内。

### 应用公共发布规则

四个处理函数最后都调用 `build_feature_row`。这个函数补充内容哈希、字节数和 token 数，并应用同一套授权与质量规则：

```python
if not str(row.get("license_id") or "").strip():
    flags.append("missing_license")

quality_score = round(
    max(0.0, quality_score - 0.25 * ("missing_license" in flags)),
    3,
)
decision = (
    "accepted"
    if not flags and quality_score >= 0.8
    else "rejected"
)

result = {
    "record_id": row["record_id"],
    "modality": row["modality"],
    "content_sha256": hashlib.sha256(payload).hexdigest(),
    "byte_size": len(payload),
    "token_count": len(tokenize(content_text)),
    "quality_score": quality_score,
    "decision": decision,
    "risk_flags": flags,
    "media_metrics": metrics,
    "feature_json": json.dumps(features, sort_keys=True),
}
```

这里的判定是严格门禁：只要存在任一风险标记，记录就会被拒绝；`0.8` 是对无风险记录的最低质量要求。缺少授权还会额外扣除 `0.25` 质量分。

## 第二步：合并类型化结果

脚本为每种模态选择独立的 batch 函数，在 Relation 上过滤输入，再调用 `map_batches`：

```python
stage_functions = {
    "document": "process_document_batch",
    "image": "process_image_batch",
    "audio": "process_audio_batch",
    "text": "process_text_batch",
}

relations = []
for modality in SUPPORTED_MODALITIES:
    source = raw_assets.filter(
        f"modality = '{modality}'"
    ).order("record_id")
    relations.append(
        source.map_batches(
            importable_batch_function(stage_functions[modality]),
            schema=TRAINING_FEATURE_SCHEMA,
            batch_size=BATCH_SIZE,
            **UDF_OPTIONS,
        )
    )

features_rel = relations[0]
for relation in relations[1:]:
    features_rel = features_rel.union(relation)

features_rel.order(
    "modality, record_id"
).to_table("feature_records")
```

显式分支允许每个处理函数独立测试或替换。所有分支声明相同的 `TRAINING_FEATURE_SCHEMA`，因此 `union` 后仍是一张类型稳定的 Relation。

公共字段包括 source、授权、正文、SHA-256、字节数、token 数、质量分、发布结论和风险标记。两列保留复杂类型：

- `risk_flags` 是 `VARCHAR[]`，可以保存一条记录的多个问题。
- `media_metrics` 是 `STRUCT(width, height, duration_seconds, sample_rate)`，每种模态只填写适用字段。

图片尺寸和音频采样率因此可以直接在 SQL 中查询。`feature_json` 只承载模态专属、尚未稳定为公共 schema 的补充信息。

## 第三步：生成发布与拒绝产物

处理器已经把公共门禁结果写入 `decision`。Relation 过滤形成两个互斥产物：

```python
feature_records = conn.sql("select * from feature_records")
training_release_rel = feature_records.filter(
    "decision = 'accepted'"
).order("split, modality, record_id")
rejected_records_rel = feature_records.filter(
    "decision = 'rejected'"
).order("quality_score, modality, record_id")

conn.sql("drop table if exists training_release")
training_release_rel.to_table("training_release")
training_release = conn.sql("select * from training_release")

conn.sql("drop table if exists rejected_records")
rejected_records_rel.to_table("rejected_records")
rejected_records = conn.sql("select * from rejected_records")
```

模态汇总继续保留为 Relation 聚合：

```python
modality_summary_rel = feature_records.aggregate(
    """
    modality,
    count(*) as records,
    sum(byte_size) as total_bytes,
    round(avg(quality_score), 3) as avg_quality_score,
    sum(case when decision = 'accepted' then 1 else 0 end) as accepted,
    sum(case when decision = 'rejected' then 1 else 0 end) as rejected
    """
).order("modality")

conn.sql("drop table if exists modality_summary")
modality_summary_rel.to_table("modality_summary")
modality_summary = conn.sql("select * from modality_summary")
```

默认汇总结果：

| 模态 | 输入 | 平均质量分 | 发布 | 拒绝 |
| --- | ---: | ---: | ---: | ---: |
| `audio` | 1 | 1.000 | 1 | 0 |
| `document` | 1 | 1.000 | 1 | 0 |
| `image` | 2 | 0.750 | 1 | 1 |
| `text` | 1 | 1.000 | 1 | 0 |

拒绝记录是：

| `record_id` | 质量分 | 风险标记 | 原因 |
| --- | ---: | --- | --- |
| `wikimedia-download-icon` | 0.500 | `low_resolution` | 真实 SVG 尺寸为 136×168，低于 512×512 门禁 |

默认 5 条真实公开记录最终发布 4 条、拒绝 1 条。这些数字用于核对来源、解析和发布数据流，不是训练数据质量基准。

## 写出产物

所有表格结果通过 Relation writer 写出：

```python
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
feature_records.write_parquet(str(OUTPUT_DIR / "feature_records.parquet"))
training_release.write_parquet(str(OUTPUT_DIR / "training_release.parquet"))
rejected_records.write_csv(str(OUTPUT_DIR / "rejected_records.csv"))
modality_summary.write_csv(str(OUTPUT_DIR / "modality_summary.csv"))
```

| 文件 | 用途 |
| --- | --- |
| `feature_records.parquet` | 全量类型化特征、风险标记和判定结果 |
| `training_release.parquet` | 可以交给训练任务的发布清单 |
| `rejected_records.csv` | 被拒绝的记录和具体风险原因 |
| `modality_summary.csv` | 各模态的数量、字节数、平均质量分和判定统计 |
| `manifest.json` | 输入、`source_mode`、快照校验状态与哈希、schema 版本、发布策略、行数、执行后端和产物清单 |

Parquet 保留 `risk_flags` 列表和 `media_metrics` 结构体。拒绝记录与模态汇总使用 CSV，便于直接复核门禁结果。

## 运行完整脚本

教程中的 Relation 和 Batch UDF 阶段都包含在同一个可执行脚本中：

```bash
.venv/bin/python examples/multimodal_training_data.py
```

默认运行输出：

```text
Raw assets: 5
Released records: 4
Rejected records: 1
Output directory: output/multimodal_training_data
```

需要复现原来的纯合成错误样本时运行：

```bash
.venv/bin/python examples/multimodal_training_data.py \
  --input data/multimodal_training_data/synthetic_training_assets.csv
```

使用 `--input` 可以替换资产清单，`--batch-size` 控制各模态 UDF 的输入批次，`--execution-backend` 可以显式选择 `subprocess_task` 或 `ray_task`，`--output-dir` 用于修改产物目录。脚本当前需要 `native` Relation runner；task 执行后端只影响四条 Python UDF 分支。

三条命令对应不同目的：

| 命令 | 是否联网 | 用途 |
| --- | --- | --- |
| `examples/multimodal_training_data.py` | 否 | 默认公开快照的数据处理与发布 |
| `scripts/prepare_multimodal_training_data.py --refresh` | 是 | 重新下载来源并验证哈希 |
| `examples/multimodal_training_data.py --input .../synthetic_training_assets.csv` | 否 | 回归无效图片和缺少授权等错误路径 |

聚焦验证：

```bash
.venv/bin/python -m unittest -v tests.test_multimodal_training_data
```

## 当前范围

- 文档和文本分支只解码 UTF-8，不解析 PDF、Office 文件，也不执行 OCR。
- 图片分支解析 SVG 尺寸，音频分支读取 PCM WAV 元数据；栅格图片解码、ASR 和更广的媒体质量检查不在示例内。
- 文件缺失、Base64 解码错误和 SHA-256 不一致会终止 batch；无效 UTF-8 和格式异常的媒体会写入结构化风险标记。
- `0.8` 质量阈值和 512×512 图片阈值是固定的示例规则，不是经过训练质量实验校准的指标。
