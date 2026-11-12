import json

import pytest

from fixtures import mini_bird as M
from t2s.dataset.config import load_config
from t2s.dataset.state import State
from t2s.eval import gold as G
from t2s.eval import report as REP
from t2s.llm import select
from t2s.runner import records as R

# TS harness/lib/sentence.ts signTestP로 계산한 값(node로 같은 식을 돌려 맞춤)
SIGN = {
    (0, 0): 1.0,
    (5, 5): 1.0,
    (20, 3): 0.00048828125,
    (14, 0): 0.0001220703125,
    (60, 46): 0.2064982073597098,
    (20, 22): 0.8776143287523154,
    (30, 10): 0.0022214337732293643,
}


def test_sign_test_matches_ts():
    for (a, b), p in SIGN.items():
        assert REP.sign_test_p(a, b) == pytest.approx(p, rel=1e-12), (a, b)


def sentences(a, b, **kw):
    base = {"positions": 500, "sample": False, "is_reference": True, "backend_differs": False}
    return REP.comparison_sentences(newly_correct=a, regressed=b, **{**base, **kw})


def test_sentence_branches():
    assert sentences(13, 0) == ["새로 맞음 13, 새로 틀림 0 (공통 500위치)", "차이 없음(변동 범위, |차이|=13, p=0.000)"]
    assert sentences(60, 46)[1] == "차이 없음(변동 범위, |차이|=14, p=0.206)"
    assert sentences(14, 0)[1] == "변동 기준보다 크다(|차이|=14, p=0.000). 1회 실행이라 낫다·못하다로 읽지 않는다"
    assert sentences(14, 0, sample=True) == [
        "새로 맞음 14, 새로 틀림 0 (공통 500위치)",
        "소표본·점수 아님 — 비교 문장 생략",
    ]
    assert sentences(1, 2, is_reference=False)[-1] == "모델이 달라 재현 비교 아님"
    assert sentences(1, 2, backend_differs=True)[-1] == "백엔드가 달라 우열을 말하지 않는다"


def test_compare():
    a = {
        0: {"ex_match": 1, "db_id": "x", "sql_sha256": "s0"},
        1: {"ex_match": 0, "sql_sha256": "s1"},
        2: {"ex_match": 1},
    }
    b = {
        0: {"ex_match": 0, "db_id": "x", "sql_sha256": "s0"},
        1: {"ex_match": 1, "sql_sha256": "t1"},
        3: {"ex_match": 1},
    }
    c = REP.compare(a, b)
    assert c["positions"] == 2 and c["newly_correct"] == [0] and c["regressed"] == [1] and c["sql_changed"] == 1
    with pytest.raises(REP.ReportError, match="db_id"):
        REP.compare({0: {"ex_match": 1, "db_id": "x"}}, {0: {"ex_match": 1, "db_id": "y"}})
    assert REP.compare({0: {"ex_match": 1}}, {0: {"ex_match": 1}})["sql_changed"] is None


SQLS = ["S0", "S1", "S2", "S3", "S4", "S5"]
LLM = {
    "backend": "codex-cli",
    "model": "gpt-5.6-terra",
    "reasoning_effort": "medium",
    "timeout_seconds": 150,
    "item_timeout_seconds": 1530,
    "profile": "p3-ref",
    "model_source": "conversation",
    "effort_source": "conversation",
    "session_evidence": {},
}


def scored_run(root, run_id, ex, *, refine=(0,) * 6, llm=LLM, score=True, sample=False, versions_end=None):
    run = root / "runs" / run_id
    start = {"python": "3.13.7", "sqlite": "3.50.4", "codex": "codex-cli 0.167.0", "claude": "2.1.322"}
    R.write_json_atomic(
        run / "run.json",
        {
            "run_id": run_id,
            "kind": "generate",
            "dataset": "mini",
            "dataset_kind": "bird-minidev",
            "archive_sha256": M.ARCHIVE,
            "count": len(ex),
            "sample": sample,
            "llm": llm,
            "git": {"head": "abcdef1234", "dirty": False},
            "versions_start": start,
            "started_at": "2026-11-13T00:00:00Z",
        },
    )
    recs = [
        {
            "position": i,
            "question_id": 900 + i,
            "db_id": "shop",
            "difficulty": M.DIFFS[i],
            "status": "generated",
            "sql": SQLS[i],
            "refine_calls": refine[i],
            "final_exec": {"status": "rows"},
            "llm": {"call_count": 0, "tool_calls": 0},
        }
        for i in range(len(ex))
    ]
    R.write_jsonl(run / "records.jsonl", recs)
    R.write_json_atomic(
        run / "manifest.json",
        {"versions_end": versions_end or start, "llm_totals": {"tool_calls": 0}, "database_unchanged": True},
    )
    R.write_json_atomic(run / "control.json", {"state": "finished"})
    if not score:
        return run
    rows = [
        {**{k: r[k] for k in ("position", "question_id", "db_id", "difficulty", "status")}, "ex_match": ex[i]}
        for i, r in enumerate(recs)
    ]
    R.write_jsonl(run / "ex.jsonl", rows)
    bd = {}
    for r in rows:
        b = bd.setdefault(r["difficulty"], {"correct": 0, "total": 0})
        b["total"] += 1
        b["correct"] += r["ex_match"]
    R.write_json_atomic(
        run / "score.json",
        {
            "label": f"라벨 {run_id}",
            "sample": sample,
            "ex_total": {"correct": sum(ex), "denominator": len(ex)},
            "by_difficulty": bd,
            "by_db": {"shop": {"correct": sum(ex), "total": len(ex)}},
            "status_counts": {"generated": len(ex), "no_sql": 0, "exception": 0, "not_started": 0},
            "cross_check": True,
            "data_unchanged": True,
            "python": "3.13.7",
            "sqlite": "3.50.4",
        },
    )
    R.write_json_atomic(
        run / "leak-audit.json",
        {
            "prompts": 12,
            "gold_sql_hits": [],
            "evidence_hits": [],
            "other_gold_hits": [],
            "model_made": [],
            "prompts_missing": [],
            "note": None,
        },
    )
    return run


@pytest.fixture
def mini(tmp_path, shop_db):
    root = M.make_mini_bird(tmp_path / "r", shop_db)
    return root, load_config(root, State())


def test_three_kinds_of_against(mini):
    root, config = mini
    scored_run(root, "base", [1, 1, 0, 0, 1, 0])
    scored_run(root, "mine", [1, 0, 1, 0, 1, 1], refine=(0, 2, 1, 0, 0, 0))
    lines, code = REP.report(config, "mine", "base")
    assert code == 0 and lines[0] == "라벨 mine" and len(lines) <= 12
    assert lines[1] == "EX 4/6 (66.67%) · simple 1/2 · moderate 1/2 · challenging 2/2"
    assert lines[3] == "기준 run base (EX 3/6) · SQL 바뀜 0"
    assert lines[4] == "새로 맞음 2, 새로 틀림 1 (공통 6위치)"
    assert lines[5].startswith("차이 없음(변동 범위, |차이|=1")
    assert "고친 문항 2개 가운데 새로 맞음 1, 새로 틀림 1" in lines
    assert lines[-3].startswith("누설 검사: 프롬프트 12개, 정답 SQL 0")
    assert lines[-2] == "도구 호출 0 · Python 3.13.7 · SQLite 3.50.4 · CLI 판 시작·끝 같음"
    assert lines[-1] == "runs/mine"
    # ex.jsonl 경로
    lines, _ = REP.report(config, "mine", str(root / "runs/base/ex.jsonl"))
    assert lines[3].endswith("(ex.jsonl, EX 3/6)") and lines[4] == "새로 맞음 2, 새로 틀림 1 (공통 6위치)"
    # 기준선 .json
    base_rows = [
        {
            "position": i,
            "question_id": 900 + i,
            "db_id": "shop",
            "ex_match": e,
            "sql_sha256": REP.sql_digest(SQLS[i] if i else "X"),
        }
        for i, e in enumerate([0, 0, 1, 0, 1, 1])
    ]
    (root / "baselines").mkdir()
    (root / "baselines/b.json").write_text(
        json.dumps(
            {
                "pack": "pure-fix",
                "run_id": "r0",
                "summary": {"ex_total": {"correct": 3, "denominator": 6}},
                "rows": base_rows,
            }
        )
    )
    lines, _ = REP.report(config, "mine", "baselines/b.json")
    assert lines[3] == "기준 baselines/b.json (TS pure-fix r0, EX 3/6) · SQL 바뀜 1"
    assert lines[4] == "새로 맞음 1, 새로 틀림 0 (공통 6위치)"
    data = json.loads((root / "runs/mine/report.json").read_text())
    assert data["comparison"]["newly_correct"] == [0] and data["lines"] == lines
    md = (root / "runs/mine/report.md").read_text()
    assert "## 새로 맞은 위치" in md and "S0" not in md


def test_default_baseline_missing_and_model_sentences(mini):
    root, config = mini
    other = {
        **LLM,
        "backend": "claude-code",
        "model": "claude-opus-5-5",
        "profile": None,
        "model_source": "session-self",
    }
    scored_run(root, "base", [1, 1, 0, 0, 1, 0])
    scored_run(root, "c", [1, 1, 0, 0, 1, 0], llm=other, versions_end={"codex": "x", "claude": "y"})
    lines, _ = REP.report(config, "c", None)
    assert "비교 대상 없음" in lines
    lines, _ = REP.report(config, "c", "base")
    assert "모델이 달라 재현 비교 아님" in lines and "백엔드가 달라 우열을 말하지 않는다" in lines
    assert any(x.startswith("주의: CLI 판이 측정 중 바뀌었습니다") for x in lines)


def test_sample_sentence(mini):
    root, config = mini
    scored_run(root, "base", [1, 1, 0, 0, 1, 0])
    scored_run(root, "s", [1, 1, 0, 0, 1, 1], sample=True)
    lines, _ = REP.report(config, "s", "base")
    assert "소표본·점수 아님 — 비교 문장 생략" in lines


def test_not_ready_and_no_gold(mini):
    root, config = mini
    scored_run(root, "u", [1, 0], score=False)
    with pytest.raises(REP.NotReady, match="아직 채점 전입니다: t2s score --run u"):
        REP.report(config, "u", None)
    (root / "runs/u/manifest.json").unlink()
    with pytest.raises(REP.NotReady, match="아직 생성 중입니다"):
        REP.report(config, "u", None)
    scored_run(root, "g", [1, 0, 1], score=False, refine=(1, 0, 0))
    import shutil

    shutil.rmtree(root / "gold")
    with pytest.raises(G.NoGold):
        REP.report(config, "g", None)
    lines = REP.no_gold_summary(config, "g")
    assert len(lines) == 6
    assert lines[0] == "mini · 정답 없음: 채점 생략"
    assert lines[2] == "문항 3 · 상태 generated 3, no_sql 0, exception 0, not_started 0"
    assert lines[3] == "실행 rows 3 · empty 0 · error 0 · timeout 0 · 없음 0"
    assert lines[4] == "고치기 한 문항 1 · 호출 0번 · 도구 호출 0 · DB 불변 True"
    assert lines[5] == "SQL과 실행 상태: runs/g/records.jsonl"
    assert (root / "runs/g/report.json").is_file()


def test_model_line_matches_select(tmp_path):
    profiles = {"p3-ref": {"backend": "codex-cli", "model": "gpt-5.6-terra", "reasoning_effort": "medium"}}
    cases = [
        select.select_model(profile="p3-ref", profiles=profiles, env={}, cwd=tmp_path, home=tmp_path),
        select.select_model(model="opus", profiles={}, env={}, cwd=tmp_path, home=tmp_path),
        select.select_model(
            backend="claude-code",
            model="claude-opus-5-5",
            model_source="session-self",
            profiles={},
            env={},
            cwd=tmp_path,
            home=tmp_path,
        ),
        select.select_model(backend="fake", profiles={}, env={}, cwd=tmp_path, home=tmp_path),
    ]
    for llm in cases:
        assert REP._model_line(llm.to_json()) == select.first_line(llm)
