"""
Central credential helper.

Prefer Azure Managed Identity in the Function App. Fall back to API keys
only when AUTH_MODE=key is explicitly set (useful for local dev / tests
running outside Azure).

azure-identity is imported lazily so unit tests and local key-only runs
do not require the package to be installed.
"""

import threading
import time
from collections.abc import Callable
from functools import lru_cache

from .config import optional_env

AOAI_SCOPE = "https://cognitiveservices.azure.us/.default"
STORAGE_SCOPE = "https://storage.azure.com/.default"
SEARCH_SCOPE = "https://search.azure.us/.default"
DI_SCOPE = "https://cognitiveservices.azure.us/.default"


def use_managed_identity() -> bool:
    """
    True unless the operator has explicitly forced key-based auth.
    Default is MI because that is what production should run with.
    """
    mode = optional_env("AUTH_MODE", "mi").lower()
    return mode != "key"


@lru_cache(maxsize=1)
def get_credential():
    """
    Single shared credential. DefaultAzureCredential walks the chain:
    env vars -> managed identity -> Azure CLI -> VS / VS Code.
    On a Function App with MI enabled, it lands on the MI path.
    """
    from azure.identity import DefaultAzureCredential
    return DefaultAzureCredential(
        exclude_interactive_browser_credential=True,
        exclude_visual_studio_code_credential=False,
    )


def bearer_token_provider(scope: str) -> Callable[[], str]:
    """Return a callable that produces bearer tokens for the given scope.
    The Azure OpenAI SDK expects this shape for azure_ad_token_provider."""
    from azure.identity import get_bearer_token_provider
    return get_bearer_token_provider(get_credential(), scope)


_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TOKEN_CACHE_LOCK = threading.Lock()


def bearer_token(scope: str) -> str:
    """Cached bearer token fetch for scopes we hit via raw httpx.

    Without caching, every blob fetch / storage call paid 50-300ms (or
    more on cold MSAL caches) to acquire a fresh token. On a PDF with
    200 figures × 2 (crop + vision) = 400 token fetches per document,
    or 20-120s of pure auth overhead on the critical path.

    With this cache, MSAL is hit once per scope per hour. The token is
    refreshed when within 5 minutes of expiry. Thread-safe via lock,
    but the fast path (cache hit, plenty of TTL) is lock-free.
    """
    cached = _TOKEN_CACHE.get(scope)
    now = time.time()
    if cached is not None:
        token, expires_at = cached
        # Refresh proactively at 5-min-before-expiry mark.
        if expires_at > now + 300:
            return token
    with _TOKEN_CACHE_LOCK:
        cached = _TOKEN_CACHE.get(scope)
        if cached is not None:
            token, expires_at = cached
            if expires_at > now + 300:
                return token
        tok = get_credential().get_token(scope)
        _TOKEN_CACHE[scope] = (tok.token, float(tok.expires_on))
        return tok.token
