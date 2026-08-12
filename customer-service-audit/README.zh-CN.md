# 使用 Vane 构建可审计的客服录音质检流程

[English](README.md)

客服质检很少从结构化数据开始：原始通话录音存放在对象存储中，而质检团队需要的指标——问题分类、客户情绪、紧急程度、是否需要回访——必须从语音中提取。本演示把这些录音变成一条完整的 Vane Relation 流水线：探测音频质量、用真实的 faster-whisper ASR 引擎转写每通通话、用真实的本地 Qwen 模型提取质检指标、通过确定性的 DuckDB SQL 校验，最后将每通通话一个分析 JSON 加一份批次汇总原子地发布回 MinIO。

合成 fixture 覆盖四种质检结果：

| 通话 | 预期 problem_category | 预期 customer_sentiment |
| --- | --- | --- |
| `CALL-REFUND-ANGRY` | `refund_request` | `very_negative` |
| `CALL-BILLING-CALM` | `billing_dispute` | `neutral` |
| `CALL-TECH-FRUSTRATED` | `technical_support` | `negative` |
| `CALL-PRAISE-HAPPY` | `praise` | `very_positive` |

> 这些是质检发现，不是官方合规结论或人事处理决定。

## 为什么用 Vane

Vane 是面向多模态数据的混合计算引擎：它让对象存储定位符、音频探测、SQL、无状态 Python UDF、有状态 Actor 和 AI 模型在同一条可组合、可追溯的 Relation 流水线中协同工作。Vane 还把流水线逻辑与执行后端解耦：签入的配置默认使用 `ray` Runner，并已在本地 Ray runtime 上端到端验证 MinIO、faster-whisper Actor 与 Qwen 完整路径。Local 仍是受支持的回退路径，在 driver 上运行一个 faster-whisper 引擎并挂载不可变的结果查找表。

## 架构

```text
MinIO recordings（4 段合成通话）
  -> 对象存在性、SHA-256 与音频探测事实
  -> faster-whisper ASR 边界（有状态 Actor 或 driver 查找表）
  -> 转写质量门禁
  -> Qwen 质检分析（问题分类、情绪、紧急程度、回访标记）
  -> 严格 JSON 契约校验
  -> 确定性审核处置 SQL
  -> 每通通话一个分析 JSON + 批次汇总发布到 MinIO
```

核心 Relation 路径：

```text
stg_calls / stg_run_config
  -> int_call_inputs -> int_call_probe_udf -> int_call_facts
  -> int_call_transcript_udf -> int_transcript_quality_udf -> int_transcript_facts
  -> int_call_analysis_ai (vane.ai.prompt)
  -> int_analysis_validation_inputs -> int_analysis_validation_udf
  -> int_analysis_facts -> call_audit_report
```

1. 把 MinIO `recordings/` 前缀列举为有序的通话清单；运行时从不直接读取本地 fixture 文件。
2. 通过 MinIO UDF 探测每段录音：对象存在性、SHA-256，以及只用标准库完成的 WAV 头检查（时长、声道数、采样率、`audio_usable` 门禁）。不可用音频被路由到人工审核处置，永远不会进入 ASR。
3. 用 faster-whisper 转写每段可用录音（`zh`、`beam_size=5`、启用 VAD）。Local 在 driver 上运行一个引擎并挂载按 `(bucket, object_key)` 键控的不可变查找表；Ray 挂载可复用的有状态 Actor。两条路径向 SQL 返回相同的转写 JSON 契约。
4. 在任何转写文本送模型之前，先套用确定性的转写质量门禁（`min_text_chars`、ASR 状态、语言置信度）。
5. 只把可用转写发给 Qwen，并使用加固的质检提示词：转写内容被限定为不可信证据，响应必须满足完整 JSON Schema，转写中的提示注入内容永远不会被遵循。
6. 通过 `validate_call_analysis_json` 校验每个不可信的模型响应（枚举域、分数区间、必填字段），产出 `success` 记录或带不确定原因的确定性 `invalid_response` 发现。
7. 在纯 SQL 中推导可审核的 `review_disposition`（`audited`、`review_unusable_audio`、`review_low_quality_transcript`、`review_invalid_analysis`），然后把每通通话一个分析 JSON 和 `batch_summary.json` 发布到 `analysis/` 前缀，并先清理旧输出。

## 运行演示

本演示要求 CPython 3.12，并固定 `vane-ai[openai]==0.1.0`。已验证环境使用 uv 从 PyPI 安装 Vane 及其依赖。请按照[完整 runbook](docs/runbook.zh-CN.md) 执行准确的安装命令，并准备运行中的 MinIO 和 Qwen 服务。然后执行：

```bash
python scripts/run_demo.py e2e
```

`runtime.yml` 默认为 `runner: ray`，该路径已在本地 Ray runtime 上使用真实 fixture、ASR 和 Qwen 服务跑通。真实多节点目标集群仍需针对共享路径、worker 凭据和资源容量单独执行基础设施 smoke test。

成功运行会打印：

```text
loaded 4 call recordings
published 4 call analysis files
verified 4 call analyses: CALL-BILLING-CALM=(billing_dispute,neutral), CALL-PRAISE-HAPPY=(praise,very_positive), CALL-REFUND-ANGRY=(refund_request,very_negative), CALL-TECH-FRUSTRATED=(technical_support,negative)
```

没有 AI mock 兜底：服务不可用、音频不可用、非法 AI JSON、运行时不兼容、SQL 失败、发布失败都会以非零码退出。

## 实现布局与 Vane 使用点

```text
customer-service-audit/
├── pyproject.toml
│   # 声明 Python/运行时依赖，包括固定的 Vane 版本。
│
├── requirements.txt
│   # 按 pyproject.toml 安装本源码树及其 fast-test extra。
│
├── runtime.yml
│   # 配置 Vane Runner（默认 Local）、MinIO、faster-whisper 与 Qwen。
│
├── scripts/
│   ├── run_demo.py
│   │   # 在调用 CLI 之前验证 CPython 3.12、Vane 精确版本标识、所需 API、
│   │   # 包来源与环回网络。
│   └── make_fixtures.py
│       # 重新生成 4 段确定性通话录音（edge-tts -> 16 kHz 单声道 PCM WAV）；
│       # 仅在重新制作资产时运行。
│
├── src/customer_service_audit/
│   ├── cli.py
│   │   # 分发 fixture、run、verify、e2e 命令。
│   │
│   ├── config.py
│   │   # 加载并严格校验 runtime.yml 为类型化运行时配置。
│   │
│   ├── fixture_loader.py
│   │   # 把打包的 WAV fixture 上传到 MinIO 并刷新前缀。
│   │
│   ├── minio_store.py
│   │   # 封装 MinIO 读取、存在性检查、SHA-256、上传与清理。
│   │
│   ├── pipeline.py
│   │   # 持有 driver DuckDB catalog 与独立 Runner 连接，用临时 Parquet 衔接
│   │   # 两者边界，编排完整 DAG。
│   │   └── [Vane] vane.configure 选择 Local 或 Ray；Relation.write_parquet
│   │
│   ├── vane_udfs.py
│   │   # [Vane] 无状态 @vane.func 探测/校验器与有状态 AsrTranscribeActor
│   │   #（faster-whisper，懒加载）。
│   │
│   ├── call_ai.py
│   │   # [Vane] 真实 AI 边界：Local 用 vane.ai.load_provider，Ray 用
│   │   # vane.ai.prompt，配合加固的质检提示词与 JSON Schema。
│   │
│   ├── output_writer.py
│   │   # 校验输出契约并发布每通通话 JSON 与批次汇总。
│   │
│   ├── verify_outputs.py
│   │   # 重读 MinIO 分析 JSON 并断言 4 个 fixture 结果。
│   │
│   └── sql/  (staging / intermediate / marts — 完整 SQL DAG)
│
└── tests/fast/
    # 针对配置、UDF 契约与输出形态的轻量快速测试。
```

## Fixture 来源

`src/customer_service_audit/assets/` 中的 4 段打包录音是用 edge-tts 合成的中文客服对话，重编码为 16 kHz 单声道 PCM WAV；它们是确定性、可再分发的演示资产。预期结果在 `fixture_loader.EXPECTED_ANALYSES` 中断言。如果重新生成资产，必须在发布前重新推导并重新验证预期结果。
