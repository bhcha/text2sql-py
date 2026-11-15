import json

import pytest

from t2s.dataset.questions import QuestionsError, load_public_questions


def test_four_keys_ok(tmp_path):
    p = tmp_path / "q.json"
    p.write_text(json.dumps([{"question_id": 1, "db_id": "a", "question": "q?", "difficulty": "simple"}]))
    qs = load_public_questions(p)
    assert qs[0].position == 0 and qs[0].db_id == "a"


def test_extra_keys_rejected_without_values(tmp_path):
    p = tmp_path / "q.json"
    item = {
        "question_id": 1,
        "db_id": "a",
        "question": "q?",
        "evidence": "SECRET-EV",
        "SQL": "SELECT SECRET",
        "difficulty": "simple",
    }
    p.write_text(json.dumps([item]))
    with pytest.raises(QuestionsError) as e:
        load_public_questions(p)
    msg = str(e.value)
    assert "0번" in msg and "6개" in msg
    assert "SECRET" not in msg


def test_text_questions(tmp_path):
    from t2s.dataset.questions import load_text_questions, to_public_json

    p = tmp_path / "q.txt"
    p.write_text("# 머리말\n\nHow many products?\n  List names.  \n#skip\nLast one\n", encoding="utf-8")
    qs = load_text_questions(p, "shop")
    assert [(q.position, q.question_id, q.db_id, q.question, q.difficulty) for q in qs] == [
        (0, 0, "shop", "How many products?", ""),
        (1, 1, "shop", "List names.", ""),
        (2, 2, "shop", "Last one", ""),
    ]
    assert set(to_public_json(qs)[0]) == {"question_id", "db_id", "question", "difficulty"}
    (tmp_path / "e.txt").write_text("# only comments\n\n", encoding="utf-8")
    with pytest.raises(QuestionsError):
        load_text_questions(tmp_path / "e.txt", "shop")


def _study(tmp_path, items, name="m.json"):
    p = tmp_path / name
    p.write_text(json.dumps({"description": "d", "items": items}), encoding="utf-8")
    return p


STUDY_ITEM = {"id": "a-1", "question": "q?", "question_type": "sql", "task_conditions": ["SECRET-COND line"]}


def test_study_inputs_drop_conditions(tmp_path):
    from t2s.dataset.questions import load_study_inputs, to_public_json

    p = _study(tmp_path, [STUDY_ITEM, {**STUDY_ITEM, "id": "a-2", "question": "r?"}])
    qs, extra = load_study_inputs(p, "olist")
    assert [(q.position, q.question_id, q.db_id, q.question, q.difficulty) for q in qs] == [
        (0, 0, "olist", "q?", ""),
        (1, 1, "olist", "r?", ""),
    ]
    assert extra["ids"] == ["a-1", "a-2"] and extra["input_format"] == "study-model-inputs"
    assert extra["conditions"] == "not_used" and len(extra["source_sha256"]) == 64
    assert "SECRET" not in json.dumps(to_public_json(qs)) and "SECRET" not in json.dumps(extra)


@pytest.mark.parametrize(
    ("item", "words"),
    [
        ({**STUDY_ITEM, "gold_sql": "SELECT SECRET"}, ("0번", "5개")),
        ({k: v for k, v in STUDY_ITEM.items() if k != "task_conditions"}, ("0번", "3개")),
        ({**STUDY_ITEM, "question_type": "manual"}, ("0번", "SQL 문항이 아닙니다")),
    ],
)
def test_study_inputs_rejected_without_values(tmp_path, item, words):
    from t2s.dataset.questions import load_study_inputs

    with pytest.raises(QuestionsError) as e:
        load_study_inputs(_study(tmp_path, [item]), "olist")
    assert all(w in str(e.value) for w in words) and "SECRET" not in str(e.value)


def test_study_inputs_duplicate_id_and_shape(tmp_path):
    from t2s.dataset.questions import load_study_inputs

    with pytest.raises(QuestionsError, match="1번 항목의 id가"):
        load_study_inputs(_study(tmp_path, [STUDY_ITEM, STUDY_ITEM]), "olist")
    (tmp_path / "n.json").write_text(json.dumps({"description": "no items"}), encoding="utf-8")
    with pytest.raises(QuestionsError, match="items"):
        load_study_inputs(tmp_path / "n.json", "olist")


def test_questions_json_dispatch(tmp_path):
    from t2s.dataset.questions import load_questions_json

    pub = tmp_path / "p.json"
    pub.write_text(json.dumps([{"question_id": 1, "db_id": "a", "question": "q?", "difficulty": "simple"}]))
    qs, extra = load_questions_json(pub, None)
    assert extra is None and qs[0].db_id == "a"
    qs, extra = load_questions_json(_study(tmp_path, [STUDY_ITEM]), "olist")
    assert extra["ids"] == ["a-1"] and qs[0].db_id == "olist"
    with pytest.raises(QuestionsError, match="소스가 하나인"):
        load_questions_json(_study(tmp_path, [STUDY_ITEM]), None)
    (tmp_path / "s.json").write_text('"text"', encoding="utf-8")
    with pytest.raises(QuestionsError, match="목록도 사전도"):
        load_questions_json(tmp_path / "s.json", "olist")
