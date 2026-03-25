import math
import re
from typing import Any, Dict


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_int(value: Any):
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_value(source: Any, key: str):
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def extract_provider_meta(provider: Any) -> Dict[str, Any]:
    provider_config = getattr(provider, "provider_config", {}) or {}
    meta_callable = getattr(provider, "meta", None)
    meta = meta_callable() if callable(meta_callable) else None
    configured_model = None
    get_model = getattr(provider, "get_model", None)
    if callable(get_model):
        configured_model = get_model()
    configured_model = _safe_str(configured_model) or _safe_str(provider_config.get("model"))
    return {
        "id": _safe_str(provider_config.get("id")) or _safe_str(getattr(meta, "id", None)),
        "configured_model": configured_model,
        "base_url": _safe_str(provider_config.get("api_base")),
    }


def _extract_response_model(response: Any) -> str | None:
    candidates = []

    raw_completion = getattr(response, "raw_completion", None)
    if raw_completion is not None:
        candidates.append(raw_completion)

    nested_attrs = ("response", "raw_response", "raw", "data", "result")
    for attr in nested_attrs:
        nested = getattr(response, attr, None)
        if nested is not None:
            candidates.append(nested)

    if isinstance(response, dict):
        raw_completion = response.get("raw_completion")
        if raw_completion is not None:
            candidates.append(raw_completion)
        for attr in nested_attrs:
            nested = response.get(attr)
            if nested is not None:
                candidates.append(nested)

    model_keys = ("model", "model_name", "model_id", "model_version", "modelVersion")
    for candidate in candidates:
        for key in model_keys:
            value = _get_value(candidate, key)
            safe_value = _safe_str(value)
            if safe_value:
                return safe_value

    return None


def _build_model_display(provider_id: str | None, actual_model: str | None, configured_model: str | None) -> str | None:
    display_model = actual_model or configured_model
    if provider_id and display_model:
        return f"{provider_id} / {display_model}"
    return provider_id or display_model


def _extract_usage_candidate(response: Any):
    candidates = []

    direct_attrs = ("usage", "token_usage", "raw_usage")
    nested_attrs = ("response", "raw_response", "raw", "data", "result")

    for attr in direct_attrs:
        value = getattr(response, attr, None)
        if value is not None:
            candidates.append(value)

    for attr in nested_attrs:
        nested = getattr(response, attr, None)
        if nested is None:
            continue
        if isinstance(nested, dict):
            usage = nested.get("usage")
            if usage is not None:
                candidates.append(usage)
        else:
            usage = getattr(nested, "usage", None)
            if usage is not None:
                candidates.append(usage)

    if isinstance(response, dict):
        for attr in direct_attrs:
            value = response.get(attr)
            if value is not None:
                candidates.append(value)

    for candidate in candidates:
        prompt_tokens = _safe_int(
            _get_value(candidate, "prompt_tokens") or _get_value(candidate, "input_tokens")
        )
        completion_tokens = _safe_int(
            _get_value(candidate, "completion_tokens") or _get_value(candidate, "output_tokens")
        )
        total_tokens = _safe_int(_get_value(candidate, "total_tokens"))

        if prompt_tokens is None and completion_tokens is None and total_tokens is None:
            continue

        if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
            total_tokens = int(prompt_tokens or 0) + int(completion_tokens or 0)

        return {
            "prompt_tokens": prompt_tokens or 0,
            "completion_tokens": completion_tokens or 0,
            "total_tokens": total_tokens or 0,
            "token_source": "actual",
        }

    return None


def estimate_text_tokens(text: str) -> int:
    text = str(text or "").strip()
    if not text:
        return 0

    cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
    ascii_word_count = len(re.findall(r"[A-Za-z0-9_]+", text))
    punctuation_count = len(re.findall(r"[^\w\s]", text, flags=re.UNICODE))
    whitespace_count = len(re.findall(r"\s", text))

    estimate = cjk_count + ascii_word_count + math.ceil(max(0, punctuation_count - whitespace_count) * 0.5)
    return max(1, estimate)


def extract_usage_metrics(
    response: Any,
    prompt_text: str = "",
    completion_text: str = "",
    provider: Any = None,
) -> Dict[str, Any]:
    usage = _extract_usage_candidate(response)
    provider_meta = extract_provider_meta(provider)
    response_model = _extract_response_model(response)
    actual_model = response_model or provider_meta.get("configured_model")
    model_source = "response" if response_model else "provider"
    if usage:
        usage["prompt_chars"] = len(str(prompt_text or ""))
        usage["completion_chars"] = len(str(completion_text or ""))
        metrics = usage
    else:
        prompt_tokens = estimate_text_tokens(prompt_text)
        completion_tokens = estimate_text_tokens(completion_text)
        metrics = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "token_source": "estimated",
            "prompt_chars": len(str(prompt_text or "")),
            "completion_chars": len(str(completion_text or "")),
        }

    metrics.update({
        "provider_id": provider_meta.get("id"),
        "configured_model": provider_meta.get("configured_model"),
        "actual_model": actual_model,
        "model_source": model_source if actual_model else None,
        "model_display": _build_model_display(
            provider_meta.get("id"),
            actual_model,
            provider_meta.get("configured_model"),
        ),
    })
    return metrics


def merge_usage_metrics(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    base = dict(base or {})
    incoming = dict(incoming or {})

    if not base:
        merged = incoming
    else:
        merged = {
            "prompt_tokens": int(base.get("prompt_tokens", 0) or 0) + int(incoming.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(base.get("completion_tokens", 0) or 0) + int(incoming.get("completion_tokens", 0) or 0),
            "total_tokens": int(base.get("total_tokens", 0) or 0) + int(incoming.get("total_tokens", 0) or 0),
            "prompt_chars": int(base.get("prompt_chars", 0) or 0) + int(incoming.get("prompt_chars", 0) or 0),
            "completion_chars": int(base.get("completion_chars", 0) or 0) + int(incoming.get("completion_chars", 0) or 0),
            "token_source": "actual"
            if base.get("token_source") == "actual" and incoming.get("token_source") == "actual"
            else "mixed",
            "provider_id": base.get("provider_id") or incoming.get("provider_id"),
            "configured_model": base.get("configured_model") or incoming.get("configured_model"),
            "actual_model": base.get("actual_model") or incoming.get("actual_model"),
            "model_source": base.get("model_source") or incoming.get("model_source"),
            "model_display": base.get("model_display") or incoming.get("model_display"),
        }

    merged["call_count"] = int(base.get("call_count", 0) or 0) + int(incoming.get("call_count", 1) or 1)
    return merged
