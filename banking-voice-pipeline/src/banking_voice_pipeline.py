#!/usr/bin/env python3
"""Run the Chinese banking voice pipeline with Vane and vane.ai.jev.

Install the pinned dependencies from requirements.txt (see README), including the
TestPyPI Vane dev build that provides ``Relation.jev()``. CUDA needs cuBLAS 12 and
cuDNN 9 on LD_LIBRARY_PATH:
https://github.com/SYSTRAN/faster-whisper#gpu
Set TYPESAFE_API_KEY, then run from this demo directory with a new output directory;
Ray workers must also import the installed Vane package:
    .venv/bin/python src/banking_voice_pipeline.py --output-dir output/banking_voice_pipeline

The pipeline follows multimodal_inference_benchmarks/audio_transcription/vane_main.py:
Parquet -> CPU decode/resample -> GPU ASR actors -> Jev CPU actors -> final results.
MInDS-14 clips contain one caller utterance; Whisper handles windowing internally.
Vane defaults to Ray: functions use tasks, callable classes and Jev use actors.
Relations connect every stage directly; only final Parquet/CSV files are written.

Data: PolyAI MInDS-14, zh-CN, CC-BY-4.0, https://huggingface.co/datasets/PolyAI/minds14
The publisher provides only a train split, with 502 Chinese recordings and no test
split. We select 112 recordings by SHA-256(path), without labels, for this demo.
This is a fixed evaluation subset; the example trains no model. Labels are read only
for scoring. The sample has been used during development, and exclusion from upstream
Whisper/Jev training is unknown, so test independence is limited. Results concern
short utterances, not complete multi-turn calls. All suggestions require human
confirmation.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import vane

DATA_REVISION = "40ce77cb32a384e4d50a568e1ec39ac804019d33"
MODEL_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
JEV_MODEL = "jev-1.13.0"
SAMPLE_RATE = 16000
DEFAULT_OUTPUT_DIR = Path("output/banking_voice_pipeline")
# Audio records per Vane batch; Whisper transcribes each record inside the batch.
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "128"))

# Intent: (business definition, processing queue, simple baseline keywords).
# The order matches the pinned MInDS-14 ClassLabel vocabulary.
INTENTS = {
    "abroad": ("咨询银行卡在境外能否使用或是否需要开通。", "cards", ("境外", "国外", "出国")),
    "address": ("修改银行记录中的家庭、邮寄或联系地址。", "account_service", ("地址", "搬家")),
    "app_error": ("银行应用或网上银行报错、无法登录或功能异常。", "digital_support", ("应用", "登录", "报错")),
    "atm_limit": ("查询或调整 ATM 现金提取限额。", "cards", ("取款限额", "提款限额", "取款上限")),
    "balance": ("查询银行账户当前余额或可用资金。", "account_information", ("余额",)),
    "business_loan": ("咨询或申请用于企业经营的贷款。", "business_lending", ("贷款",)),
    "card_issues": ("银行卡不能付款、取款或其他使用故障，不包括主动冻结。", "cards", ("刷不了", "卡失效", "卡不能用")),
    "cash_deposit": ("询问如何或在哪里把现金存入账户。", "cash_deposits", ("存现金", "存款", "存钱")),
    "direct_debit": ("设置、取消或查询商家从账户自动扣款的授权。", "payments", ("自动扣款", "直接扣款", "自动扣费")),
    "freeze": ("冻结或阻止丢失、被盗或有风险的银行卡或账户，停止其交易。", "card_security", ("冻结", "挂失")),
    "high_value_payment": ("询问或办理大额转账、付款及其额度限制。", "payments", ("大额", "转账限额")),
    "joint_account": ("开立或管理多人共同持有的联名账户。", "account_service", ("联名", "共同账户")),
    "latest_transactions": (
        "查询最近交易、账户流水或收支明细。",
        "account_information",
        ("最近交易", "交易记录", "流水", "明细"),
    ),
    "pay_bill": ("主动支付账单或询问如何缴费，不包括自动扣款授权。", "payments", ("账单", "缴费")),
}

WORDS = pa.list_(
    pa.struct([("role", pa.string()), ("start_ms", pa.int64()), ("end_ms", pa.int64()), ("text", pa.string())])
)
DECODED = pa.schema([("record_id", pa.string()), ("waveform", pa.list_(pa.float32())), ("error", pa.string())])
TRANSCRIBED = pa.schema(
    [
        ("record_id", pa.string()),
        ("text", pa.string()),
        ("segments", WORDS),
        ("baseline_intent", pa.string()),
        ("error", pa.string()),
    ]
)


def questions():
    from typesafe_sdk import Choice, Noul, Score

    return {
        "intent": Choice(
            instructions="根据 conversation 判断 caller 的主要银行业务诉求，包括已解决的原始诉求。结合客服上下文，但不要把客服无关发言当作客户请求。信息不足选 other。",
            criteria={**{name: spec[0] for name, spec in INTENTS.items()}, "other": "其他业务或信息不足。"},
        ),
        "urgency": Score(
            instructions="在 analysis_time 时，客户尚未解决的事项有多紧急？已完成的操作不算待办，不臆测损失或期限。",
            criteria=[
                "事项已解决，或仅作一般咨询，没有未完成的紧急事项。",
                "常规请求未完成，可按正常周期处理，没有迫近期限。",
                "客户明确需要尽快处理，或正常业务受影响，但无即时损失风险。",
                "有明确的当天期限或严重业务阻断，需要优先处理。",
                "仍有盗刷、账户被盗或资金继续损失的即时风险，需立即介入。",
            ],
        ),
        "dissatisfaction": Score(
            instructions="仅依据 caller 的文字表达判断不满，不推断音调，也不把问题严重性当作不满。",
            criteria=[
                "中性或礼貌咨询，没有表达不满。",
                "表达轻微困惑、不便或担忧，没有抱怨服务。",
                "明确表达失望、抱怨或对处理不满。",
                "反复或强烈抱怨，对服务明显愤怒。",
                "表达极端愤怒、辱骂，或因服务问题威胁投诉、曝光、销户。",
            ],
        ),
        "needs_human": Noul(
            instructions="在 analysis_time 时是否仍需银行工作人员处理、核实或回复？无人回答的问题、未办理的申请或未完成的跟进为是；结合双方发言，已经解决或完成的操作不计入后续人工需求。"
        ),
        "input_sufficient": Noul(
            instructions="conversation 的转写是否足够清楚，能够识别客户主要诉求？残缺、矛盾或无法理解时为否。"
        ),
    }


def keyword_intent(text):
    text = re.sub(r"\s+", "", text.lower())
    scores = {name: sum(word in text for word in spec[2]) for name, spec in INTENTS.items()}
    winners = [name for name, score in scores.items() if score == max(scores.values()) and score > 0]
    return winners[0] if len(winners) == 1 else "other"


def decode_audio(batch):
    import av
    from faster_whisper.audio import decode_audio as decode

    rows = []
    for row in batch.to_pylist():
        waveform, error = [], None
        try:
            with av.open(io.BytesIO(row["audio_bytes"])) as container:
                if len(container.streams.audio) != 1 or container.streams.audio[0].codec_context.channels != 1:
                    raise ValueError("Expected one mono audio stream")
            waveform = decode(io.BytesIO(row["audio_bytes"]), sampling_rate=SAMPLE_RATE)
            if not len(waveform) or not np.isfinite(waveform).all():
                raise ValueError("Empty or nonfinite waveform")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            waveform = []
        rows.append({"record_id": row["record_id"], "waveform": waveform, "error": error})
    return pa.Table.from_pylist(rows, schema=DECODED)


def transcriber(model_path):
    class Transcribe:
        def __init__(self):
            from faster_whisper import WhisperModel

            self.model = WhisperModel(model_path, device="cuda", compute_type="float16", cpu_threads=2)

        def __call__(self, batch):
            rows = []
            for row in batch.to_pylist():
                words, error = [], row["error"]
                if error is None:
                    try:
                        waveform = np.asarray(row["waveform"], dtype=np.float32)
                        segments, _ = self.model.transcribe(
                            waveform,
                            language="zh",
                            beam_size=5,
                            vad_filter=True,
                            word_timestamps=True,
                            condition_on_previous_text=False,
                        )
                        for segment in segments:
                            if segment.text.strip() and not segment.words:
                                raise ValueError("Missing word timestamps")
                            for word in segment.words or []:
                                words.append(
                                    {
                                        "role": "caller",
                                        "start_ms": round(word.start * 1000),
                                        "end_ms": round(word.end * 1000),
                                        "text": word.word,
                                    }
                                )
                        validate_transcript(words, round(len(waveform) * 1000 / SAMPLE_RATE))
                    except Exception as exc:
                        error = f"{type(exc).__name__}: {exc}"
                text = "".join(word["text"] for word in words).strip()
                rows.append(
                    {
                        "record_id": row["record_id"],
                        "text": text,
                        "segments": words,
                        "baseline_intent": keyword_intent(text) if error is None else None,
                        "error": error,
                    }
                )
            return pa.Table.from_pylist(rows, schema=TRANSCRIBED)

    return Transcribe


def validate_transcript(words, duration_ms):
    text = "".join(word["text"] for word in words).strip()
    if not text or re.search(r"(.{2,20})\1{3,}", re.sub(r"\s+", "", text)):
        raise ValueError("Empty or abnormally repeated transcription")
    if any(not 0 <= word["start_ms"] <= word["end_ms"] <= duration_ms for word in words):
        raise ValueError("Word timestamp outside recording")
    if [word["start_ms"] for word in words] != sorted(word["start_ms"] for word in words):
        raise ValueError("Word timestamps out of order")


def state():
    # NULL skips Jev for failed/suspicious ASR. Only this allowlist reaches the API.
    return vane.sql_expr("""CASE WHEN error IS NULL THEN struct_pack(
        language := 'zh-CN', analysis_time := 'after_message_or_call_end', conversation := segments
    ) END""")


def queue(intent):
    return INTENTS[intent][1] if intent in INTENTS else intent


def metrics(actual, expected):
    f1 = []
    for label in INTENTS:
        tp = sum(a == label and b == label for a, b in zip(actual, expected))
        fp = sum(a == label and b != label for a, b in zip(actual, expected))
        fn = sum(a != label and b == label for a, b in zip(actual, expected))
        f1.append(2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0)
    return {
        "intent_accuracy": sum(a == b for a, b in zip(actual, expected)) / len(expected),
        "macro_f1_14_intents": sum(f1) / len(f1),
        "queue_accuracy": sum(queue(a) == queue(b) for a, b in zip(actual, expected)) / len(expected),
    }


def main(output, limit):
    from huggingface_hub import hf_hub_download, snapshot_download

    if not 1 <= limit <= 502:
        raise ValueError("--limit must be between 1 and 502")
    if BATCH_SIZE <= 0:
        raise ValueError("BATCH_SIZE must be positive")
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        raise ValueError("Set TYPESAFE_API_KEY before running")
    # 官方 zh-CN 只有名为 train 的 502 条录音；文件名不决定本项目如何使用它们。
    # 本例仅做推理，下面按路径哈希固定选取 112 条评测；没有原文的 ID 清单，因此不是同一划分。
    # 若将其作为保留测试集，应在评测前冻结规则和问题定义，避免根据这 112 条的成绩调参。
    dataset = hf_hub_download(
        "PolyAI/minds14", "zh-CN/train-00000-of-00001.parquet", repo_type="dataset", revision=DATA_REVISION
    )
    model_path = snapshot_download(
        "Systran/faster-whisper-small", revision=MODEL_REVISION, allow_patterns=["*.json", "*.bin", "*.txt"]
    )
    output.mkdir(parents=True, exist_ok=False)
    try:
        with vane.connect() as con:
            # 1. Read the same label-free audio rows for both methods.
            raw = con.read_parquet(dataset)
            if raw.count("*").fetchone()[0] != 502:
                raise ValueError("Expected the pinned 502-record Chinese subset")
            source = (
                raw.select(
                    vane.sql_expr("sha256(path)").alias("record_id"),
                    vane.sql_expr("CASE WHEN lang_id = 13 THEN audio.bytes ELSE error('Expected zh-CN') END").alias(
                        "audio_bytes"
                    ),
                )
                .order("record_id")
                .limit(limit)
            )
            selected = {rid for (rid,) in source.select("record_id").fetchall()}
            if len(selected) != limit:
                raise ValueError("Expected unique recording IDs")

            # 2. Decode/resample on CPUs, then transcribe on GPU actors.
            started = time.perf_counter()
            audio = source.map_batches(
                decode_audio,
                schema={
                    "record_id": vane.sqltypes.VARCHAR,
                    "waveform": vane.list_type(vane.sqltypes.FLOAT),
                    "error": vane.sqltypes.VARCHAR,
                },
                batch_size=BATCH_SIZE,
            )
            transcripts = audio.map_batches(
                transcriber(model_path),
                schema={
                    "record_id": vane.sqltypes.VARCHAR,
                    "text": vane.sqltypes.VARCHAR,
                    "segments": vane.sqltype("STRUCT(role VARCHAR, start_ms BIGINT, end_ms BIGINT, text VARCHAR)[]"),
                    "baseline_intent": vane.sqltypes.VARCHAR,
                    "error": vane.sqltypes.VARCHAR,
                },
                batch_size=BATCH_SIZE,
                actor_number=1,
                gpus=1.0,
            )

            # 3. Feed ASR directly to Jev; Vane owns AsyncTypeSafeClient and concurrency.
            judged = transcripts.jev(
                state(),
                questions=questions(),
                model=JEV_MODEL,
                actor_number=4,
                max_concurrency_per_actor=8,
            )

            # 4. Keep result formatting in the same pipeline, then write its final output.
            result = judged.query(
                "judged",
                f"""
                SELECT record_id, text, segments, error, baseline_intent, response,
                    CASE WHEN response IS NULL OR (response ->> '$.model') = '{JEV_MODEL}'
                         THEN response ->> '$.answers.intent.choice'
                         ELSE error('Unexpected Jev model version') END AS intent,
                    (response ->> '$.answers.intent.confidence')::DOUBLE AS intent_confidence,
                    (response ->> '$.answers.urgency.score')::DOUBLE + 1 AS urgency,
                    (response ->> '$.answers.dissatisfaction.score')::DOUBLE + 1 AS dissatisfaction,
                    (response ->> '$.answers.needs_human.noul')::DOUBLE AS needs_human_probability,
                    needs_human_probability >= 0.5 AS needs_human,
                    (response ->> '$.answers.input_sufficient.noul')::DOUBLE AS input_sufficient_probability,
                    true AS review_required,
                    CASE WHEN error IS NOT NULL THEN 'transcript_requires_review'
                         WHEN input_sufficient_probability < 0.5 THEN 'insufficient_input'
                         ELSE 'manual_confirmation' END AS review_reason
                FROM judged
            """,
            )
            queues = " ".join(f"WHEN '{name}' THEN '{spec[1]}'" for name, spec in INTENTS.items())
            result = result.select(
                *result.columns,
                vane.sql_expr(f"CASE intent {queues} ELSE intent END").alias("queue"),
                vane.sql_expr(f"CASE baseline_intent {queues} ELSE baseline_intent END").alias("baseline_queue"),
            )
            result.write_parquet(str(output / "results.parquet"))
            print(f"ASR + keywords + Jev: {time.perf_counter() - started:.2f}s")

            # 5. CSV and evaluation read the final output, so neither reruns inference.
            saved = con.read_parquet(str(output / "results.parquet"))
            saved.select(*[name for name in saved.columns if name not in {"response", "segments"}]).write_csv(
                str(output / "review.csv"), header=True
            )
            rows = [dict(zip(saved.columns, row)) for row in saved.fetchall()]
            # Gold labels are read only now; they never enter the inference pipeline.
            gold = {
                rid: list(INTENTS)[label]
                for rid, label in raw.project("sha256(path) AS record_id, intent_class").fetchall()
            }
    finally:
        vane.teardown_runner()

    # Failed rows (NULL predictions) stay in the denominator.
    if len(rows) != limit or {row["record_id"] for row in rows} != selected:
        raise ValueError("Evaluation requires exactly the original recording set")
    expected = [gold[row["record_id"]] for row in rows]
    print("Fixed MInDS-14 evaluation subset; test independence is limited (see module documentation).")
    for name, column in (("Keywords", "baseline_intent"), ("Jev", "intent")):
        print(name, metrics([row[column] for row in rows], expected))
    print(
        f"Rows: {len(rows)}; ASR/quality failures: {sum(row['error'] is not None for row in rows)}; outputs: {output}"
    )
    print("The 0.5 threshold is uncalibrated. Urgency, dissatisfaction and follow-up lack gold labels.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="New directory for final outputs")
    parser.add_argument("--limit", type=int, default=112, help="Recordings selected by path hash (1-502)")
    args = parser.parse_args()
    main(args.output_dir.resolve(), args.limit)
