import json
import shutil

import pytest

from fixtures import mini_bird as M
from t2s.dataset.config import load_config
from t2s.dataset.state import State
from t2s.eval import bird
from t2s.eval import gold as G
from t2s.runner import records as R

pytest.importorskip("func_timeout")

PRED = [M.GOLD_SQL[0], M.GOLD_SQL[1], M.GOLD_SQL[2], M.GOLD_SQL[3], "SELECT price FROM product", None]


@pytest.fixture
def mini(tmp_path, shop_db):
    root = M.make_mini_bird(tmp_path / "r", shop_db)
    return root, load_config(root, State())


def rescore(mini, tmp_path, sqls=PRED, run_id="rs"):
    _root, config = mini
    path = M.predictions_jsonl(tmp_path / "p.jsonl", sqls)
    bird.import_predictions(config, "mini", path, run_id)
    return bird.score(config, run_id, num_cpus=2)


def test_score_four_of_six(mini, tmp_path):
    root, _config = mini
    s = rescore(mini, tmp_path)
    assert s["ex_total"] == {"correct": 4, "denominator": 6, "percent": 66.67}
    assert s["cross_check"] is True and s["data_unchanged"] is True and s["sample"] is False
    assert s["status_counts"] == {"generated": 5, "no_sql": 1, "exception": 0, "not_started": 0}
    ex = R.read_jsonl(root / "runs/rs/ex.jsonl")
    assert [r["ex_match"] for r in ex] == [1, 1, 1, 1, 0, 0]
    assert s["evaluator"]["table"]["count"] == {"simple": 2, "moderate": 2, "challenging": 2, "total": 6}
    # 중간 파일은 gold/_work/<run_id>/에만 있다
    assert sorted(p.name for p in (root / "gold/_work/rs").iterdir()) == ["diff.jsonl", "ex.raw.jsonl"]
    run_text = "".join(p.read_text(encoding="utf-8") for p in (root / "runs/rs").rglob("*") if p.is_file())
    for secret in (M.GOLD_SQL[4], M.GOLD_SQL[5], *M.EVIDENCE):
        assert secret not in run_text
    assert bird.score_line(s).startswith("score: BIRD Mini-Dev / SQLite / ab12cd34 / 6문제 / EX / evidence not_used")
    assert "→ EX 4/6 (66.67%) · 채점기 대조 True · DB 불변 True" in bird.score_line(s)


def test_wal_side_files_removed(tmp_path, shop_db):
    root = M.make_mini_bird(tmp_path / "w", shop_db, wal=True)
    config = load_config(root, State())
    db = root / "data/mini/MINIDEV/dev_databases/shop/shop.sqlite"
    path = M.predictions_jsonl(tmp_path / "p.jsonl", PRED)
    bird.import_predictions(config, "mini", path, "w1")
    s = bird.score(config, "w1", num_cpus=2)
    assert s["data_unchanged"] is True
    for suffix in ("-wal", "-shm", "-journal"):
        assert not db.with_name(db.name + suffix).exists()


def test_sample_missing_level_skips_official(mini, tmp_path):
    root, _ = mini
    s = rescore(mini, tmp_path, PRED[:2], "small")
    assert s["cross_check"] is None and s["cross_check_note"] == bird.SKIP_NOTE
    assert s["evaluator"]["skipped"] is True and s["ex_total"]["correct"] == 2 and s["sample"] is True
    assert (root / "gold/_work/small/gold.subset.sql").is_file()
    assert not (root / "runs/small/evaluator-stdout.txt").exists()
    assert "소표본·점수 아님" in s["label"]


def test_parse_table_and_percent():
    text = (
        "start calculate EX\n"
        "                     simple               moderate             challenging          total               \n"
        "count                148                  250                  102                  500                 \n"
        "EX                   66.89                52.80                31.37                52.60               \n"
    )
    t = bird.parse_table(text)
    assert t["count"] == {"simple": 148, "moderate": 250, "challenging": 102, "total": 500}
    assert t["EX"] == {"simple": "66.89", "moderate": "52.80", "challenging": "31.37", "total": "52.60"}
    assert [bird.official_percent(c, n) for c, n in ((99, 148), (132, 250), (32, 102), (263, 500))] == [
        "66.89",
        "52.80",
        "31.37",
        "52.60",
    ]
    assert bird.official_percent(0, 0) is None and bird.parse_table("nothing") is None
    ours = {
        "total": {"correct": 263, "total": 500},
        "by_difficulty": {
            "simple": {"correct": 99, "total": 148},
            "moderate": {"correct": 132, "total": 250},
            "challenging": {"correct": 32, "total": 102},
        },
    }
    ok, detail = bird.cross_check(ours, t)
    assert ok and detail["total"]["evaluator_correct_derived"] == 263
    ours["by_difficulty"]["simple"]["correct"] = 98
    assert bird.cross_check(ours, t)[0] is False


BASE = {
    "dataset": "bird-minidev",
    "dataset_kind": "bird-minidev",
    "archive_sha256": "cc48ba16838204e4",
    "kind": "generate",
    "git": {"head": "647f7f0aaaaaaaa", "dirty": False},
    "llm": {"model": "gpt-5.6-terra", "reasoning_effort": "medium", "backend": "codex-cli"},
}


def test_labels():
    assert bird.label(BASE, 500, False) == (
        "BIRD Mini-Dev / SQLite / cc48ba16 / 500문제 / EX / evidence not_used / 로컬 측정 / "
        "gpt-5.6-terra·추론 medium·codex-cli / 1회 실행 / 구현 Python 647f7f0"
    )
    dirty = {**BASE, "git": {"head": "647f7f0aaaa", "dirty": True}}
    assert bird.label(dirty, 500, False).endswith("구현 Python 647f7f0 / (미커밋 코드)")
    sample = bird.label(BASE, 20, True)
    assert "/ 20문제 /" in sample and sample.endswith("구현 Python 647f7f0 / 소표본·점수 아님")
    fake = {**BASE, "llm": {"model": "fake-model", "reasoning_effort": "none", "backend": "fake"}}
    assert bird.label(fake, 5, True).endswith(" / 소표본·점수 아님 / 가짜 백엔드·점수 아님")
    rs = {**BASE, "kind": "rescore", "llm": None, "source": {"sha256": "a4f77df8ffff"}}
    assert bird.label(rs, 500, False) == (
        "BIRD Mini-Dev / SQLite / cc48ba16 / 500문제 / EX / evidence not_used / 로컬 측정 / "
        "재채점(예측 파일 a4f77df8) / 채점 Python 647f7f0"
    )
    nogit = {**BASE, "git": {"head": None}}
    assert bird.label(nogit, 500, False).endswith("구현 Python 커밋 없음")
    other = {**BASE, "dataset": "shop", "dataset_kind": "single"}
    assert bird.label(other, 3, False).startswith("shop / SQLite / 3문제 / EX")


def test_vendor_hash_mismatch_stops(mini, tmp_path, monkeypatch):
    fake_vendor = tmp_path / "vendor"
    shutil.copytree(bird.VENDOR, fake_vendor)
    (fake_vendor / "evaluation_ex.py").write_text("# 고친 사본\n")
    monkeypatch.setattr(bird, "VENDOR", fake_vendor)
    with pytest.raises(bird.ScoreError, match="해시가 고정값과 다릅니다"):
        rescore(mini, tmp_path)


def test_public_and_gold_misaligned(mini, tmp_path):
    root, _ = mini
    gold = root / "gold/mini"
    items = json.loads((gold / "answers.json").read_text())
    items[3]["question"] = "Something else?"
    (gold / "answers.json").write_text(json.dumps(items))
    M.write_gold_toml(gold)
    with pytest.raises(bird.ScoreError, match="위치 3"):
        rescore(mini, tmp_path)


def test_import_predictions_json_form(mini, tmp_path):
    _root, config = mini
    data = {str(i): f"{s or R.FAILURE_SQL}{R.SEPARATOR}shop" for i, s in enumerate(PRED)}
    p = tmp_path / "pred.json"
    p.write_text(json.dumps(data))
    run = bird.import_predictions(config, "mini", p, "js")
    assert (run / "predictions.json").read_bytes() == p.read_bytes()
    meta = R.read_json(run / "run.json")
    assert meta["kind"] == "rescore" and meta["source"]["sha256"] == R.sha256_file(p)
    recs = R.read_jsonl(run / "records.jsonl")
    assert [r["status"] for r in recs] == ["generated"] * 5 + ["no_sql"]
    s = bird.score(config, "js", num_cpus=2)
    assert s["ex_total"]["correct"] == 4
    with pytest.raises(bird.ScoreError, match="이미 있는"):
        bird.import_predictions(config, "mini", p, "js")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"0": f"SELECT 1{R.SEPARATOR}otherdb"}))
    with pytest.raises(bird.ScoreError, match="db_id"):
        bird.import_predictions(config, "mini", bad, "bad")


def test_no_gold(tmp_path, shop_db):
    root = M.make_mini_bird(tmp_path / "n", shop_db)
    shutil.rmtree(root / "gold")
    config = load_config(root, State())
    path = M.predictions_jsonl(tmp_path / "p.jsonl", PRED)
    bird.import_predictions(config, "mini", path, "ng")
    with pytest.raises(G.NoGold):
        bird.score(config, "ng")
