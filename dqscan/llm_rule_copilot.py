"""LLM-backed Rule Copilot: natural language -> rules.yaml."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable

import requests
import yaml

from .planner import SUPPORTED_RULE_TYPES

# NOTE: Set your Hugging Face API key here if you want to hardcode it in the codebase.
HF_API_KEY = ""
# Default HF model; update if you want a different one.
HF_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
# Optional: If you created a dedicated HF Inference Endpoint, set the full URL here.
# Example: "https://YOUR-ENDPOINT.hf.space"
HF_ENDPOINT = ""
# Optional: Local Transformers model id/path for inference (falls back to HF_MODEL).
TRANSFORMERS_MODEL = ""
# Optional: Use bitsandbytes 4-bit quantization when loading local model.
TRANSFORMERS_LOAD_IN_4BIT = True
# Optional: Max new tokens for local generation.
TRANSFORMERS_MAX_NEW_TOKENS = 2048
# Optional: Context length for tokenizer truncation.
TRANSFORMERS_CTX = 2048

_LOCAL_MODEL_CACHE: dict[str, Any] = {}


@dataclass(frozen=True)
class RuleSchemaSpec:
    supported_types: tuple[str, ...]
    severities: tuple[str, ...]
    selector_keys: tuple[str, ...]


def default_schema_spec() -> RuleSchemaSpec:
    return RuleSchemaSpec(
        supported_types=tuple(sorted(SUPPORTED_RULE_TYPES)),
        severities=("critical", "high", "medium", "low"),
        selector_keys=("dtype", "include", "exclude", "name_regex"),
    )


def build_system_prompt(spec: RuleSchemaSpec) -> str:
    supported = ", ".join(spec.supported_types)
    severities = ", ".join(spec.severities)
    selector_keys = ", ".join(spec.selector_keys)
    return (
        "You generate data quality rules for a CSV scanner.\n"
        "Output ONLY a JSON object. No explanations.\n"
        "Schema (top-level): {\n"
        '  "checks": [\n'
        "    {\n"
        '      "name": string,\n'
        '      "rules": [ Rule, ... ]\n'
        "    }\n"
        "  ]\n"
        "}\n"
        "Rule schema:\n"
        "{\n"
        '  "id": string,\n'
        '  "type": one of [' + supported + "],\n"
        '  "severity": one of [' + severities + "] (optional),\n"
        '  "column": string OR "selector": { ' + selector_keys + " },\n"
        "  ... rule-type specific fields ...\n"
        "}\n"
        "Rule-type specific fields:\n"
        "- completeness: column, min\n"
        "- missing_rate: column, max\n"
        "- type: column, expected (numeric|datetime|text|categorical), min_parse_success OR min\n"
        "- range: column, min_value, max_value, min_compliance OR min\n"
        "- uniqueness: column, min\n"
        "- domain: column, allowed_values (list), min_compliance OR min\n"
        "- pattern: column, regex, min_compliance OR min\n"
        "- outlier: column, method (iqr|mad), max_rate OR max, optional iqr_k/mad_threshold/sample_n\n"
        "- predicate: columns (2) + operator OR expression, min_compliance OR min\n"
        "Selector schema (optional instead of column):\n"
        "{\n"
        '  "dtype": string or list of strings,\n'
        '  "include": list of column names (optional),\n'
        '  "exclude": list of column names (optional),\n'
        '  "name_regex": string (optional)\n'
        "}\n"
        "Never use unsupported rule types. Output MUST be valid JSON.\n"
        "Return a JSON object only. Do not wrap in markdown or code fences. "
        "Do not include trailing comments."
    )


def build_few_shot_examples() -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": "REMINDER: Output JSON only. No prose, no markdown.",
        },
        {
            "role": "user",
            "content": "Create rules: age must be between 0 and 120, min compliance 0.99.",
        },
        {
            "role": "assistant",
            "content": json.dumps(
                {
                    "checks": [
                        {
                            "name": "range-checks",
                            "rules": [
                                {
                                    "id": "R_range_age",
                                    "type": "range",
                                    "column": "age",
                                    "min_value": 0,
                                    "max_value": 120,
                                    "min_compliance": 0.99,
                                    "severity": "high",
                                }
                            ],
                        }
                    ]
                }
            ),
        },
        {
            "role": "user",
            "content": "User id must be unique with min 0.999.",
        },
        {
            "role": "assistant",
            "content": json.dumps(
                {
                    "checks": [
                        {
                            "name": "uniqueness",
                            "rules": [
                                {
                                    "id": "R_unique_user_id",
                                    "type": "uniqueness",
                                    "column": "user_id",
                                    "min": 0.999,
                                    "severity": "critical",
                                }
                            ],
                        }
                    ]
                }
            ),
        },
        {
            "role": "user",
            "content": "All numeric columns except class should have missing rate <= 0.01.",
        },
        {
            "role": "assistant",
            "content": json.dumps(
                {
                    "checks": [
                        {
                            "name": "selector-missing",
                            "rules": [
                                {
                                    "id": "R_missing_numeric_all",
                                    "type": "missing_rate",
                                    "selector": {"dtype": ["numeric"], "exclude": ["class"]},
                                    "max": 0.01,
                                    "severity": "medium",
                                }
                            ],
                        }
                    ]
                }
            ),
        },
    ]


def _format_llama31_chat(messages: list[dict[str, str]]) -> str:
    # Llama 3/3.1 chat format: special tokens with role headers and \n\n before content.
    parts = ["<|begin_of_text|>"]
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        parts.append(f"<|start_header_id|>{role}<|end_header_id|>\n\n{content}<|eot_id|>")
    parts.append("<|start_header_id|>assistant<|end_header_id|>\n\n")
    return "".join(parts)


def _clean_llama31_output(text: str) -> str:
    # Strip any special tokens or trailing content after end-of-turn.
    cleaned = text.strip()
    if "<|eot_id|>" in cleaned:
        cleaned = cleaned.split("<|eot_id|>", 1)[0]
    return cleaned.strip()


def _extract_json_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    in_str = False
    escape = False
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == "\"":
                in_str = False
            continue
        if ch == "\"":
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _call_huggingface(
    messages: list[dict[str, str]],
    api_key: str,
    model: str,
) -> str:
    url = HF_ENDPOINT or f"https://api-inference.huggingface.co/models/{model}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    prompt = _format_llama31_chat(messages)

    payload: dict[str, Any] = {
        "inputs": prompt,
        "parameters": {
            "temperature": 0,
            "return_full_text": False,
            "max_new_tokens": 1200,
        },
        "options": {"wait_for_model": True},
    }
    response = requests.post(url, headers=headers, json=payload, timeout=120)
    if response.status_code == 410:
        raise ValueError(
            "Hugging Face Inference API returned 410 (Gone). "
            "This usually means serverless inference is disabled for the model. "
            "Use a dedicated Inference Endpoint and set HF_ENDPOINT, or switch to a model "
            "that supports the serverless Inference API."
        )
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and "error" in data:
        raise ValueError(f"Hugging Face API error: {data['error']}")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and "generated_text" in first:
            return _clean_llama31_output(str(first["generated_text"]))
    raise ValueError("Unexpected Hugging Face API response format.")


def _transformers_config() -> dict[str, Any]:
    model = TRANSFORMERS_MODEL or HF_MODEL
    return {
        "model": model,
        "load_in_4bit": bool(TRANSFORMERS_LOAD_IN_4BIT),
        "max_new_tokens": int(TRANSFORMERS_MAX_NEW_TOKENS),
        "ctx": int(TRANSFORMERS_CTX),
    }


def _load_transformers_model(model_id: str, load_in_4bit: bool, hf_token: str | None) -> tuple[Any, Any]:
    if model_id in _LOCAL_MODEL_CACHE:
        return _LOCAL_MODEL_CACHE[model_id]
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except ImportError as exc:
        raise ValueError(
            "Missing transformers/bitsandbytes dependencies. Install with:\n"
            "pip install transformers bitsandbytes accelerate sentencepiece"
        ) from exc

    if not torch.cuda.is_available():
        raise ValueError(
            "CUDA is not available. GPU inference is required for 4-bit loading. "
            "Install a CUDA-enabled PyTorch build and verify torch.cuda.is_available() is True."
        )

    quant_config = None
    load_kwargs: dict[str, Any] = {"device_map": "cuda", "token": hf_token}
    if load_in_4bit:
        quant_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
        )
        load_kwargs["quantization_config"] = quant_config
    else:
        load_kwargs["torch_dtype"] = torch.float16

    tokenizer = AutoTokenizer.from_pretrained(model_id, token=hf_token)
    model = AutoModelForCausalLM.from_pretrained(model_id, **load_kwargs)
    _LOCAL_MODEL_CACHE[model_id] = (tokenizer, model)
    return tokenizer, model


def _call_transformers_local(
    messages: list[dict[str, str]],
    api_key: str,
    _model: str,
) -> str:
    cfg = _transformers_config()
    model_id = cfg["model"]
    prompt = None
    tokenizer, model = _load_transformers_model(model_id, cfg["load_in_4bit"], api_key or None)
    if hasattr(tokenizer, "apply_chat_template"):
        prompt = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
    if not prompt:
        prompt = _format_llama31_chat(messages)
    inputs = tokenizer(
        prompt,
        return_tensors="pt",
        truncation=True,
        max_length=cfg["ctx"],
        add_special_tokens=False,
    )
    inputs = {k: v.to(model.device) for k, v in inputs.items()}
    output_ids = model.generate(
        **inputs,
        max_new_tokens=cfg["max_new_tokens"],
        min_new_tokens=1,
        do_sample=False,
        temperature=0.0,
        eos_token_id=tokenizer.eos_token_id,
        pad_token_id=tokenizer.eos_token_id,
    )
    input_len = int(inputs["input_ids"].shape[1])
    gen_ids = output_ids[0][input_len:]
    if gen_ids.numel() == 0:
        raise ValueError("Local transformers model returned empty output.")
    decoded = tokenizer.decode(gen_ids, skip_special_tokens=False)
    decoded = _clean_llama31_output(decoded)
    _write_debug_output(decoded, prompt)
    if not decoded:
        raise ValueError("Local transformers model returned empty output.")
    return decoded


def _write_debug_output(text: str, prompt: str | None = None) -> None:
    path = os.getenv("TRANSFORMERS_DEBUG_OUTPUT_PATH", TRANSFORMERS_DEBUG_OUTPUT_PATH)
    if not path:
        return
    try:
        from pathlib import Path

        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = text
        if prompt:
            payload = f"PROMPT:\n{prompt}\n\nOUTPUT:\n{text}"
        out_path.write_text(payload, encoding="utf-8")
    except OSError:
        pass

def validate_rules_json(obj: Any, spec: RuleSchemaSpec) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(obj, dict):
        return False, ["Top-level JSON must be an object."]

    checks = obj.get("checks")
    if not isinstance(checks, list) or not checks:
        errors.append("Top-level 'checks' must be a non-empty list.")
        return False, errors

    for ci, check in enumerate(checks):
        if not isinstance(check, dict):
            errors.append(f"checks[{ci}] must be an object.")
            continue
        if "name" not in check or not isinstance(check.get("name"), str):
            errors.append(f"checks[{ci}].name must be a string.")
        rules = check.get("rules")
        if not isinstance(rules, list) or not rules:
            errors.append(f"checks[{ci}].rules must be a non-empty list.")
            continue
        for ri, rule in enumerate(rules):
            if not isinstance(rule, dict):
                errors.append(f"checks[{ci}].rules[{ri}] must be an object.")
                continue
            rule_id = rule.get("id")
            if not isinstance(rule_id, str) or not rule_id:
                errors.append(f"checks[{ci}].rules[{ri}].id must be a non-empty string.")
            rtype = rule.get("type")
            if not isinstance(rtype, str):
                errors.append(f"checks[{ci}].rules[{ri}].type must be a string.")
                continue
            if rtype not in spec.supported_types:
                errors.append(f"checks[{ci}].rules[{ri}].type '{rtype}' is not supported.")

            has_column = "column" in rule
            has_selector = "selector" in rule
            if not has_column and not has_selector:
                errors.append(f"checks[{ci}].rules[{ri}] must have 'column' or 'selector'.")
            if has_column and not isinstance(rule.get("column"), str):
                errors.append(f"checks[{ci}].rules[{ri}].column must be a string.")
            if has_selector:
                selector = rule.get("selector")
                if not isinstance(selector, dict):
                    errors.append(f"checks[{ci}].rules[{ri}].selector must be an object.")
                else:
                    for key in selector.keys():
                        if key not in spec.selector_keys:
                            errors.append(
                                f"checks[{ci}].rules[{ri}].selector has unsupported key '{key}'."
                            )

            _validate_rule_specific_fields(rule, rtype, ci, ri, errors)

    return len(errors) == 0, errors


def _validate_rule_specific_fields(
    rule: dict[str, Any],
    rtype: str,
    ci: int,
    ri: int,
    errors: list[str],
) -> None:
    def err(msg: str) -> None:
        errors.append(f"checks[{ci}].rules[{ri}]: {msg}")

    if rtype == "completeness":
        if "min" not in rule:
            err("completeness requires 'min'.")
    elif rtype == "missing_rate":
        if "max" not in rule:
            err("missing_rate requires 'max'.")
    elif rtype == "type":
        if "expected" not in rule:
            err("type requires 'expected'.")
        if "min_parse_success" not in rule and "min" not in rule:
            err("type requires 'min_parse_success' or 'min'.")
    elif rtype == "range":
        if "min_value" not in rule or "max_value" not in rule:
            err("range requires 'min_value' and 'max_value'.")
        if "min_compliance" not in rule and "min" not in rule:
            err("range requires 'min_compliance' or 'min'.")
    elif rtype == "uniqueness":
        if "min" not in rule:
            err("uniqueness requires 'min'.")
    elif rtype == "domain":
        if "allowed_values" not in rule:
            err("domain requires 'allowed_values'.")
        if "min_compliance" not in rule and "min" not in rule:
            err("domain requires 'min_compliance' or 'min'.")
    elif rtype == "pattern":
        if "regex" not in rule:
            err("pattern requires 'regex'.")
        if "min_compliance" not in rule and "min" not in rule:
            err("pattern requires 'min_compliance' or 'min'.")
    elif rtype == "outlier":
        if "method" not in rule:
            err("outlier requires 'method'.")
        if "max_rate" not in rule and "max" not in rule:
            err("outlier requires 'max_rate' or 'max'.")
    elif rtype == "predicate":
        has_columns = "columns" in rule
        has_expression = "expression" in rule
        if not has_columns and not has_expression:
            err("predicate requires 'columns' or 'expression'.")
        if "min_compliance" not in rule and "min" not in rule:
            err("predicate requires 'min_compliance' or 'min'.")


def json_to_rules_yaml(obj: dict[str, Any]) -> str:
    return yaml.safe_dump(obj, sort_keys=False, allow_unicode=False)


def generate_rules_json(
    nl_text: str,
    spec: RuleSchemaSpec | None = None,
    model: str | None = None,
    api_key: str | None = None,
    retries: int = 2,
    call_fn: Callable[[list[dict[str, str]], str, str], str] | None = None,
) -> dict[str, Any]:
    spec = spec or default_schema_spec()
    api_key = api_key or HF_API_KEY
    model = model or HF_MODEL
    if call_fn is None:
        call_fn = _call_transformers_local

    system_prompt = build_system_prompt(spec)
    few_shots = build_few_shot_examples()

    last_errors: list[str] = []
    last_raw = ""

    for attempt in range(retries + 1):
        error_report = ""
        if last_errors:
            error_report = (
                "The previous JSON failed validation. Fix it and output ONLY JSON. "
                "Errors:\n- " + "\n- ".join(last_errors) + "\n"
                "Return the corrected JSON object with the same schema."
            )
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(few_shots)
        messages.append(
            {
                "role": "user",
                "content": nl_text if not error_report else f"{nl_text}\n\n{error_report}\n\nPrevious JSON:\n{last_raw}",
            }
        )

        raw = call_fn(messages, api_key, model)
        last_raw = raw
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            extracted = _extract_json_object(raw or "")
            if extracted:
                try:
                    obj = json.loads(extracted)
                except json.JSONDecodeError as exc2:
                    last_errors = [f"JSON parse error: {exc2}"]
                    continue
                ok, errors = validate_rules_json(obj, spec)
                if ok:
                    return obj
                last_errors = errors
                continue
            last_errors = [f"JSON parse error: {exc}"]
            continue

        ok, errors = validate_rules_json(obj, spec)
        if ok:
            return obj
        last_errors = errors

    raise ValueError("Failed to generate valid rules JSON. Errors:\n- " + "\n- ".join(last_errors))
# Optional: Write raw model output to a debug file for inspection.
TRANSFORMERS_DEBUG_OUTPUT_PATH = "reports/raw_llm_output.txt"
