"""Thin client for the Meta Model API (OpenAI-compatible).

Facts taken from meta-models/meta-model-cookbook (01_api_fundamentals):
base URL https://api.meta.ai/v1, key in MODEL_API_KEY, retry only on
429 / 5xx / timeouts, never on other 4xx.
"""

from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"


@dataclass(frozen=True)
class Provider:
    key_env: str
    base_url_env: str
    base_url: str
    model_env: str
    model: str


# Muse Spark on Meta Model API, and the open-weight Muse Spark Open on a hosted provider
# (dev.meta.ai/docs/muse-spark-open/deploy#hosted-providers). Both speak the OpenAI chat API.
PROVIDERS = {
    "meta": Provider("MODEL_API_KEY", "MODEL_API_BASE_URL", DEFAULT_BASE_URL, "MUSE_SPARK_MODEL", DEFAULT_MODEL),
    "baseten": Provider("BASETEN_API_KEY", "BASETEN_BASE_URL", "https://inference.baseten.co/v1",
                        "MUSE_SPARK_OPEN_MODEL", "meta-models/muse-spark-open"),
}


@dataclass(frozen=True)
class ModelConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL

    @classmethod
    def from_env(cls, provider: str = "meta") -> "ModelConfig":
        load_dotenv()
        p = PROVIDERS[provider]
        api_key = os.environ.get(p.key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"{p.key_env} is not set. Copy .env.example to .env and fill it in.")
        return cls(
            api_key=api_key,
            base_url=os.environ.get(p.base_url_env) or p.base_url,
            model=os.environ.get(p.model_env) or p.model,
        )


class MetaClient:
    """Chat completions with retry on transient failures."""

    def __init__(self, config: ModelConfig, *, max_retries: int = 4, sdk: OpenAI | None = None):
        self.config = config
        self.max_retries = max_retries
        # SDK retries are disabled so that our own policy is the only one.
        self.sdk = sdk or OpenAI(base_url=config.base_url, api_key=config.api_key, max_retries=0)

    @classmethod
    def from_env(cls, provider: str = "meta") -> "MetaClient":
        return cls(ModelConfig.from_env(provider))

    def chat(self, messages: list[dict], **kwargs):
        kwargs.setdefault("model", self.config.model)
        for attempt in range(self.max_retries + 1):
            try:
                return self.sdk.chat.completions.create(messages=messages, **kwargs)
            except (RateLimitError, APITimeoutError, APIConnectionError):
                if attempt == self.max_retries:
                    raise
            except APIStatusError as exc:
                if exc.status_code < 500 or attempt == self.max_retries:
                    raise
            time.sleep(0.5 * 2**attempt + random.uniform(0, 0.5))
        raise AssertionError("unreachable")

    def ping(self) -> str:
        """One-word round trip that proves the key, base URL and model work."""
        response = self.chat(
            [{"role": "user", "content": "Reply with the single word: ready"}],
            reasoning_effort="low",  # supported by Muse Spark and Muse Spark Open
        )
        return (response.choices[0].message.content or "").strip()
