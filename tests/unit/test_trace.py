import json

import pytest

from t2s.pipeline.trace import STAGE_IDS, TRACE_FORMAT, CallRecord, RoundRecord, Trace


def test_stages_keys_in_order():
    t = Trace(question="q")
    assert tuple(t.stages) == STAGE_IDS
    assert len(STAGE_IDS) == 11
    assert all(v is None for v in t.stages.values())


def test_set_stage_rejects_unknown():
    with pytest.raises(KeyError):
        Trace(question="q").set_stage("nope", {})


def test_to_json_dumps():
    t = Trace(question="q", sql="SELECT 1")
    t.set_stage("schema", {"names": ("a", "b"), "rows": {"a": 3}})
    t.calls.append(CallRecord(1, "extract_keywords", 1, "h", 3, None, None, None, 0, 0, None, None))
    t.rounds.append(RoundRecord(round=1, old_sql="SELECT 1"))
    out = t.to_json()
    text = json.dumps(out, ensure_ascii=False)
    back = json.loads(text)
    assert back["format"] == TRACE_FORMAT
    assert [s["id"] for s in back["stages"]] == list(STAGE_IDS)
    assert back["stages"][0]["value"]["names"] == ["a", "b"]
    assert back["calls"][0]["k"] == 1
    assert back["rounds"][0]["old_sql"] == "SELECT 1"


def test_to_json_rejects_bytes():
    t = Trace(question="q")
    t.set_stage("schema", {"x": b"1"})
    with pytest.raises(TypeError):
        t.to_json()
