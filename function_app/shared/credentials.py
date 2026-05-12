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

    Retries: IMDS (the Managed Identity endpoint) occasionally returns
    transient 500s under load. MSAL has NO built-in retry for these.
    Without our wrapper retry, any single IMDS hiccup turns into a
    per-record skill failure that counts toward maxFailedItems and
    pushes us closer to indexer abort. 3 retries with exponential
    backoff (0.5s, 1s, 2s) covers all observed transient cases.
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
        # Refresh `now` under the lock in case we blocked a long time.
        now = time.time()
        if cached is not None:
            token, expires_at = cached
            if expires_at > now + 300:
                return token
        # Retry IMDS transient failures (500/503/timeouts).
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                tok = get_credential().get_token(scope)
                _TOKEN_CACHE[scope] = (tok.token, float(tok.expires_on))
                return tok.token
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
        # Exhausted retries -- re-raise the last exception so the
        # calling skill returns a structured error envelope.
        raise last_exc if last_exc else RuntimeError("bearer_token failed")
