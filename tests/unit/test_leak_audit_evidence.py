"""evidence used run의 누설 검사: evidence 글은 누설로 세지 않고, 빠짐을 센다. 정답 SQL·다른 문항 정답·채점 메모는 센다."""

import json

import pytest

from fixtures import mini_bird as M
from t2s.dataset.config import load_config
from t2s.dataset.state import State
from t2s.eval import leak_audit as L
from t2s.runner import records as R


def make_run(root, dataset, count, prompts, *, used, run_id="ev"):
    run = root / "runs" / run_id
    meta = {"run_id": run_id, "kind": "generate", "dataset": dataset, "count": count}
    if used is not None:
        meta["evidence"] = {"mode": "used" if used else "not_used"}
    R.write_json_atomic(run / "run.json", meta)
    recs = []
    for pos in range(count):
        texts = prompts.get(pos, ["plain prompt about products"])
        folder = R.item_dir(run, pos)
        for k, text in enumerate(texts, start=1):
            (folder / "calls" / str(k)).mkdir(parents=True, exist_ok=True)
            (folder / "calls" / str(k) / "prompt.txt").write_text(text, encoding="utf-8")
        (folder / "trace.json").write_text(json.dumps({"rounds": []}))
        rec = {"position": pos, "db_id": "shop" if dataset == "mini" else dataset, "llm": {"call_count": len(texts)}}
        R.write_json_atomic(folder / "record.json", rec)
        recs.append(rec)
    R.write_jsonl(run / "records.jsonl", recs)
    return run


@pytest.fixture
def mini(tmp_path, shop_db):
    root = M.make_mini_bird(tmp_path / "r", shop_db)
    return root, load_config(root, State())


def test_bird_used_counts_missing_not_given(mini):
    root, config = mini
    prompts = {i: [f"hint: {M.EVIDENCE[i]}", f"again {M.EVIDENCE[i]}"] for i in range(6)}
    prompts[2] = ["no hint here", "still none"]
    prompts[3] = [f"hint: {M.EVIDENCE[3]} {M.GOLD_SQL[3]}"]
    prompts[4] = [f"hint: {M.EVIDENCE[4]} see {M.GOLD_SQL[5]}"]
    make_run(root, "mini", 6, prompts, used=True)
    r = L.audit(config, "ev")
    assert r["evidence_hits"] == [] and r["evidence_missing"] == [2]
    assert r["gold_sql_hits"] == [3] and r["other_gold_hits"] == [4] and L.leaked(r)
    s = L.leak_summary(r)
    assert "정답 SQL 1, evidence 제공(used)·빠짐 1, 다른 문항 정답 1, 프롬프트 누락 0" in s
    assert not any(e in s for e in M.EVIDENCE)
    short = L.short(r)
    assert short["evidence_missing"] == 1 and short["total"] == 3


def test_bird_used_clean(mini):
    root, config = mini
    make_run(root, "mini", 6, {i: [f"hint: {M.EVIDENCE[i]}"] for i in range(6)}, used=True)
    r = L.audit(config, "ev")
    assert not L.leaked(r)
    assert L.leak_summary(r) == (
        "누설 검사: 프롬프트 6개, 정답 SQL 0, evidence 제공(used)·빠짐 0, 다른 문항 정답 0, 프롬프트 누락 0"
        " (모델이 쓴 SQL이 정답과 같음 0)"
    )


def test_not_used_counts_evidence_as_before(mini):
    root, config = mini
    for used, rid in ((False, "a"), (None, "b")):
        make_run(root, "mini", 6, {0: [f"hint: {M.EVIDENCE[0]}"]}, used=used, run_id=rid)
        r = L.audit(config, rid)
        assert r["evidence_hits"] == [0] and "evidence_missing" not in r and "evidence_mode" not in r
        assert "evidence 1, 다른 문항 정답 0" in L.leak_summary(r)


def test_study_used_counts_notes(tmp_path, shop_db):
    from fixtures import mini_study as MS

    root = MS.make_mini_study(tmp_path / "s", shop_db)
    config = load_config(root, State())
    joined = ["; ".join(c.strip() for c in cs) for cs in MS.CONDITIONS]
    prompts = {i: [f"hint: {joined[i]}"] for i in range(4)}
    prompts[1] = [f"hint: {joined[1]} {MS.NOTES[1]}"]
    prompts[3] = ["nothing"]
    make_run(root, "olist", 4, prompts, used=True)
    r = L.audit(config, "ev")
    assert r["evidence_hits"] == [1] and r["evidence_missing"] == [3] and r["evidence_label"] == "채점 메모"
    s = L.leak_summary(r)
    assert "evidence 제공(used)·빠짐 1, 채점 메모 1, 다른 문항 정답 0" in s
    assert MS.MARK not in s and MS.COND_MARK not in s
