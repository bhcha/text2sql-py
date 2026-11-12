"""누설 검사: 정답 문자열을 일부러 넣은 프롬프트를 잡는지 본다(가짜 정답만 쓴다)."""

import json

import pytest

from fixtures import mini_bird as M
from t2s.dataset.config import load_config
from t2s.dataset.state import State
from t2s.eval import leak_audit as L
from t2s.runner import records as R


@pytest.fixture
def mini(tmp_path, shop_db):
    root = M.make_mini_bird(tmp_path / "r", shop_db)
    return root, load_config(root, State())


def make_run(root, prompts: dict[int, list[str]], *, rounds: dict[int, list[str]] | None = None, run_id="lk"):
    run = root / "runs" / run_id
    R.write_json_atomic(run / "run.json", {"run_id": run_id, "kind": "generate", "dataset": "mini", "count": 6})
    recs = []
    for pos in range(6):
        texts = prompts.get(pos, ["plain prompt about products"])
        folder = R.item_dir(run, pos)
        for k, text in enumerate(texts, start=1):
            (folder / "calls" / str(k)).mkdir(parents=True, exist_ok=True)
            (folder / "calls" / str(k) / "prompt.txt").write_text(text, encoding="utf-8")
        trace = {"rounds": [{"old_sql": s} for s in (rounds or {}).get(pos, [])]}
        (folder / "trace.json").write_text(json.dumps(trace))
        rec = {"position": pos, "db_id": "shop", "llm": {"call_count": len(texts)}}
        R.write_json_atomic(folder / "record.json", rec)
        recs.append(rec)
    R.write_jsonl(run / "records.jsonl", recs)
    return run


def test_clean(mini):
    root, config = mini
    make_run(root, {})
    r = L.audit(config, "lk")
    assert r["prompts"] == 6 and not L.leaked(r)
    assert L.leak_summary(r) == (
        "누설 검사: 프롬프트 6개, 정답 SQL 0, evidence 0, 다른 문항 정답 0, 프롬프트 누락 0 (모델이 쓴 SQL이 정답과 같음 0)"
    )
    assert (root / "runs/lk/leak-audit.json").is_file()


def test_own_gold_sql_with_spacing(mini):
    root, config = mini
    spaced = M.GOLD_SQL[1].replace(" ", "\n   ")
    make_run(root, {1: [f"question\n{spaced}\nend"]})
    r = L.audit(config, "lk")
    assert r["gold_sql_hits"] == [1] and r["model_made"] == [] and L.leaked(r)
    assert M.GOLD_SQL[1] not in L.leak_summary(r)


def test_model_made_is_counted_apart(mini):
    root, config = mini
    make_run(root, {2: ["ok", f"fix this: {M.GOLD_SQL[2]}"]}, rounds={2: [M.GOLD_SQL[2]]})
    r = L.audit(config, "lk")
    assert r["model_made"] == [2] and r["gold_sql_hits"] == [] and not L.leaked(r)


def test_evidence_threshold(mini):
    root, config = mini
    assert len(M.EVIDENCE[0]) >= 25
    make_run(root, {0: [f"hint: {M.EVIDENCE[0]}"]})
    assert L.audit(config, "lk")["evidence_hits"] == [0]
    # 24자 이하 evidence는 세지 않는다
    gold = root / "gold/mini"
    items = json.loads((gold / "answers.json").read_text())
    items[3]["evidence"] = "x" * 24
    (gold / "answers.json").write_text(json.dumps(items))
    M.write_gold_toml(gold)
    make_run(root, {3: ["x" * 24]}, run_id="lk2")
    assert L.audit(config, "lk2")["evidence_hits"] == []


def test_other_gold(mini):
    root, config = mini
    assert len(M.GOLD_SQL[4]) >= 40 and len(M.GOLD_SQL[0]) < 40
    make_run(root, {0: [f"see {M.GOLD_SQL[4]}"], 1: [f"see {M.GOLD_SQL[0]}"]})
    r = L.audit(config, "lk")
    assert r["other_gold_hits"] == [0]


def test_missing_prompt_and_attempt_folders(mini):
    root, config = mini
    run = make_run(root, {5: ["a", "b"]})
    (R.item_dir(run, 5) / "calls/2/prompt.txt").unlink()
    old = run / "items" / "0004.attempt-1" / "calls" / "1"
    old.mkdir(parents=True)
    (old / "prompt.txt").write_text(f"old attempt {M.GOLD_SQL[4]}")
    r = L.audit(config, "lk")
    assert r["prompts_missing"] == [5]
    assert r["gold_sql_hits"] == [4]  # 이전 시도 폴더도 본다
    assert r["prompts"] == 7


def test_rescore_note(mini, tmp_path):
    _root, config = mini
    from t2s.eval import bird

    bird.import_predictions(config, "mini", M.predictions_jsonl(tmp_path / "p.jsonl", M.GOLD_SQL), "rs")
    r = L.audit(config, "rs")
    assert r["note"] == "프롬프트 없음(재채점 run)" and not L.leaked(r)
    assert L.leak_summary(r) == "누설 검사: 프롬프트 없음(재채점 run)"
