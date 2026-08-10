"""Create and load the deterministic four-call demo fixture."""

from __future__ import annotations

import argparse
from importlib.resources import files
from pathlib import Path
import sys
from typing import Sequence

from .config import DEFAULT_CONFIG_PATH, load_runtime_config
from .minio_store import MinioStore


FIXTURE_BUCKET = "customer-service-audit-fixtures"
AUDIO_CONTENT_TYPE = "audio/wav"

# The four synthetic calls cover four audit outcomes. The analysis values are
# verified after each run; transcripts stay engine-produced at runtime.
EXPECTED_ANALYSES = {
    "CALL-REFUND-ANGRY": ("refund_request", "very_negative"),
    "CALL-BILLING-CALM": ("billing_dispute", "neutral"),
    "CALL-TECH-FRUSTRATED": ("technical_support", "negative"),
    "CALL-PRAISE-HAPPY": ("praise", "very_positive"),
}

PACKAGED_AUDIO_ASSETS = {
    "CALL-REFUND-ANGRY": "call_refund_angry.wav",
    "CALL-BILLING-CALM": "call_billing_calm.wav",
    "CALL-TECH-FRUSTRATED": "call_tech_frustrated.wav",
    "CALL-PRAISE-HAPPY": "call_praise_happy.wav",
}


def packaged_audio_bytes(name: str) -> bytes:
    return (
        files(__package__)
        .joinpath("assets")
        .joinpath(name)
        .read_bytes()
    )


def load_fixture(config_path: Path = DEFAULT_CONFIG_PATH) -> int:
    """Refresh the MinIO recordings used by the audit pipeline at runtime."""

    config = load_runtime_config(config_path)
    store = MinioStore(config.minio)
    store.probe()
    store.ensure_bucket(config.minio.bucket)

    # The fixture owns the recordings and analysis prefixes end to end.
    store.remove_prefix(config.minio.bucket, config.minio.recordings_prefix)
    store.remove_prefix(config.minio.bucket, config.minio.analysis_prefix)

    for call_id, asset_name in PACKAGED_AUDIO_ASSETS.items():
        store.put_bytes(
            config.minio.bucket,
            f"{config.minio.recordings_prefix}{call_id}.wav",
            packaged_audio_bytes(asset_name),
            AUDIO_CONTENT_TYPE,
        )
    return len(PACKAGED_AUDIO_ASSETS)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load deterministic customer service call fixtures."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Static runtime YAML path.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        call_count = load_fixture(args.config)
    except Exception as exc:
        print(f"fixture load failed: {exc}", file=sys.stderr)
        return 1
    print(f"loaded {call_count} call recordings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
