import hashlib
import json

import pytest

from t2s.eval.routing import sources as S

QS = [
    {"position": 0, "question_id": 10, "db_id": "d1", "difficulty": "simple"},
    {"position": 1, "question_id": 11, "db_id": "d2", "difficulty": "moderate"},
]
HASHES = {"d1": "h1", "d2": "h2"}


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def make_pure(root, name="pure-run", evidence="not_used", usage_none=False, qid1=11):
    run = root / name
    dump(
        run / "run.json",
        {"model": "m", "reasoning_effort": "medium", "evidence": evidence, "count": 2, "snapshot": "ab" * 32},
    )
    dump(
        run / "ex.json",
        [{"position": 0, "official_match": 1, "exec": "ok"}, {"position": 1, "official_match": 0, "exec": "error"}],
    )
    dump(run / "score.json", {"correct": 1})
    gen = {k: {"sha256": v} for k, v in HASHES.items()}
    dump(run / "db_generation.json", {"before": {**gen, "other": {"sha256": "x"}}, "after": gen})
    for pos, qid, db in ((0, 10, "d1"), (1, qid1, "d2")):
        d = run / "items" / f"{pos:04d}"
        dump(d / "started.json", {"position": pos, "id": qid, "db_id": db})
        usage = (
            None
            if (usage_none and pos == 1)
            else {"input_tokens": 100 + pos, "cached_input_tokens": 50, "output_tokens": 7}
        )
        dump(
            d / "result.json",
            {
                "status": "ok",
                "sql": "SELECT 1",
                "elapsed_seconds": 1.5,
                "usage": usage,
                "command": ["sandbox", "codex", "exec"],
            },
        )
    return name


def make_p3(root, name="p3-run", harness=True, null_tokens=False):
    run = root / name
    dump(
        run / "run.json",
        {
            "evidence": {"mode": "not_used"},
            "count": 2,
            "llm": {"backend": "codex-cli", "model": "m", "reasoning_effort": "medium"},
            "git": {"head": "abcdef1234", "src_tree": "t" * 40, "dirty": False},
            "databases": {k: {"sha256": v} for k, v in HASHES.items()},
            "questions": {"sha256": "q"},
        },
    )
    recs = []
    for pos, qid, db in ((0, 10, "d1"), (1, 11, "d2")):
        lm = {
            "input_tokens": None if (null_tokens and pos == 1) else 200,
            "output_tokens": None if (null_tokens and pos == 1) else 20,
        }
        recs.append(
            {
                "position": pos,
                "question_id": qid,
                "db_id": db,
                "difficulty": "simple",
                "status": "generated" if pos == 0 else "exception",
                "sql": "SELECT 1",
                "final_exec": {"status": "rows"} if pos == 0 else None,
                "llm": lm,
                "elapsed_ms": 1234,
            }
        )
        meta = {"usage": {"input_tokens": 30, "output_tokens": 3}, "cli_version": "codex-cli 9"}
        if harness:
            meta["harness"] = ["minimal", "task"]
        dump(run / "items" / f"{pos:04d}" / "calls" / "1" / "llm-meta.json", meta)
        dump(run / "items" / f"{pos:04d}" / "calls" / "2" / "llm-meta.json", {"usage": None})
    text = "".join(json.dumps(r) + "\n" for r in recs)
    (run / "records.jsonl").write_text(text, encoding="utf-8")
    (run / "ex.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "position": r["position"],
                    "question_id": r["question_id"],
                    "db_id": r["db_id"],
                    "ex_match": 1 - r["position"],
                }
            )
            + "\n"
            for r in recs
        ),
        encoding="utf-8",
    )
    totals = (
        {"input_tokens": None, "output_tokens": None} if null_tokens else {"input_tokens": 400, "output_tokens": 40}
    )
    dump(
        run / "manifest.json", {"records": {"sha256": hashlib.sha256(text.encode()).hexdigest()}, "llm_totals": totals}
    )
    return name


def spec(name, kind):
    return {"name": "x", "model": "m", "kind": kind, "path": name}


def test_pure_and_p3_same_shape(tmp_path):
    a = S.load(tmp_path, spec(make_pure(tmp_path), "pure"), QS, HASHES)
    b = S.load(tmp_path, spec(make_p3(tmp_path), "p3"), QS, HASHES)
    keys = {"position", "question_id", "db_id", "difficulty", "sql", "ex", "ms", "tok_in", "tok_out", "tok"}
    assert keys <= set(a["items"][0]) and keys <= set(b["items"][0])
    assert a["items"][0]["ms"] == 1500.0 and a["items"][0]["tok"] == 107
    assert b["items"][0]["ms"] == 1234.0 and b["items"][0]["tok"] == 220
    assert b["items"][1]["sql"] is None  # 생성 예외는 SQL 없음
    assert a["flags"] == [] and b["flags"] == []
    assert a["info"]["commit"].startswith("스냅샷 abab")


def test_p3_null_tokens_use_call_sum(tmp_path):
    b = S.load(tmp_path, spec(make_p3(tmp_path, null_tokens=True), "p3"), QS, HASHES)
    assert b["items"][1]["tok_in"] == 30 and b["items"][1]["token_partial"]
    assert "partial" in b["flags"]


def test_pure_missing_usage(tmp_path):
    a = S.load(tmp_path, spec(make_pure(tmp_path, usage_none=True), "pure"), QS, HASHES)
    assert a["items"][1]["tok"] == 0 and a["items"][1]["token_missing"]
    assert a["flags"] == ["missing_item"]


def test_no_harness_is_inflated(tmp_path):
    b = S.load(tmp_path, spec(make_p3(tmp_path, harness=False), "p3"), QS, HASHES)
    assert "inflated" in b["flags"]


def test_mismatches_stop(tmp_path):
    with pytest.raises(S.SourceError):
        S.load(tmp_path, spec(make_pure(tmp_path, "bad-id", qid1=99), "pure"), QS, HASHES)
    with pytest.raises(S.SourceError):
        S.load(tmp_path, spec(make_pure(tmp_path, "used", evidence="used"), "pure"), QS, HASHES)
    with pytest.raises(S.SourceError):
        S.load(tmp_path, spec(make_pure(tmp_path, "short"), "pure"), QS[:1], HASHES)
    with pytest.raises(S.SourceError):
        S.load(tmp_path, spec(make_p3(tmp_path), "p3"), QS, {"d1": "h1", "d2": "other"})


def test_run_table_is_relative():
    for s in S.RUNS:
        assert not s["path"].startswith("/")
    assert [s["name"] for s in S.RUNS].count(S.BASELINE) == 1
