import pytest
from openai import APIStatusError, BadRequestError, RateLimitError

from griot.client import MetaClient, ModelConfig
from tests.conftest import completion


def _error(cls, status):
    # Built without an HTTP response so the test does not depend on the SDK's transport library.
    exc = cls.__new__(cls)
    Exception.__init__(exc, "boom")
    exc.status_code = status
    return exc


def test_config_requires_key(monkeypatch):
    monkeypatch.setattr("griot.client.load_dotenv", lambda: None)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MODEL_API_KEY"):
        ModelConfig.from_env()


def test_config_defaults(monkeypatch):
    monkeypatch.setattr("griot.client.load_dotenv", lambda: None)
    monkeypatch.setenv("MODEL_API_KEY", "x")
    monkeypatch.delenv("MODEL_API_BASE_URL", raising=False)
    monkeypatch.delenv("MUSE_SPARK_MODEL", raising=False)
    config = ModelConfig.from_env()
    assert (config.base_url, config.model) == ("https://api.meta.ai/v1", "muse-spark-1.3")


def test_retries_transient_errors(make_client):
    client, sdk = make_client([_error(RateLimitError, 429), _error(APIStatusError, 503), completion("ready")])
    assert client.ping() == "ready"
    assert len(sdk.calls) == 3
    assert sdk.calls[0]["model"] == "muse-spark-1.3"


def test_does_not_retry_bad_request(make_client):
    client, sdk = make_client([_error(BadRequestError, 400), completion("never")])
    with pytest.raises(BadRequestError):
        client.ping()
    assert len(sdk.calls) == 1


def test_gives_up_after_max_retries(make_client):
    client, sdk = make_client([_error(RateLimitError, 429)] * 5)
    with pytest.raises(RateLimitError):
        client.ping()
    assert len(sdk.calls) == client.max_retries + 1


@pytest.mark.live
def test_live_connection():
    try:
        client = MetaClient.from_env()
    except RuntimeError:
        pytest.skip("MODEL_API_KEY not set")
    assert "ready" in client.ping().lower()
