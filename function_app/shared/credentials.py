"""
Central credential helper.

Prefer Azure Managed Identity in the Function App. Fall back to API keys
only when AUTH_MODE=key is explicitly set (useful for local dev / tests
running outside Azure).

azure-identity is imported lazily so unit tests and local key-only runs
do not require the package to be installed.
"""

from collections.abc import Callable
from functools import lru_cache

from .config import optional_env

AOAI_SCOPE = "https://cognitiveservices.azure.com/.default"
STORAGE_SCOPE = "https://storage.azure.com/.default"
SEARCH_SCOPE = "https://search.azure.com/.default"
DI_SCOPE = "https://cognitiveservices.azure.com/.default"


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


def bearer_token(scope: str) -> str:
    """One-shot bearer token fetch for scopes we hit via raw httpx."""
    return get_credential().get_token(scope).token
