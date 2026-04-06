"""
Azure OpenAI client wrapper. Uses the openai SDK in Azure mode.
"""

import os
from functools import lru_cache
from openai import AzureOpenAI


@lru_cache(maxsize=1)
def get_client() -> AzureOpenAI:
    return AzureOpenAI(
        api_key=os.environ["AOAI_API_KEY"],
        api_version=os.environ.get("AOAI_API_VERSION", "2024-08-01-preview"),
        azure_endpoint=os.environ["AOAI_ENDPOINT"],
    )


def vision_deployment() -> str:
    return os.environ["AOAI_VISION_DEPLOYMENT"]


def chat_deployment() -> str:
    return os.environ["AOAI_CHAT_DEPLOYMENT"]
