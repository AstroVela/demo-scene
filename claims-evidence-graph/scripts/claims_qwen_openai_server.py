#!/usr/bin/env python3
"""Compatibility wrapper for the local Qwen OpenAI-compatible adapter."""

from __future__ import annotations

from claims_evidence_graph_pipeline import qwen_openai_server as _server

parse_args = _server.parse_args
normalize_messages = _server.normalize_messages
json_object_from_text = _server.json_object_from_text
coerce_bool = _server.coerce_bool
coerce_list = _server.coerce_list
coerce_confidence = _server.coerce_confidence
first_value = _server.first_value
normalize_damage_json = _server.normalize_damage_json
Handler = _server.Handler

MODEL = _server.MODEL
PROCESSOR = _server.PROCESSOR
ARGS = _server.ARGS


def load_model() -> None:
    global MODEL, PROCESSOR
    _server.ARGS = ARGS
    _server.MODEL = MODEL
    _server.PROCESSOR = PROCESSOR
    _server.load_model()
    MODEL = _server.MODEL
    PROCESSOR = _server.PROCESSOR


def generate_chat_completion(payload: dict) -> str:
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


def main() -> None:
    _server.main()


if __name__ == "__main__":
    main()
