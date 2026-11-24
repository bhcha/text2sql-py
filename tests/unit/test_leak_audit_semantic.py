"""semantic used run의 라벨·채점 기록·보고·누설 검사·경로 판정(작은 가짜 스터디 평가셋, 가짜 백엔드).

라벨은 evidence 칸 바로 뒤에 ` / semantic used(<정의 sha256 앞 8자>)`, 시험용 정의면 ` / 시험용 정의·점수 아님`.
not_used·옛 run의 라벨은 그 전과 글자로 같다. 조건·정의가 다른 run은 견주지 않는다.
"""

import hashlib
import json
import shutil

import pytest

from conftest import FIXTURES
from t2s.cli import data_cmds, run_cmds
from t2s.cli.main import main
from t2s.eval import bird, study
from t2s.eval import gold as G
from t2s.prep import embed
from t2s.runner import records as R

pytest.importorskip("func_timeout")

FIXTURE = FIXTURES / "semantic" / "shop.semantic.json"


@pytest.fixture
def sroot(tmp_path, monkeypatch, shop_db, capsys):
    from fixtures import mini_study as MS

    monkeypatch.setattr(data_cmds, "install_guard", lambda root, **kw: None)
    monkeypatch.setattr(run_cmds, "install_guard", lambda root, **kw: None)
    r = MS.make_mini_study(tmp_path / "srepo", shop_db)
    model = r / "data/models/Xenova/all-mpnet-base-v2"
    fake = {}
    for f in embed.MODEL_FILES:
        (model / f).parent.mkdir(parents=True, exist_ok=True)
        (model / f).write_bytes(f"fake {f}".encode())
        fake[f] = hashlib.sha256(f"fake {f}".encode()).hexdigest()
    monkeypatch.setattr(embed, "MODEL_SHA256", fake)
    monkeypatch.setattr(embed, "load_embedder", lambda d: embed.HashEmbedder())
    monkeypatch.setenv("T2S_ROOT", str(r))
    assert main(["prepare"]) == 0
    capsys.readouterr()
    return r, MS


def cli(capsys, *argv):
    code = main(list(argv))
    o = capsys.readouterr()
    return code, o.out.splitlines(), o.err.splitlines()


def _import(root, capsys, doc=None):
    inbox = root / "var/semantic-inbox/olist.json"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    if doc is None:
        shutil.copy(FIXTURE, inbox)
    else:
        inbox.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    assert main(["semantic-import", "olist", "--yes"]) == 0
    capsys.readouterr()
    return json.loads((root / "var/state.json").read_text())["semantic"]["olist"]


def _run(root, MS, capsys, run_id, *extra):
    argv = ["run", "--foreground", "--backend", "fake", "--questions", str(root / MS.INPUTS), "--run-id", run_id]
    code, lines, err = cli(capsys, *argv, *extra)
    assert code == 0, (lines, err)
    return root / "runs" / run_id


def _prompts(run):
    return sorted(run.glob("items/*/calls/*/prompt.txt"))


def test_used_run_label_score_report_leak(sroot, capsys):
    root, MS = sroot
    st = _import(root, capsys)
    run = _run(root, MS, capsys, "su", "--semantic", "used")
    score = R.read_json(run / R.SCORE_FILE)
    sha8 = st["sha256"][:8]
    assert f" / EX(스터디 결과 비교) / evidence not_used / semantic used({sha8}) / 로컬 측정 / " in score["label"]
    assert score["label"].endswith(" / 가짜 백엔드·점수 아님 / 시험용 정의·점수 아님")
    assert score["semantic"] == {
        "mode": "used",
        "source_sha256": st["sha256"],
        "model": "shop-fixture",
        "version_id": "fixture-shop-v1",
        "kind": "fixture",
        "accepted_at": st["accepted_at"],
        "checked": True,
    }
    assert score["leak"]["total"] == 0 and score["leak"]["semantic_missing"] == 0
    code, leak, _ = cli(capsys, "leak-audit", "--run", "su")
    assert code == 0 and "정답 SQL 0, 문항 조건·채점 메모 0, 지표 정의 제공(used)·빠짐 0, 다른 문항 정답 0" in leak[0]
    assert MS.MARK not in leak[0] and MS.COND_MARK not in leak[0]
    code, lines, _ = cli(capsys, "report", "--run", "su")
    assert code == 0
    assert lines[0] == score["label"]
    assert "비교 대상 없음(기준은 semantic not_used, 이번은 used라 견주지 않는다)" in lines
    rep = R.read_json(run / R.REPORT_FILE)
    assert rep["semantic"] == {"mode": "used", "source_sha256": st["sha256"]} and rep["against_semantic"] is None
    assert rep["comparison"] is None


def test_not_used_label_and_records_unchanged(sroot, capsys):
    root, MS = sroot
    _import(root, capsys)  # 받아들인 정의가 있어도 말이 없으면 쓰지 않는다
    run = _run(root, MS, capsys, "sn")
    score = R.read_json(run / R.SCORE_FILE)
    assert " / evidence not_used / 로컬 측정 / " in score["label"] and "semantic" not in score["label"]
    assert "시험용 정의" not in score["label"] and "semantic" not in score
    assert "semantic_missing" not in score["leak"] and "지표 정의" not in score["leak"]["summary"]
    rep = R.read_json(run / R.REPORT_FILE)
    assert "semantic" not in rep and "against_semantic" not in rep
    assert all(G.semantic_label(R.read_json(run / R.RUN_FILE)) == "" for _ in (0,))


def _meta(**kw):
    base = {
        "dataset": "olist",
        "dataset_kind": "single",
        "git": {"head": "a" * 40, "dirty": False},
        "llm": {"model": "m", "reasoning_effort": "medium", "backend": "codex-cli"},
        "evidence": {"mode": "not_used"},
    }
    return {**base, **kw}


def test_label_functions_old_and_used():
    old = _meta()
    pure = _meta(semantic={"mode": "not_used"})
    assert bird.label(old, 49, False) == bird.label(pure, 49, False)
    assert study.label(old, 49, False, "Olist 스터디 평가셋 v0.3", "1" * 64) == (
        "Olist 스터디 평가셋 v0.3 / SQLite / 11111111 / 49문제 / EX(스터디 결과 비교) / evidence not_used / 로컬 측정"
        " / m·추론 medium·codex-cli / 1회 실행 / 구현 Python aaaaaaa"
    )
    used = _meta(semantic={"mode": "used", "source_sha256": "3fa1c0de" + "0" * 56, "source": {"kind": "graph_mcp"}})
    assert study.label(used, 49, False, "Olist 스터디 평가셋 v0.3", "1" * 64) == (
        "Olist 스터디 평가셋 v0.3 / SQLite / 11111111 / 49문제 / EX(스터디 결과 비교) / evidence not_used"
        " / semantic used(3fa1c0de) / 로컬 측정 / m·추론 medium·codex-cli / 1회 실행 / 구현 Python aaaaaaa"
    )
    assert " / evidence not_used / semantic used(3fa1c0de) / 로컬 측정 / " in bird.label(used, 5, False)
    fixture = _meta(semantic={"mode": "used", "source_sha256": "f" * 64, "source": {"kind": "fixture"}})
    assert bird.label(fixture, 5, False).endswith(" / 시험용 정의·점수 아님")
    assert G.semantic_mode({"kind": "rescore", "semantic": {"mode": "used"}}) is None
    assert G.semantic_label({"kind": "rescore", "semantic": {"mode": "used"}}) == ""


def test_semantic_missing_counted(sroot, capsys):
    root, MS = sroot
    _import(root, capsys)
    run = _run(root, MS, capsys, "sm", "--semantic", "used")
    text = json.loads((run / R.SEMANTIC_FILE).read_text())["text"]
    for p in sorted((run / "items/0002/calls").glob("*/prompt.txt")):
        p.write_text(p.read_text(encoding="utf-8").replace(text, ""), encoding="utf-8")
    code, leak, _ = cli(capsys, "leak-audit", "--run", "sm")
    assert code != 0 and "지표 정의 제공(used)·빠짐 1" in leak[0]
    assert R.read_json(run / R.LEAK_FILE)["semantic_missing"] == [2]


def test_definition_overlapping_conditions_is_a_leak(sroot, capsys):
    root, MS = sroot
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc["source"]["kind"] = "graph_mcp"
    doc["metrics"][0]["description"] = MS.CONDITIONS[1][0]  # 평가셋을 보고 쓴 정의(문항 조건과 글자 그대로 겹침)
    _import(root, capsys, doc)
    argv = ["run", "--foreground", "--backend", "fake", "--questions", str(root / MS.INPUTS), "--semantic", "used"]
    main([*argv, "--run-id", "so"])
    capsys.readouterr()
    leak = R.read_json(root / "runs/so" / R.LEAK_FILE)
    assert 1 in leak["evidence_hits"] and leak["semantic_missing"] == []
    code, lines, _ = cli(capsys, "leak-audit", "--run", "so")
    assert code != 0 and MS.COND_MARK not in "\n".join(lines)


def test_score_refuses_changed_copy(sroot, capsys):
    root, MS = sroot
    _import(root, capsys)
    run = _run(root, MS, capsys, "sc", "--semantic", "used", "--no-score")
    copy = run / R.SEMANTIC_FILE
    copy.write_bytes(copy.read_bytes().replace(b"paid orders", b"paid  orders"))
    code, _, err = cli(capsys, "score", "--run", "sc")
    assert code == 1 and any("semantic.json" in e for e in err)
    assert not (run / R.SCORE_FILE).exists()


def test_report_against_refuses_other_condition_or_definition(sroot, capsys):
    root, MS = sroot
    _import(root, capsys)
    _run(root, MS, capsys, "a-used", "--semantic", "used")
    _run(root, MS, capsys, "a-pure")
    code, lines, _ = cli(capsys, "report", "--run", "a-used", "--against", "a-pure")
    assert code == 1 and "semantic 조건이 달라 견주지 않는다(이번 used, 기준 not_used)" in lines
    assert not any(x.startswith("새로 맞음") for x in lines)
    code, lines, _ = cli(capsys, "report", "--run", "a-pure", "--against", "a-used")
    assert code == 1 and "semantic 조건이 달라 견주지 않는다(이번 not_used, 기준 used)" in lines
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc["source"]["version_id"] = "fixture-shop-v2"
    st2 = _import(root, capsys, doc)
    _run(root, MS, capsys, "b-used", "--semantic", "used")
    code, lines, _ = cli(capsys, "report", "--run", "b-used", "--against", "a-used")
    first = R.read_json(root / "runs/a-used" / R.RUN_FILE)["semantic"]["source_sha256"]
    assert code == 1 and f"지표 정의가 달라 견주지 않는다(이번 {st2['sha256'][:8]}, 기준 {first[:8]})" in lines
    rep = R.read_json(root / "runs/b-used" / R.REPORT_FILE)
    assert rep["against_semantic"] == {"mode": "used", "source_sha256": first} and rep["comparison"] is None
    # 같은 정의로 돈 두 used run은 견준다
    _run(root, MS, capsys, "c-used", "--semantic", "used")
    code, lines, _ = cli(capsys, "report", "--run", "c-used", "--against", "b-used")
    assert code == 0 and any(x.startswith("새로 맞음") for x in lines)


def test_route_judge_refuses_semantic_used(sroot, capsys):
    root, MS = sroot
    _import(root, capsys)
    _run(root, MS, capsys, "h-pure")
    _run(root, MS, capsys, "c-used", "--semantic", "used")
    code, lines, _ = cli(capsys, "route-judge", "--harness", "h-pure", "--candidate", "c-used")
    assert code == 1
    assert lines[-1] == "semantic used run으로는 경로 판정을 하지 않는다(하네스 not_used, 후보 used)"
    assert not R.judgment_path(root / "runs", "h-pure", "c-used").exists()


def test_definition_with_own_gold_sql_is_a_leak(sroot, capsys):
    """정의 글 안에 그 문항의 정답 SQL이 있으면, 모델이 같은 SQL을 썼더라도(rounds old_sql) 모델 생성으로 빼지 않는다."""
    root, MS = sroot
    doc = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc["source"]["kind"] = "graph_mcp"
    doc["metrics"][2]["sql"] = MS.GOLD_SQL[0]  # 평가셋 정답을 그대로 담은 정의
    _import(root, capsys, doc)
    run = _run(root, MS, capsys, "sg", "--semantic", "used", "--no-score")
    trace_path = run / "items/0000" / R.TRACE_FILE
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    trace["rounds"] = [{"old_sql": MS.GOLD_SQL[0]}]
    trace_path.write_text(json.dumps(trace), encoding="utf-8")
    code, lines, err = cli(capsys, "leak-audit", "--run", "sg")
    leak = R.read_json(run / R.LEAK_FILE)
    assert 0 in leak["gold_sql_hits"] and 0 not in leak["model_made"]
    assert code != 0
    assert MS.MARK not in "\n".join(lines + err)


def test_report_against_run_folder_ex_jsonl_refuses(sroot, capsys):
    """--against에 run 폴더의 ex.jsonl을 주어도 곁의 run.json으로 semantic 조건을 대조한다."""
    root, MS = sroot
    _import(root, capsys)
    _run(root, MS, capsys, "x-used", "--semantic", "used")
    _run(root, MS, capsys, "x-pure")
    for mine, theirs in (("x-pure", "x-used"), ("x-used", "x-pure")):
        code, lines, _ = cli(capsys, "report", "--run", mine, "--against", f"runs/{theirs}/ex.jsonl")
        assert code == 1, (mine, lines)
        assert any(x.startswith("semantic 조건이 달라 견주지 않는다(") for x in lines)
        assert not any(x.startswith("새로 맞음") for x in lines)
