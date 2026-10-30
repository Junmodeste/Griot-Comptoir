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
class ModelConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL

    @classmethod
    def from_env(cls) -> "ModelConfig":
        load_dotenv()
        api_key = os.environ.get("MODEL_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("MODEL_API_KEY is not set. Copy .env.example to .env and fill it in.")
        return cls(
            api_key=api_key,
            base_url=os.environ.get("MODEL_API_BASE_URL") or DEFAULT_BASE_URL,
            model=os.environ.get("MUSE_SPARK_MODEL") or DEFAULT_MODEL,
        )


class MetaClient:
    """Chat completions with retry on transient failures."""

    def __init__(self, config: ModelConfig, *, max_retries: int = 4, sdk: OpenAI | None = None):
        self.config = config
        self.max_retries = max_retries
        # SDK retries are disabled so that our own policy is the only one.
        self.sdk = sdk or OpenAI(base_url=config.base_url, api_key=config.api_key, max_retries=0)

    @classmethod
    def from_env(cls) -> "MetaClient":
        return cls(ModelConfig.from_env())

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
            reasoning_effort="minimal",
        )
        return (response.choices[0].message.content or "").strip()
