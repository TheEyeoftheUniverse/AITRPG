from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Iterable

from astrbot.api import logger

from .provider_resolver import resolve_provider_by_id
from .usage_metrics import extract_provider_meta, extract_usage_metrics

# --- 全局并发限制 ---
# 限制同时进行的 AI 请求数，防止多玩家并发时触发 429 风暴。
# 延迟初始化以确保在 asyncio 事件循环启动后再创建 Semaphore。
_global_semaphore: asyncio.Semaphore | None = None
_GLOBAL_CONCURRENCY = 6


def _get_semaphore() -> asyncio.Semaphore:
    global _global_semaphore
    if _global_semaphore is None:
        _global_semaphore = asyncio.Semaphore(_GLOBAL_CONCURRENCY)
    return _global_semaphore


def normalize_provider_candidates(
    primary_provider_id: str | None,
    fallback_provider_ids: Iterable[str] | None = None,
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def _push(value: Any):
        provider_id = str(value or "").strip()
        if not provider_id or provider_id in seen:
            return
        ordered.append(provider_id)
        seen.add(provider_id)

    _push(primary_provider_id)
    for provider_id in fallback_provider_ids or []:
        _push(provider_id)
    return ordered


def _is_recoverable_error_text(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return True

    keywords = (
        "connection error",
        "connecterror",
        "timeout",
        "timed out",
        "rate limit",
        "429",
        "500",
        "502",
        "503",
        "504",
        "service unavailable",
        "temporarily unavailable",
        "provider unavailable",
        "disabled",
        "token invalidated",
        "unauthorized",
        "authenticationerror",
        "authentication error",
        "invalid api key",
        "api key",
        "forbidden",
        "quota",
        "overloaded",
        "all connection attempts failed",
    )
    return any(keyword in normalized for keyword in keywords)


def is_recoverable_provider_error(error: Any) -> bool:
    if error is None:
        return True

    if isinstance(error, BaseException):
        error_name = type(error).__name__.lower()
        if error_name in {
            "apiconnectionerror",
            "connecterror",
            "connectionerror",
            "timeout",
            "timeouterror",
            "apitimeouterror",
            "apiratelimiterror",
            "ratelimiterror",
            "authenticationerror",
        }:
            return True
        return _is_recoverable_error_text(f"{error_name}: {error}")

    return _is_recoverable_error_text(str(error))


def _build_attempt(
    *,
    provider_id: str,
    provider: Any = None,
    index: int,
    total: int,
    status: str,
    message: str,
    metrics: dict | None = None,
) -> dict:
    provider_meta = extract_provider_meta(provider)
    attempt = {
        "provider_id": provider_id,
        "configured_model": provider_meta.get("configured_model"),
        "actual_model": None,
        "model_display": None,
        "index": index,
        "total": total,
        "status": status,
        "message": str(message or "").strip(),
    }
    if metrics:
        attempt["actual_model"] = metrics.get("actual_model")
        attempt["model_display"] = metrics.get("model_display")
    if not attempt["model_display"]:
        configured_model = attempt.get("configured_model")
        if provider_id and configured_model:
            attempt["model_display"] = f"{provider_id} / {configured_model}"
        else:
            attempt["model_display"] = provider_id or configured_model
    return attempt


@dataclass
class ProviderCallOutcome:
    response: Any
    provider: Any
    metrics: dict


class ProviderFailoverError(RuntimeError):
    def __init__(self, message: str, attempts: list[dict], metrics: dict | None = None):
        super().__init__(message)
        self.attempts = attempts
        self.metrics = metrics or {}


async def text_chat_with_fallback(
    *,
    context: Any,
    primary_provider_id: str | None,
    fallback_provider_ids: Iterable[str] | None,
    prompt: str,
    contexts: list,
    trace_label: str,
    max_retries_per_provider: int = 2,
    base_backoff: float = 1.0,
) -> ProviderCallOutcome:
    """向 provider 发送请求，支持全局并发限制、per-provider 指数退避重试和 fallback 切换。

    Args:
        max_retries_per_provider: 每个 provider 在切换到下一个之前的最大重试次数（针对可恢复错误）。
        base_backoff: 指数退避基准秒数，第 n 次重试等待 base_backoff * 2^(n-1) 秒。
    """
    candidate_ids = normalize_provider_candidates(primary_provider_id, fallback_provider_ids)
    attempts: list[dict] = []

    if not candidate_ids:
        raise ProviderFailoverError(
            f"{trace_label}: no provider candidates configured",
            attempts=[],
            metrics={
                "attempts": [],
                "attempt_count": 0,
                "candidate_count": 0,
                "fallback_used": False,
            },
        )

    total_candidates = len(candidate_ids)
    last_error_message = ""

    # 全局并发限制：排队等待 semaphore，防止并发 AI 请求过多触发 429
    async with _get_semaphore():
        for index, provider_id in enumerate(candidate_ids, start=1):
            provider = resolve_provider_by_id(context, provider_id)
            if provider is None:
                attempt = _build_attempt(
                    provider_id=provider_id,
                    index=index,
                    total=total_candidates,
                    status="missing",
                    message="provider not found",
                )
                attempts.append(attempt)
                last_error_message = f"Provider {provider_id} not found"
                logger.warning("[%s] Provider %s not found, trying next candidate.", trace_label, provider_id)
                continue

            if index > 1:
                logger.warning(
                    "[%s] Switched to fallback provider %s (%s/%s).",
                    trace_label,
                    provider_id,
                    index,
                    total_candidates,
                )

            # per-provider 指数退避重试
            for retry in range(max_retries_per_provider + 1):
                if retry > 0:
                    wait = base_backoff * (2 ** (retry - 1))
                    logger.warning(
                        "[%s] Provider %s retry %s/%s after %.1fs backoff.",
                        trace_label,
                        provider_id,
                        retry,
                        max_retries_per_provider,
                        wait,
                    )
                    await asyncio.sleep(wait)

                try:
                    response = await provider.text_chat(prompt=prompt, contexts=contexts)
                    response_text = (
                        response.completion_text if hasattr(response, "completion_text") else str(response)
                    )
                    metrics = extract_usage_metrics(
                        response,
                        prompt,
                        response_text,
                        provider=provider,
                    )
                    attempt = _build_attempt(
                        provider_id=provider_id,
                        provider=provider,
                        index=index,
                        total=total_candidates,
                        status="success",
                        message="ok",
                        metrics=metrics,
                    )

                    if getattr(response, "role", None) == "err":
                        attempt["status"] = "error"
                        attempt["message"] = response_text or "provider returned err response"
                        attempts.append(attempt)
                        last_error_message = attempt["message"]
                        if is_recoverable_provider_error(response_text):
                            if retry < max_retries_per_provider:
                                # 还有重试次数，继续退避重试同一 provider
                                continue
                            # 重试耗尽，切换下一个 provider
                            if index < total_candidates:
                                logger.warning(
                                    "[%s] Provider %s err response after %s retries, trying next fallback.",
                                    trace_label,
                                    provider_id,
                                    retry,
                                )
                                break
                        metrics.update(
                            {
                                "attempts": attempts,
                                "attempt_count": len(attempts),
                                "candidate_count": total_candidates,
                                "fallback_used": len(attempts) > 1,
                                "selected_attempt_index": None,
                            }
                        )
                        raise ProviderFailoverError(
                            response_text or f"{trace_label}: provider returned err response",
                            attempts=attempts,
                            metrics=metrics,
                        )

                    attempts.append(attempt)
                    metrics.update(
                        {
                            "attempts": attempts,
                            "attempt_count": len(attempts),
                            "candidate_count": total_candidates,
                            "fallback_used": len(attempts) > 1,
                            "selected_attempt_index": len(attempts),
                        }
                    )
                    return ProviderCallOutcome(response=response, provider=provider, metrics=metrics)

                except ProviderFailoverError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    message = f"{type(exc).__name__}: {exc}"
                    attempt = _build_attempt(
                        provider_id=provider_id,
                        provider=provider,
                        index=index,
                        total=total_candidates,
                        status="exception",
                        message=message,
                    )
                    attempts.append(attempt)
                    last_error_message = message

                    if is_recoverable_provider_error(exc):
                        if retry < max_retries_per_provider:
                            # 还有重试次数，继续退避重试同一 provider
                            continue
                        # 重试耗尽，尝试切换下一个 provider
                        if index < total_candidates:
                            logger.warning(
                                "[%s] Provider %s request error after %s retries, trying next fallback: %s",
                                trace_label,
                                provider_id,
                                retry,
                                message,
                            )
                            break

                    raise ProviderFailoverError(
                        f"{trace_label}: {message}",
                        attempts=attempts,
                        metrics={
                            "provider_id": provider_id,
                            "configured_model": attempt.get("configured_model"),
                            "actual_model": None,
                            "model_source": None,
                            "model_display": attempt.get("model_display"),
                            "attempts": attempts,
                            "attempt_count": len(attempts),
                            "candidate_count": total_candidates,
                            "fallback_used": len(attempts) > 1,
                            "selected_attempt_index": None,
                        },
                    ) from exc

    raise ProviderFailoverError(
        last_error_message or f"{trace_label}: all providers unavailable",
        attempts=attempts,
        metrics={
            "attempts": attempts,
            "attempt_count": len(attempts),
            "candidate_count": total_candidates,
            "fallback_used": len(attempts) > 1,
            "selected_attempt_index": None,
        },
    )
