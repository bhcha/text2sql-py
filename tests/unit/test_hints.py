"""dataset/hints.py(공개 evidence 파일 읽기, 문항 조건 잇기)와 스터디 입력의 keep_conditions 두 갈래."""

import json

import pytest

from t2s.dataset.hints import HINT_RULES_VERSION, join_conditions, load_public_hints
from t2s.dataset.questions import PublicQuestion, QuestionsError, load_questions_json, load_study_inputs

QS = [PublicQuestion(i, 900 + i, "shop", f"q{i}?", "simple") for i in range(3)]


def _hints(tmp_path, items):
    p = tmp_path / "hints.public.json"
    p.write_text(json.dumps(items), encoding="utf-8")
    return p


def test_load_public_hints_ok(tmp_path):
    p = _hints(tmp_path, [{"question_id": 900 + i, "hint": f"h{i}" if i else ""} for i in range(3)])
    assert load_public_hints(p, QS) == ["", "h1", "h2"]
    assert HINT_RULES_VERSION == "1.0.0"


@pytest.mark.parametrize(
    ("items", "words"),
    [
        ([{"question_id": 900, "hint": "a"}], "항목 수 1"),
        ([{"question_id": 900 + i, "hint": "a", "SQL": "SECRET"} for i in range(3)], "0번 항목의 칸"),
        (
            [{"question_id": 900, "hint": "a"}, {"question_id": 999, "hint": "b"}, {"question_id": 902, "hint": "c"}],
            "1번 항목의 question_id",
        ),
        ([{"question_id": 900 + i, "hint": 3} for i in range(3)], "0번 항목의 hint가 글이 아닙니다"),
        ({"a": 1}, "목록이 아닙니다"),
    ],
)
def test_load_public_hints_mismatch_stops(tmp_path, items, words):
    with pytest.raises(QuestionsError, match=words) as e:
        load_public_hints(_hints(tmp_path, items), QS)
    assert "SECRET" not in str(e.value)


def test_join_conditions():
    assert join_conditions([]) == ""
    assert join_conditions(["  a b ", "c;d", "\t{x}\n"]) == "a b; c;d; {x}"
    with pytest.raises(QuestionsError):
        join_conditions(["a", 3])


STUDY_ITEM = {"id": "a-1", "question": "q?", "question_type": "sql", "task_conditions": ["SECRET-COND line", " two "]}


def _study(tmp_path, items):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"description": "d", "items": items}), encoding="utf-8")
    return p


def test_keep_conditions_two_branches(tmp_path):
    p = _study(tmp_path, [STUDY_ITEM, {**STUDY_ITEM, "id": "a-2", "task_conditions": []}])
    qs, extra = load_study_inputs(p, "olist")
    assert extra["conditions"] == "not_used" and len(qs) == 2
    qs, extra, conditions = load_study_inputs(p, "olist", keep_conditions=True)
    assert extra["conditions"] == "used" and conditions == [["SECRET-COND line", " two "], []]
    assert "SECRET" not in json.dumps(extra)
    qs, extra, conditions = load_questions_json(p, "olist", keep_conditions=True)
    assert conditions[0] == ["SECRET-COND line", " two "]
    pub = tmp_path / "pub.json"
    pub.write_text(json.dumps([{"question_id": 1, "db_id": "olist", "question": "q", "difficulty": ""}]))
    assert load_questions_json(pub, "olist", keep_conditions=True)[2] is None
    bad = _study(tmp_path, [{**STUDY_ITEM, "task_conditions": "not a list"}])
    with pytest.raises(QuestionsError, match="0번 항목의 task_conditions"):
        load_study_inputs(bad, "olist", keep_conditions=True)
