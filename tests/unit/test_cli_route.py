"""route-start → (두 run을 함께) → route-status → route-judge → route-apply 흐름. 가짜 백엔드와 작은 가짜 BIRD만 쓴다."""

import hashlib
import json
import os
import sys
import threading

import pytest

from fixtures import mini_bird as M
from t2s.cli import data_cmds, run_cmds
from t2s.cli.main import main
from t2s.prep import embed
from t2s.runner import records as R

pytest.importorskip("func_timeout")


class Spawned:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))

        class P:
            pid = 424242

        return P()


@pytest.fixture
def root(tmp_path, monkeypatch, shop_db, capsys):
    r = M.make_mini_bird(tmp_path / "repo", shop_db)
    model = r / "data/models/Xenova/all-mpnet-base-v2"
    fake = {}
    for f in embed.MODEL_FILES:
        (model / f).parent.mkdir(parents=True, exist_ok=True)
        (model / f).write_bytes(f"fake {f}".encode())
        fake[f] = hashlib.sha256(f"fake {f}".encode()).hexdigest()
    monkeypatch.setattr(embed, "MODEL_SHA256", fake)
    monkeypatch.setattr(embed, "load_embedder", lambda d: embed.HashEmbedder())
    monkeypatch.setenv("T2S_ROOT", str(r))
    monkeypatch.setattr(data_cmds, "install_guard", lambda root: None)
    monkeypatch.setattr(run_cmds, "install_guard", lambda root: None)
    monkeypatch.setattr(run_cmds, "PAIR_POLL_SECONDS", 0.05)
    assert main(["prepare"]) == 0
    capsys.readouterr()
    return r


def cli(capsys, *argv):
    code = main(list(argv))
    o = capsys.readouterr()
    return code, o.out.splitlines(), o.err


def _start(root, capsys, monkeypatch, *extra):
    sp = Spawned()
    monkeypatch.setattr(run_cmds, "_popen", sp)
    code, lines, err = cli(capsys, "route-start", "--backend", "fake", *extra)
    return code, lines, err, sp


def test_route_start_spawns_two_paired_runs(root, capsys, monkeypatch):
    code, lines, _, sp = _start(root, capsys, monkeypatch, "--limit", "3")
    assert code == 0, lines
    assert lines[0].endswith("출처 가짜 백엔드")
    assert "경로 harness(전체 P3) · 측정용 강제" in lines and "경로 single(단일 호출) · 측정용 강제" in lines
    assert "밖으로 나가는 것 없음(가짜 백엔드)" in lines
    assert len(sp.calls) == 2
    ids = [cmd[-1] for cmd, _ in sp.calls]
    assert ids[0].endswith("-harness") and ids[1].endswith("-single")
    pair = ids[0].removesuffix("-harness")
    assert ids[1] == f"{pair}-single" and lines[-1].startswith(f"짝 {pair} · 진행: t2s route-status")
    for cmd, kw in sp.calls:
        tail = [sys.executable, "-m", "t2s.cli.main", "run", "--worker", "--run-id"]
        assert cmd[-len(tail) - 1 : -1] == tail and kw["start_new_session"] is True
    h = R.read_json(root / "runs" / ids[0] / "run.json")
    s = R.read_json(root / "runs" / ids[1] / "run.json")
    assert h["route"]["pair"] == {"id": pair, "role": "harness", "other": ids[1]}
    assert s["route"]["pair"] == {"id": pair, "role": "single", "other": ids[0]}
    assert (h["route"]["applied"], s["route"]["applied"]) == ("harness", "single")
    assert h["route"]["source"] == s["route"]["source"] == "forced"
    assert h["questions"] == s["questions"] and h["count"] == s["count"] == 3
    assert h["pipeline"] == s["pipeline"]
    assert R.list_pairs(root / "runs")[pair]["runs"] == {"harness": ids[0], "single": ids[1]}


def test_route_start_refused_while_running(root, capsys, monkeypatch):
    code, _, _ = cli(
        capsys, "run", "--foreground", "--backend", "fake", "--no-score", "--run-id", "busy", "--limit", "1"
    )
    R.update_control(root / "runs/busy", state="running", pid=os.getpid())
    code, _, err, sp = _start(root, capsys, monkeypatch)
    assert code == 1 and "이미 도는 run이 있습니다: busy" in err and sp.calls == []


def _work_both(root, ids):
    codes = {}

    def go(rid):
        codes[rid] = main(["run", "--worker", "--run-id", rid])

    threads = [threading.Thread(target=go, args=(rid,)) for rid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=240)
    return codes


def test_pair_runs_together_then_judge_and_apply(root, capsys, monkeypatch):
    code, lines, _, sp = _start(root, capsys, monkeypatch)
    assert code == 0
    ids = [cmd[-1] for cmd, _ in sp.calls]
    pair = ids[0].removesuffix("-harness")

    # 짝 밖의 run은 짝이 도는 동안 거절
    R.update_control(root / "runs" / ids[0], state="running", pid=os.getpid())
    code, _, err = cli(capsys, "run", "--foreground", "--backend", "fake", "--run-id", "x")
    assert code == 1 and f"이미 도는 run이 있습니다: {ids[0]}" in err
    R.update_control(root / "runs" / ids[0], state="starting", pid=424242)

    codes = _work_both(root, ids)
    capsys.readouterr()
    assert codes == {ids[0]: 0, ids[1]: 0}
    h_rows = R.read_jsonl(root / "runs" / ids[0] / "records.jsonl")
    s_rows = R.read_jsonl(root / "runs" / ids[1] / "records.jsonl")
    assert all(r["llm"]["call_count"] == 1 for r in s_rows)
    assert all(r["llm"]["call_count"] > 1 for r in h_rows)
    for rid in ids:
        run = root / "runs" / rid
        assert (run / "score.json").is_file() and (run / "report.json").is_file()
        assert R.read_json(run / "control.json")["phase"] == "done"
        names = {
            json.loads((c / "llm-meta.json").read_text())["name"]
            for c in (run / "items").glob("*/calls/*")
            if (c / "llm-meta.json").is_file()
        }
        if rid.endswith("-single"):
            assert names == {"single_sql"}
        else:
            assert {"extract_keywords", "generate_sql"} <= names and "single_sql" not in names
    # 하네스 run은 단일 호출 run이 끝난 뒤 채점했다
    h_score = R.read_json(root / "runs" / ids[0] / "score.json")["scored_at"]
    s_score = R.read_json(root / "runs" / ids[1] / "score.json")["scored_at"]
    assert s_score <= h_score

    code, lines, _ = cli(capsys, "route-status")
    assert code == 0 and len(lines) <= 10
    assert lines[0] == f"짝 {pair} · mini · evidence not_used · 6문항"
    assert lines[-1] == "두 run 모두 채점 끝: t2s route-judge"

    code, lines, _ = cli(capsys, "route-judge")
    assert code == 0, lines
    assert lines[0] == f"경로 판정 route-judge/1 · 짝 {pair}"
    assert lines[1].startswith("하네스: BIRD Mini-Dev") and " / 경로 single(단일 호출) / " in lines[2]
    assert any(x.startswith("새로 맞음 ") for x in lines) and any(x.startswith("경로 판정: ") for x in lines)
    assert any(x.startswith("기록에 쓸 수 없음: ") and "가짜 백엔드" in x for x in lines)
    jpath = R.judgment_path(root / "runs", ids[0], ids[1])
    data = R.read_json(jpath)
    assert data["usable"] is False and data["harness_run"] == ids[0] and data["candidate_run"] == ids[1]
    assert data["basis"]["single_run"] == ids[1]
    text = jpath.read_text(encoding="utf-8")
    assert not any(secret in text for secret in (*M.GOLD_SQL, *M.EVIDENCE))
    before = jpath.read_text()
    code, lines, _ = cli(capsys, "route-judge", "--pair", pair)
    assert code == 0 and lines[-1].startswith("판정 파일(이미 있음, 같은 내용)") and jpath.read_text() == before
    # 내용이 다르면 덮어쓰지 않는다
    changed = {**R.read_json(jpath), "verdict": "other"}
    R.write_json_atomic(jpath, changed)
    code, lines, _ = cli(capsys, "route-judge")
    assert code == 1 and "덮어쓰지 않음" in lines[-1] and R.read_json(jpath)["verdict"] == "other"
    R.write_json_atomic(jpath, json.loads(before))

    toml_before = (root / "harness.toml").read_text()
    code, _, err = cli(capsys, "route-apply")
    assert code == 1 and "기록에 쓸 수 없는 판정입니다" in err
    code, _, err = cli(capsys, "route-apply", "--yes")
    assert code == 1 and (root / "harness.toml").read_text() == toml_before


def test_route_judge_before_score(root, capsys, monkeypatch):
    code, _, _, sp = _start(root, capsys, monkeypatch, "--no-score")
    ids = [cmd[-1] for cmd, _ in sp.calls]
    code, lines, _ = cli(capsys, "route-judge")
    assert code == 1 and lines == [f"아직 생성 중입니다: {ids[0]} (t2s route-status)"]
    _work_both(root, ids)
    capsys.readouterr()
    code, lines, _ = cli(capsys, "route-judge")
    assert code == 1 and lines == [f"아직 채점 전입니다: t2s score --run {ids[0]}"]


def test_route_status_without_pairs(root, capsys):
    code, lines, _ = cli(capsys, "route-status")
    assert code == 0 and lines == ["시작한 짝(route-start)이 없습니다"]
    code, _, err = cli(capsys, "route-apply")
    assert code == 1 and "짝(route-start)이 없습니다" in err


def test_resume_of_pair_run_ignores_partner(root, capsys, monkeypatch):
    code, _, _, sp = _start(root, capsys, monkeypatch, "--no-score")
    ids = [cmd[-1] for cmd, _ in sp.calls]
    R.update_control(root / "runs" / ids[0], state="running", pid=os.getpid())
    R.update_control(root / "runs" / ids[1], state="stopped", pid=None)
    monkeypatch.setattr(run_cmds.batch, "git_info", lambda root: {"head": "h", "src_tree": "t", "dirty": False})
    for rid in ids:
        meta = R.read_json(root / "runs" / rid / "run.json")
        meta["git"] = {"head": "h", "src_tree": "t", "dirty": False}
        R.write_json_atomic(root / "runs" / rid / "run.json", meta)
    code, lines, err = cli(capsys, "resume", "--run-id", ids[1])
    assert code == 0, err
    assert any(x.startswith(f"시작: {ids[1]}") for x in lines)


def test_route_judge_halted_run_not_sent_to_scoring(root, capsys, monkeypatch):
    code, _, _, sp = _start(root, capsys, monkeypatch, "--no-score")
    ids = [cmd[-1] for cmd, _ in sp.calls]
    _work_both(root, ids)
    capsys.readouterr()
    mpath = root / "runs" / ids[0] / "manifest.json"
    R.write_json_atomic(mpath, {**R.read_json(mpath), "halted": {"reason": "usage_limit"}})
    code, lines, _ = cli(capsys, "route-judge")
    assert code == 1 and len(lines) == 1, lines
    assert lines[0].startswith(f"멈춘 run입니다: {ids[0]}") and f"t2s resume --run-id {ids[0]}" in lines[0]
    assert not any("t2s score" in x for x in lines)
    assert not (root / "runs" / ids[0] / "score.json").exists()
    assert not R.judgment_path(root / "runs", ids[0], ids[1]).exists()


def test_route_judge_running_run_not_sent_to_scoring(root, capsys, monkeypatch):
    code, _, _, sp = _start(root, capsys, monkeypatch, "--no-score")
    ids = [cmd[-1] for cmd, _ in sp.calls]
    _work_both(root, ids)
    capsys.readouterr()
    R.update_control(root / "runs" / ids[0], state="running", pid=os.getpid())
    code, lines, _ = cli(capsys, "route-judge")
    assert code == 1 and lines == [f"아직 도는 중입니다: {ids[0]} (t2s route-status)"]
    assert not any("t2s score" in x for x in lines)
    assert not (root / "runs" / ids[0] / "score.json").exists()
    # 죽은 pid면 도는 중으로 보지 않고 채점 전 안내로 간다
    R.update_control(root / "runs" / ids[0], state="running", pid=None, worker_pid=None)
    code, lines, _ = cli(capsys, "route-judge")
    assert code == 1 and lines == [f"아직 채점 전입니다: t2s score --run {ids[0]}"]


def test_route_start_refuses_dirty_full_measurement(root, capsys, monkeypatch):
    f = run_cmds._dirty_full_measurement
    assert f("codex-cli", 500, 500, {"dirty": True}) is True
    assert f("codex-cli", 500, 500, {"dirty": None}) is True
    assert f("codex-cli", 500, 500, {"dirty": False}) is False
    assert f("codex-cli", 3, 500, {"dirty": True}) is False
    assert f("fake", 500, 500, {"dirty": True}) is False
    monkeypatch.setattr(run_cmds.batch, "git_info", lambda root: {"head": "h", "src_tree": "t", "dirty": True})
    seen = []
    monkeypatch.setattr(run_cmds, "_dirty_full_measurement", lambda *a: seen.append(a) or True)
    runs = root / "runs"
    before = sorted(p.name for p in runs.iterdir()) if runs.exists() else []
    code, lines, err, sp = _start(root, capsys, monkeypatch)
    assert code == 1 and "미커밋 코드 상태에서는 전체 측정을 시작하지 않습니다" in err and "--limit" in err
    assert sp.calls == [] and not any(x.startswith("시작: ") for x in lines)
    assert seen and seen[0][0] == "fake" and seen[0][1] == seen[0][2] and seen[0][3]["dirty"] is True
    assert (sorted(p.name for p in runs.iterdir()) if runs.exists() else []) == before


def test_route_judge_refuses_different_model_before_comparing(root, capsys, monkeypatch):
    code, _, _, sp = _start(root, capsys, monkeypatch)
    ids = [cmd[-1] for cmd, _ in sp.calls]
    assert _work_both(root, ids) == {ids[0]: 0, ids[1]: 0}
    capsys.readouterr()
    jpath = R.judgment_path(root / "runs", ids[0], ids[1])
    for field, change in (("llm", {"model": "other-model"}), ("git", {"head": "0" * 40})):
        mpath = root / "runs" / ids[1] / "run.json"
        original = R.read_json(mpath)
        R.write_json_atomic(mpath, {**original, field: {**(original.get(field) or {}), **change}})
        code, lines, _ = cli(capsys, "route-judge", "--harness", ids[0], "--candidate", ids[1])
        assert code == 1, lines
        assert any("견주지 않는다" in x for x in lines)
        assert not any(x.startswith(("경로 판정:", "새로 맞음")) for x in lines)
        assert not jpath.exists()
        R.write_json_atomic(mpath, original)
