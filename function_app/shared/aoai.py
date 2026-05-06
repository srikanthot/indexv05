"""
Azure OpenAI client wrapper.

Prefers managed-identity auth (AUTH_MODE=mi, the default). Falls back to
API key auth when AUTH_MODE=key is set or AOAI_API_KEY is provided and
MI is disabled.

The `openai` package is imported lazily inside get_client() rather than
at module top-level so that:
  - shared modules that transitively `from .aoai import ...` (e.g. when
    a test pulls `normalize_figure_ref` out of diagram.py) don't fail
    at import time on a machine without `openai` installed
  - the package is still required at runtime for any code path that
    actually instantiates the client (preanalyze vision calls, the
    Function App vision skill)

Both requirements files declare `openai`; the lazy import is for
defensive dev-time UX, not a license to skip the dependency.
"""

from functools import lru_cache

from .config import optional_env, required_env
from .credentials import (
    AOAI_SCOPE,
    bearer_token_provider,
    use_managed_identity,
)


@lru_cache(maxsize=1)
def get_client():
    """Returns an AzureOpenAI client. Imported lazily so that loading
    this module does not require the `openai` package — only calling
    this function does."""
    from openai import AzureOpenAI
    # 2024-12-01-preview is the minimum API version that supports gpt-4.1
    # chat + vision deployments. Override via AOAI_API_VERSION app setting.
    api_version = optional_env("AOAI_API_VERSION", "2024-12-01-preview")
    endpoint = required_env("AOAI_ENDPOINT")

    if use_managed_identity():
        return AzureOpenAI(
            api_version=api_version,
            azure_endpoint=endpoint,
            azure_ad_token_provider=bearer_token_provider(AOAI_SCOPE),
        )

    return AzureOpenAI(
        api_key=required_env("AOAI_API_KEY"),
        api_version=api_version,
        azure_endpoint=endpoint,
    )


def vision_deployment() -> str:
    return required_env("AOAI_VISION_DEPLOYMENT")


def chat_deployment() -> str:
    return required_env("AOAI_CHAT_DEPLOYMENT")
