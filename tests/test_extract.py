import datetime as dt
import json

import pytest

from griot.extract import ExtractionError, extract_page, image_to_data_url
from griot.schema import LedgerPage, json_schema_format
from tests.conftest import GROUND_TRUTH, SAMPLES, completion

PAGE = SAMPLES / "synthetic_page_01.jpg"


def test_schema_is_flat_and_closed():
    schema = json_schema_format()["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    line = schema["$defs"]["LedgerLine"]
    assert line["additionalProperties"] is False
    # Every field is required (nullable when unknown) so the model cannot silently drop one.
    assert set(line["required"]) == set(line["properties"])
    assert "LedgerPage" not in json.dumps(schema["$defs"])  # no recursion (rejected by the API)


def test_image_data_url():
    assert image_to_data_url(PAGE).startswith("data:image/jpeg;base64,/9j/")
    with pytest.raises(ValueError):
        image_to_data_url(GROUND_TRUTH / "synthetic_page_01.json")


def test_extract_page_validates_model_output(make_client):
    truth = (GROUND_TRUTH / "synthetic_page_01.json").read_text(encoding="utf-8")
    client, sdk = make_client([completion(truth)])

    result = extract_page(client, PAGE, reference_date=dt.date(2026, 10, 30))

    assert result.page == LedgerPage.model_validate_json(truth)
    assert (result.prompt_tokens, result.completion_tokens) == (1200, 300)
    call = sdk.calls[0]
    assert call["response_format"]["type"] == "json_schema"
    system, user = call["messages"]
    assert isinstance(system["content"], str)  # images are rejected outside user messages
    assert user["content"][1]["type"] == "image_url"
    assert "2026-10-30" in user["content"][0]["text"]


def test_extract_page_rejects_invalid_output(make_client):
    bad = json.dumps({"page_date": None, "currency": "XOF", "lines": [{"raw_text": "x"}], "notes": None})
    client, _ = make_client([completion(bad)])
    with pytest.raises(ExtractionError) as info:
        extract_page(client, PAGE)
    assert info.value.raw_output == bad
