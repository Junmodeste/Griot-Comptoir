from pathlib import Path
from types import SimpleNamespace

import pytest

from griot.client import MetaClient, ModelConfig

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"
GROUND_TRUTH = ROOT / "eval" / "ground_truth"


class FakeSDK:
    """Stands in for openai.OpenAI: records calls, replays scripted outcomes."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def completion(content: str, model: str = "muse-spark-1.3"):
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=300),
    )


@pytest.fixture
def make_client(monkeypatch):
    monkeypatch.setattr("griot.client.time.sleep", lambda _: None)

    def factory(outcomes):
        sdk = FakeSDK(outcomes)
        return MetaClient(ModelConfig(api_key="test-key"), sdk=sdk), sdk

    return factory
