import pytest

from t2s.fetch import public_questions as pq


def _item(**kw):
    base = {
        "question_id": 1,
        "db_id": "a",
        "question": "가?",
        "evidence": "SECRET",
        "SQL": "SELECT 1",
        "difficulty": "simple",
    }
    base.update(kw)
    return base


def test_build_and_serialize():
    items = pq.build_public_items([_item()])
    assert items == [{"question_id": 1, "db_id": "a", "question": "가?", "difficulty": "simple"}]
    text = pq.serialize_public(items)
    assert (
        text
        == '[\n  {\n    "question_id": 1,\n    "db_id": "a",\n    "question": "가?",\n    "difficulty": "simple"\n  }\n]\n'
    )
    assert pq.check_public_against_official(items, [_item()]) == []


def test_key_order_rejected_without_values():
    bad = {"db_id": "a", "question_id": 1, "question": "q", "evidence": "SECRET", "SQL": "S", "difficulty": "d"}
    with pytest.raises(pq.PublicQuestionsError) as e:
        pq.build_public_items([_item(), bad])
    assert str(e.value) == "공식 문제 1번 항목의 키가 예상과 다름"


def test_check_reports_positions_only():
    items = pq.build_public_items([_item(), _item(question_id=2)])
    official = [_item(), _item(question_id=3)]
    errs = pq.check_public_against_official(items, official)
    assert errs == ["공개 문항 1번 항목의 question_id가 공식 파일과 다름"]
