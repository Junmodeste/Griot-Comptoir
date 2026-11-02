import json

from griot.agent import AuditLog, process_page
from tests.conftest import GROUND_TRUTH, SAMPLES, completion

IMAGE = SAMPLES / "synthetic_page_01.jpg"


def _page(**line_overrides):
    page = json.loads((GROUND_TRUTH / "synthetic_page_01.json").read_text(encoding="utf-8"))
    page["lines"][1].update(line_overrides)
    return page


def _events(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]


def test_clean_page_skips_reread(make_client, tmp_path):
    page = _page()
    page["lines"] = page["lines"][:5]  # drop the line with the deliberate arithmetic error
    client, sdk = make_client([completion(json.dumps(page))])
    audit = AuditLog(tmp_path / "audit.jsonl")

    result = process_page(client, IMAGE, audit, work_dir=tmp_path)

    assert len(sdk.calls) == 1 and not result.reread
    assert [e["event"] for e in _events(audit.path)] == ["extracted", "verified", "ready"]


def test_reread_fixes_misread_and_is_audited(make_client, tmp_path):
    misread = _page(raw_text="Huile 3 L x 1.300 = 3.600", total=3600)  # model read 3.600 for 3.900
    client, sdk = make_client([completion(json.dumps(misread)), completion(json.dumps(_page()))])
    audit = AuditLog(tmp_path / "audit.jsonl")

    result = process_page(client, IMAGE, audit, work_dir=tmp_path)

    assert result.reread and len(sdk.calls) == 2
    assert sdk.calls[1]["reasoning_effort"] == "high"
    prompt = sdk.calls[1]["messages"][0]["content"][0]["text"]
    assert "line 2" in prompt and "3 x 1300 = 3900, but 3600 is written" in prompt
    assert {"line": 2, "field": "total", "before": 3600.0, "after": 3900.0} in result.changes
    # Line 6 keeps the writer's own arithmetic error: still in the review queue.
    assert [v.index for v in result.verdict.review_queue] == [5]
    assert [e["event"] for e in _events(audit.path)] == ["extracted", "verified", "reread", "ready"]


def test_reread_that_changes_line_count_is_rejected(make_client, tmp_path):
    misread = _page(total=3600)
    shorter = _page()
    shorter["lines"] = shorter["lines"][:4]
    client, _ = make_client([completion(json.dumps(misread)), completion(json.dumps(shorter))])
    audit = AuditLog(tmp_path / "audit.jsonl")

    result = process_page(client, IMAGE, audit, work_dir=tmp_path)

    assert result.page.lines[1].total == 3600 and result.changes == []
    assert "reread_rejected" in [e["event"] for e in _events(audit.path)]


def test_sideways_photo_is_straightened_and_read_again(make_client, tmp_path):
    sideways = _page()
    sideways["rotation"] = 90
    client, sdk = make_client([completion(json.dumps(sideways))] + [completion(json.dumps(_page()))] * 2)
    audit = AuditLog(tmp_path / "audit.jsonl")

    result = process_page(client, IMAGE, audit, work_dir=tmp_path)

    upright = tmp_path / "upright" / "synthetic_page_01.jpg"
    assert upright.exists() and len(sdk.calls) == 3  # read, read upright, re-read the arithmetic error
    assert result.extracted.rotation == 90 and result.page.rotation == 0
    assert "rotated_and_extracted" in [e["event"] for e in _events(audit.path)]
