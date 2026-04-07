"""
Azure OpenAI client wrapper. Uses the openai SDK in Azure mode.

All env access goes through shared.config so missing settings raise
ConfigError (typed) instead of KeyError mid-request.
"""

from functools import lru_cache
from openai import AzureOpenAI

from .config import required_env, optional_env


@lru_cache(maxsize=1)
def get_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=required_env("AOAI_API_KEY"),
        api_version=optional_env("AOAI_API_VERSION", "2024-08-01-preview"),
        azure_endpoint=required_env("AOAI_ENDPOINT"),
    )


def vision_deployment() -> str:
    return required_env("AOAI_VISION_DEPLOYMENT")


def chat_deployment() -> str:
    return required_env("AOAI_CHAT_DEPLOYMENT")
