"""스터디 채점(eval/study.py score)을 손으로 만든 run 폴더와 가짜 정답으로 시험한다."""

import hashlib
import json

import pytest

from fixtures import mini_study as M
from t2s.dataset.config import load_config
from t2s.dataset.state import State
from t2s.eval import bird
from t2s.eval import gold as G
from t2s.eval import study as S
from t2s.runner import records as R


@pytest.fixture
def mini(tmp_path, shop_db):
    root = M.make_mini_study(tmp_path / "r", shop_db)
    return root, load_config(root, State())


def make_run(root, sqls, *, run_id="st", ids=None, questions=None, llm=None):
    run = root / "runs" / run_id
    run.mkdir(parents=True)
    qs = questions or M.QUESTIONS[: len(sqls)]
    public = [{"question_id": i, "db_id": "olist", "question": q, "difficulty": ""} for i, q in enumerate(qs)]
    R.write_json_atomic(run / "questions.json", public)
    records = [
        {
            "position": i,
            "question_id": i,
            "db_id": "olist",
            "difficulty": "",
            "status": "generated" if s is not None else "no_sql",
            "sql": s,
        }
        for i, s in enumerate(sqls)
    ]
    R.write_jsonl(run / "records.jsonl", records)
    (run / "predictions.json").write_text("{}", encoding="utf-8")
    meta = {
        "run_id": run_id,
        "kind": "generate",
        "dataset": "olist",
        "count": len(sqls),
        "sample": len(sqls) != 4,
        "questions": {
            "path": f"runs/{run_id}/questions.json",
            "input_format": "study-model-inputs",
            "ids": ids or M.IDS[: len(sqls)],
            "conditions": "not_used",
        },
        "llm": llm or {"backend": "fake", "model": "fake", "reasoning_effort": None},
        "git": {"head": "abcdef1234", "dirty": False},
    }
    R.write_json_atomic(run / "run.json", meta)
    R.write_json_atomic(
        run / "manifest.json",
        {
            "records": {"sha256": R.sha256_file(run / "records.jsonl")},
            "predictions": {"sha256": R.sha256_file(run / "predictions.json")},
        },
    )
    return run


FAKE = ["SELECT COUNT(*) FROM product"] * 4


def test_score_two_of_four(mini):
    root, config = mini
    run = make_run(root, FAKE)
    db = root / "data/olist/olist.db"
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    s = S.score(config, "st")
    assert s["ex_total"] == {"correct": 2, "denominator": 4, "percent": 50.0}
    assert s["cross_check"] is True and s["cross_check_label"] == "정답 대조"
    assert s["cross_check_detail"] == {"checked": 4, "equal": 4, "mismatch_positions": []}
    assert s["data_unchanged"] is True and hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert s["evaluator"]["id"] == "study-run-baseline-290ff9b" and s["conditions"] == "not_used"
    assert s["exec_counts"]["ok"] == 4 and s["by_difficulty"] == {}
    assert s["evaluator"]["timeout_seconds"] == 10.0
    answers = hashlib.sha256((root / "gold/olist/study-v0.3.json").read_bytes()).hexdigest()
    assert s["label"] == (
        f"Olist 스터디 평가셋 v0.3 / SQLite / {answers[:8]} / 4문제 / EX(스터디 결과 비교) / 문항 조건 not_used"
        " / 로컬 측정 / fake·추론 None·fake / 1회 실행 / 구현 Python abcdef1 / 가짜 백엔드·점수 아님"
    )
    rows = R.read_jsonl(run / "ex.jsonl")
    assert [r["ex_match"] for r in rows] == [1, 0, 0, 1]
    assert set(rows[0]) == {"position", "question_id", "id", "db_id", "difficulty", "status", "ex_match", "pred_exec"}
    assert rows[0]["id"] == "t-001" and rows[0]["difficulty"] == ""
    detail = R.read_jsonl(G.work_dir(root, "st") / S.DETAIL_FILE)
    assert [d["pred_code"] for d in detail] == ["match", "row_count", "value_diff", "match"]
    assert all(d["gold_match"] for d in detail)
    # 정답 글자는 run 폴더에 없다
    text = "".join(p.read_text(encoding="utf-8") for p in run.rglob("*") if p.is_file())
    assert M.MARK not in text and M.COND_MARK not in text
    assert "(정답 " not in text  # 이유 글(정답 열·행 수가 든 것)은 gold/_work에만
    assert bird.score_line(s).startswith("score: Olist 스터디 평가셋 v0.3")
    assert "정답 대조 True" in bird.score_line(s) and not bird.score_failed(s)


def test_no_sql_and_statement_rules(mini):
    root, config = mini
    make_run(root, [None, "SELECT 1; SELECT 2", "SELECT COUNT(*) FROM customer;", "DELETE FROM product"])
    s = S.score(config, "st", meta_time_out=5)
    rows = R.read_jsonl(root / "runs/st/ex.jsonl")
    assert [r["pred_exec"] for r in rows] == ["no_sql", "multi_statement", "ok", "denied"]
    assert [r["ex_match"] for r in rows] == [0, 0, 1, 0]
    assert s["evaluator"]["timeout_seconds"] == 5.0 and s["status_counts"]["no_sql"] == 1


def test_sample_label(mini):
    root, config = mini
    make_run(root, FAKE[:2])
    s = S.score(config, "st")
    assert s["sample"] is True and s["label"].endswith(" / 소표본·점수 아님 / 가짜 백엔드·점수 아님")
    assert s["denominator"] == 2


def test_gold_mismatch_fails_cross_check(mini):
    root, config = mini
    items = json.loads((root / "gold/olist/study-v0.3.json").read_text(encoding="utf-8"))
    items[2]["expected_rows"] = [[999]]
    M.write_gold(root, items)
    make_run(root, FAKE)
    s = S.score(config, "st")
    assert s["cross_check"] is False and s["cross_check_detail"]["mismatch_positions"] == [2]
    assert bird.score_failed(s)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ids": ["t-001", "t-XXX", "t-003", "t-004"]},
        {"questions": [M.QUESTIONS[0], "Another question?", *M.QUESTIONS[2:]]},
    ],
)
def test_join_mismatch_stops_with_position_only(mini, kwargs):
    root, config = mini
    make_run(root, FAKE, **kwargs)
    with pytest.raises(bird.ScoreError) as e:
        S.score(config, "st")
    assert str(e.value) == "run의 문항과 정답 파일이 어긋납니다(위치 1)"
    assert not (root / "runs/st/score.json").exists()


def test_run_without_study_ids_is_refused(mini):
    root, config = mini
    run = make_run(root, FAKE)
    meta = R.read_json(run / "run.json")
    del meta["questions"]["ids"]
    R.write_json_atomic(run / "run.json", meta)
    with pytest.raises(bird.ScoreError, match="스터디 모델 입력 파일"):
        S.score(config, "st")


def test_hash_checks(mini):
    root, config = mini
    run = make_run(root, FAKE)
    (run / "predictions.json").write_text('{"0": "x"}', encoding="utf-8")
    with pytest.raises(bird.ScoreError, match="predictions.json"):
        S.score(config, "st")
    (run / "predictions.json").write_text("{}", encoding="utf-8")
    (root / "gold/olist/study-v0.3.json").write_text("[]", encoding="utf-8")
    with pytest.raises(G.GoldError):
        S.score(config, "st")


def test_leak_texts(mini):
    root, _ = mini
    texts = S.leak_texts(G.load_gold(root, "olist"))
    assert len(texts) == 4 and texts[0]["sql"] == M.GOLD_SQL[0]
    assert texts[0]["hints"] == [*M.CONDITIONS[0], M.NOTES[0]]
