"""
Azure OpenAI client wrapper.

Prefers managed-identity auth (AUTH_MODE=mi, the default). Falls back to
API key auth when AUTH_MODE=key is set or AOAI_API_KEY is provided and
MI is disabled.
"""

from functools import lru_cache

from openai import AzureOpenAI

from .config import optional_env, required_env
from .credentials import (
    AOAI_SCOPE,
    bearer_token_provider,
    use_managed_identity,
)


@lru_cache(maxsize=1)
def get_client() -> AzureOpenAI:
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
