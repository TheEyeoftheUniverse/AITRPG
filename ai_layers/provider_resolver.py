from typing import Any

from .usage_metrics import extract_provider_meta


def resolve_provider_by_id(context: Any, provider_id: str):
    """Resolve a configured provider ID across old and new AstrBot Context APIs."""
    get_provider = getattr(context, "get_provider", None)
    if callable(get_provider):
        provider = get_provider(provider_id)
        if provider:
            return provider

    get_provider_by_id = getattr(context, "get_provider_by_id", None)
    if callable(get_provider_by_id):
        provider = get_provider_by_id(provider_id)
        if provider:
            return provider

    get_all_providers = getattr(context, "get_all_providers", None)
    if callable(get_all_providers):
        for provider in get_all_providers() or []:
            if extract_provider_meta(provider).get("id") == provider_id:
                return provider

    return None
