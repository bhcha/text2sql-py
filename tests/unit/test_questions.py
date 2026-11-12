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
