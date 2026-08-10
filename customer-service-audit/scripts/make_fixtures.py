#!/usr/bin/env python3
"""Generate the four deterministic customer service call fixture recordings.

This script produces the packaged WAV assets consumed by ``fixture_loader``.
It uses edge-tts to synthesize natural Chinese customer service dialogues,
then decodes each clip to 16 kHz mono PCM WAV (the layout faster-whisper and
the stdlib ``wave`` probe both expect).

Run it once from the ``customer-service-audit`` directory; the resulting
``src/customer_service_audit/assets/*.wav`` files are committed so the demo is
reproducible without network access at runtime.

    python scripts/make_fixtures.py

Requires: ``edge-tts`` and ``faster-whisper`` (the latter provides PyAV for
MP3 decoding). Install them into the demo virtual environment.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
import struct
import wave

import edge_tts


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PROJECT_ROOT / "src" / "customer_service_audit" / "assets"
TARGET_SAMPLE_RATE = 16000
VOICE = "zh-CN-XiaoxiaoNeural"

# Each fixture is a scripted customer service dialogue. The call_id maps to the
# expected (problem_category, customer_sentiment) verified after each run.
FIXTURE_SCRIPTS = {
    "call_refund_angry": (
        "客服：您好，这里是客服中心，请问有什么可以帮您？"
        "客户：我上周买的东西到现在还没收到，你们到底发没发货？我已经等了整整十天了，太离谱了！"
        "客户：我不管，我现在就要退款，立刻给我退钱，否则我要投诉到消协去！"
        "客服：非常抱歉给您带来不好的体验，我马上为您核实订单并申请退款。"
        "客户：快点处理，我真的很生气，这服务太差了！"
    ),
    "call_billing_calm": (
        "客服：您好，这里是客服中心，请问有什么可以帮您？"
        "客户：你好，我想核对一下这个月的账单，有一笔费用我不太确定是什么。"
        "客服：好的，请您提供一下账户编号，我帮您查询明细。"
        "客户：账户编号是八八二零。这笔四十九元的费用是做什么的？"
        "客服：这是您开通的增值服务月费。如果您不需要，我可以帮您关闭。"
        "客户：好的，那就帮我关闭吧，谢谢。"
        "客服：已经为您关闭，下个月起不再扣费。还有其他可以帮您的吗？"
        "客户：没有了，谢谢你。"
    ),
    "call_tech_frustrated": (
        "客服：您好，这里是技术支持，请问有什么可以帮您？"
        "客户：你们的软件又闪退了，我已经重启了好几次，还是打不开，真的很烦。"
        "客服：抱歉给您带来困扰，请问您使用的是什么系统和版本？"
        "客户：Windows 十一，最新版本，昨天还好好的，今天就不行了。"
        "客服：建议您先清除缓存再重新登录，我这边也帮您查一下后台日志。"
        "客户：我试过了，没用，能不能给个靠谱点的解决办法？"
        "客服：我会安排工程师远程协助您排查，稍后与您联系。"
    ),
    "call_praise_happy": (
        "客服：您好，这里是客服中心，请问有什么可以帮您？"
        "客户：你好，我不是来投诉的，我是专门来表扬你们的客服小李的。"
        "客户：上次我遇到问题，他特别耐心，一直帮我处理到很晚，真的太感谢了。"
        "客服：非常感谢您的认可，我会把您的表扬转达给小李。"
        "客户：你们的服务真的很棒，以后我会继续支持你们的产品，加油！"
        "客服：谢谢您，祝您生活愉快！"
    ),
}


async def _synthesize_mp3(text: str) -> bytes:
    communicate = edge_tts.Communicate(text, VOICE)
    buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])
    return buffer.getvalue()


def _decode_to_pcm(mp3_bytes: bytes) -> bytes:
    """Decode MP3 to 16 kHz mono int16 PCM using faster-whisper's PyAV bridge."""

    from faster_whisper.audio import decode_audio

    samples = decode_audio(io.BytesIO(mp3_bytes), sampling_rate=TARGET_SAMPLE_RATE)
    clipped = samples.clip(-1.0, 1.0)
    return struct.pack(f"<{len(clipped)}h", *(int(value * 32767) for value in clipped))


def _write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(TARGET_SAMPLE_RATE)
        audio_file.writeframes(pcm)


async def _generate_all() -> int:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in FIXTURE_SCRIPTS.items():
        mp3_bytes = await _synthesize_mp3(text)
        pcm = _decode_to_pcm(mp3_bytes)
        target = ASSETS_DIR / f"{name}.wav"
        _write_wav(target, pcm)
        duration = len(pcm) / (TARGET_SAMPLE_RATE * 2)
        print(f"wrote {target.name} ({duration:.1f}s)")
    return len(FIXTURE_SCRIPTS)


def main() -> int:
    count = asyncio.run(_generate_all())
    print(f"generated {count} fixture recordings in {ASSETS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
