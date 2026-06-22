"""Local Qwen OpenAI-compatible adapter for claims POC smoke tests."""

from __future__ import annotations

import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


MODEL = None
PROCESSOR = None
ARGS = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model-path",
        default=str(Path.home() / "models" / "Qwen2.5-VL-3B-Instruct"),
    )
    parser.add_argument("--served-model-name", default="Qwen2.5-VL-3B-Instruct")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--max-new-tokens", type=int, default=768)
    return parser.parse_args()


def load_model() -> None:
    global MODEL, PROCESSOR
    if MODEL is not None and PROCESSOR is not None:
        return

    import torch
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

    model_path = str(Path(ARGS.model_path).expanduser())
    PROCESSOR = AutoProcessor.from_pretrained(model_path, local_files_only=True)
    MODEL = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        model_path,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        local_files_only=True,
    )
    MODEL.eval()


def normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, list):
            parts: list[dict[str, Any]] = []
            for part in content:
                part_type = part.get("type")
                if part_type == "image_url":
                    image_url = part.get("image_url", {})
                    url = (
                        image_url.get("url")
                        if isinstance(image_url, dict)
                        else image_url
                    )
                    parts.append({"type": "image", "image": url})
                elif part_type == "text":
                    parts.append({"type": "text", "text": str(part.get("text", ""))})
                else:
                    parts.append(part)
            normalized.append({"role": message.get("role", "user"), "content": parts})
        else:
            normalized.append(
                {
                    "role": message.get("role", "user"),
                    "content": [{"type": "text", "text": str(content)}],
                }
            )
    return normalized


def generate_chat_completion(payload: dict[str, Any]) -> str:
    load_model()
    if MODEL is None or PROCESSOR is None:
        raise RuntimeError("Qwen model failed to load")

    import torch
    from qwen_vl_utils import process_vision_info

    messages = normalize_messages(payload.get("messages", []))
    text = PROCESSOR.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = PROCESSOR(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
    ).to(MODEL.device)

    temperature = float(payload.get("temperature") or 0.0)
    max_new_tokens = int(payload.get("max_tokens") or ARGS.max_new_tokens)
    with torch.inference_mode():
        generated_ids = MODEL.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=temperature > 0.0,
            temperature=temperature if temperature > 0.0 else None,
        )
    trimmed = [
        output_ids[len(input_ids) :]
        for input_ids, output_ids in zip(inputs.input_ids, generated_ids, strict=True)
    ]
    raw_text = PROCESSOR.batch_decode(
        trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )[0]
    return normalize_damage_json(raw_text)


def json_object_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            raise
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model response JSON must be an object")
    return value


def coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "yes", "visible", "present", "clear", "1"}:
        return True
    if text in {"false", "no", "not visible", "absent", "unclear", "0"}:
        return False
    return default


def coerce_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [part.strip() for part in re.split(r"[,;/]", text) if part.strip()]


def coerce_confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    text = str(value or "").strip().lower()
    if text in {"very high", "high"}:
        return 0.85
    if text in {"medium", "moderate"}:
        return 0.6
    if text in {"low", "weak"}:
        return 0.35
    try:
        number = float(text)
    except ValueError:
        return 0.5
    return max(0.0, min(1.0, number))


def first_value(obj: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in obj:
            return obj[key]
    return default


def normalize_damage_json(raw_text: str) -> str:
    obj = json_object_from_text(raw_text)
    vehicle_visible = coerce_bool(
        first_value(obj, "vehicle_visible", "vehicle", "vehicle_present", "car_visible")
    )
    damage_visible = coerce_bool(
        first_value(obj, "damage_visible", "damage", "damage_present")
    )
    target_vehicle_clear = coerce_bool(
        first_value(
            obj,
            "target_vehicle_clear",
            "target_clear",
            "vehicle_clear",
            default=vehicle_visible,
        ),
        default=vehicle_visible,
    )
    damaged_parts = coerce_list(
        first_value(obj, "damaged_parts", "damage_parts", "parts", "part")
    )
    damage_types = coerce_list(
        first_value(obj, "damage_types", "damage_type", "types", "type")
    )
    if damage_visible and not damage_types:
        damage_types = ["unknown"]
    if not damage_visible and not damage_types:
        damage_types = ["none_visible"]
    normalized = {
        "vehicle_visible": vehicle_visible,
        "target_vehicle_clear": target_vehicle_clear,
        "damage_visible": damage_visible,
        "damaged_parts": damaged_parts,
        "damage_types": damage_types,
        "severity_hint": str(
            first_value(obj, "severity_hint", "severity", default="unknown")
            or "unknown"
        ).lower(),
        "evidence_description": str(
            first_value(
                obj,
                "evidence_description",
                "damage_description",
                "description",
                default="",
            )
            or ""
        ),
        "uncertainty_reasons": coerce_list(
            first_value(obj, "uncertainty_reasons", "uncertainty", "limitations")
        ),
        "confidence": coerce_confidence(first_value(obj, "confidence", default=0.5)),
    }
    return json.dumps(normalized, ensure_ascii=False)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/v1/models":
            self.send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": ARGS.served_model_name,
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": "local-transformers",
                        }
                    ],
                },
            )
            return
        self.send_json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/v1/chat/completions":
            self.send_json(404, {"error": {"message": "not found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            content = generate_chat_completion(payload)
            self.send_json(
                200,
                {
                    "id": f"chatcmpl-local-{int(time.time() * 1000)}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": ARGS.served_model_name,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
        except Exception as exc:
            self.send_json(
                500,
                {"error": {"type": type(exc).__name__, "message": str(exc)}},
            )


def main() -> None:
    global ARGS
    ARGS = parse_args()
    load_model()
    server = ThreadingHTTPServer((ARGS.host, ARGS.port), Handler)
    print(
        f"Serving {ARGS.served_model_name} at "
        f"http://{ARGS.host}:{ARGS.port}/v1"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
