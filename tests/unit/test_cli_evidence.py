"""evidence used run: 작은 가짜 BIRD·스터디로 run → score → report → leak-audit, 분리 프로세스·resume·거절 갈래."""

import hashlib
import json

import pytest

from fixtures import mini_bird as M
from fixtures.fake_cli import make_fake_cli
from t2s.cli import data_cmds, run_cmds
from t2s.cli.main import main
from t2s.llm.base import LlmFailure, LlmSettings
from t2s.llm.fake import FakeBackend, _finish
from t2s.prep import embed
from t2s.runner import records as R

pytest.importorskip("func_timeout")


def _fake_model(r, monkeypatch):
    model = r / "data/models/Xenova/all-mpnet-base-v2"
    fake = {}
    for f in embed.MODEL_FILES:
        (model / f).parent.mkdir(parents=True, exist_ok=True)
        (model / f).write_bytes(f"fake {f}".encode())
        fake[f] = hashlib.sha256(f"fake {f}".encode()).hexdigest()
    monkeypatch.setattr(embed, "MODEL_SHA256", fake)
    monkeypatch.setattr(embed, "load_embedder", lambda d: embed.HashEmbedder())


@pytest.fixture
def guard_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(data_cmds, "install_guard", lambda root: None)
    monkeypatch.setattr(run_cmds, "install_guard", lambda root, **kw: calls.append(kw))
    return calls


@pytest.fixture
def root(tmp_path, monkeypatch, shop_db, capsys, guard_calls):
    r = M.make_mini_bird(tmp_path / "repo", shop_db)
    M.add_public_hints(r)
    _fake_model(r, monkeypatch)
    monkeypatch.setenv("T2S_ROOT", str(r))
    assert main(["prepare"]) == 0
    capsys.readouterr()
    return r


def cli(capsys, *argv):
    code = main(list(argv))
    o = capsys.readouterr()
    return code, o.out.splitlines(), o.err


def prompts_of(run, pos):
    return [p.read_text(encoding="utf-8") for p in sorted((R.item_dir(run, pos) / "calls").glob("*/prompt.txt"))]


def test_bird_used_run(root, capsys, guard_calls):
    code, lines, _ = cli(capsys, "run", "--foreground", "--backend", "fake", "--evidence", "used", "--run-id", "u1")
    assert code == 0, lines
    assert guard_calls[0] == {"allow_hints": True}
    assert any(x.startswith("시작: u1") and x.endswith("· evidence used") for x in lines)
    run = root / "runs/u1"
    meta = R.read_json(run / "run.json")
    ev = meta["evidence"]
    assert ev == {
        "mode": "used",
        "source": "official",
        "path": "runs/u1/hints.json",
        "sha256": R.sha256_file(run / "hints.json"),
        "source_path": M.HINTS,
        "source_sha256": R.sha256_file(root / M.HINTS),
        "count": 6,
        "rules": "1.0.0",
    }
    assert R.read_json(run / "hints.json") == M.EVIDENCE
    assert not any(e in json.dumps(meta) for e in M.EVIDENCE)
    for pos in range(6):
        ps = prompts_of(run, pos)
        assert ps and all(M.EVIDENCE[pos] in p for p in ps)
        assert f"-- External Knowledge: {M.EVIDENCE[pos]}\n" in "".join(ps)
    score = R.read_json(run / "score.json")
    assert " / EX / evidence used / 로컬 측정 / " in score["label"]
    assert score["evidence"]["mode"] == "used" and score["evidence"]["checked"] is True
    assert score["leak"]["total"] == 0 and score["leak"]["evidence_missing"] == 0
    code, leak, _ = cli(capsys, "leak-audit", "--run", "u1")
    assert code == 0 and "정답 SQL 0, evidence 제공(used)·빠짐 0, 다른 문항 정답 0, 프롬프트 누락 0" in leak[0]
    code, rep, _ = cli(capsys, "report", "--run", "u1")
    assert code == 0 and rep[0] == score["label"] and "비교 대상 없음" in rep
    assert R.read_json(run / "report.json")["evidence"] == "used"
    code, st, _ = cli(capsys, "status", "--run-id", "u1")
    assert st[0] == "u1 · mini · evidence used · 끝남"


def test_not_used_run_has_no_evidence_anywhere(root, capsys, guard_calls):
    code, _, _ = cli(capsys, "run", "--foreground", "--backend", "fake", "--run-id", "n1")
    assert code == 0 and guard_calls[0] == {}
    run = root / "runs/n1"
    assert not (run / "hints.json").exists()
    assert R.read_json(run / "run.json")["evidence"] == {"mode": "not_used"}
    text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in run.rglob("*") if p.is_file())
    assert not any(e in text for e in M.EVIDENCE)
    assert R.read_json(run / "score.json")["evidence"] == {"mode": "not_used"}


def test_outgoing_line_names_evidence(root, capsys, monkeypatch, tmp_path):
    exe = make_fake_cli(tmp_path, "codex")
    monkeypatch.setenv("T2S_CODEX_BIN", str(exe))
    monkeypatch.setenv("FAKE_CLI_MODE", "ok")

    class P:
        pid = 424242

    monkeypatch.setattr(run_cmds, "_popen", lambda cmd, **kw: P())
    args = ["--backend", "codex-cli", "--model", "gpt-5.6-terra", "--effort", "medium"]
    code, lines, _ = cli(capsys, "run", *args, "--evidence", "used", "--run-id", "o1")
    assert code == 0
    assert any(x.startswith("밖으로 나감(OpenAI): ") and x.endswith(", 문항 evidence 글") for x in lines)
    code, lines, _ = cli(capsys, "run", *args, "--no-probe", "--run-id", "o2")
    assert any(x.startswith("밖으로 나감(OpenAI): ") and "evidence" not in x for x in lines)


def test_worker_reads_only_run_hints(root, capsys, monkeypatch, guard_calls):
    class P:
        pid = 424242

    monkeypatch.setattr(run_cmds, "_popen", lambda cmd, **kw: P())
    code, _, _ = cli(capsys, "run", "--backend", "fake", "--evidence", "used", "--no-score", "--run-id", "w1")
    assert code == 0 and guard_calls[-1] == {"allow_hints": True}
    (root / M.HINTS).unlink()  # 분리 프로세스는 data/의 공개 파일을 열지 않는다
    code, _, err = cli(capsys, "run", "--worker", "--run-id", "w1")
    assert code == 0, err
    assert guard_calls[-1] == {}
    assert all(M.EVIDENCE[3] in p for p in prompts_of(root / "runs/w1", 3))
    # hints.json이 run.json의 해시와 다르면 분리 프로세스는 돌지 않는다
    code, _, err = cli(capsys, "run", "--backend", "fake", "--evidence", "used", "--no-score", "--run-id", "w2")
    assert code == 1 and "/getbird" in err  # 공개 파일이 없으면 시작하지 않는다
    M.add_public_hints(root)
    code, _, err = cli(capsys, "run", "--backend", "fake", "--evidence", "used", "--no-score", "--run-id", "w3")
    assert code == 0
    (root / "runs/w3/hints.json").write_text(json.dumps(["x"] * 6), encoding="utf-8")
    code, _, err = cli(capsys, "run", "--worker", "--run-id", "w3")
    assert code == 1 and "hints.json" in err and R.read_json(root / "runs/w3/control.json")["state"] == "failed"


class FailSecond:
    id = "fake"
    settings = LlmSettings("fake", "fake-model", "none", 1)

    def call(self, prompt, call_dir, *, name, attempt):
        if M.QUESTIONS[1] in prompt:
            return _finish(self.settings, prompt, call_dir, None, LlmFailure("CodexExit", "가짜"), name, attempt)
        return FakeBackend().call(prompt, call_dir, name=name, attempt=attempt)


def test_resume_keeps_mode_and_checks_hash(root, capsys, monkeypatch):
    monkeypatch.setattr(run_cmds, "BACKEND_FACTORY", FailSecond)
    base = ("run", "--foreground", "--backend", "fake", "--evidence", "used", "--no-score")
    cli(capsys, *base, "--run-id", "r1")
    rows = R.read_jsonl(root / "runs/r1/records.jsonl")
    assert rows[1]["status"] == "exception"
    monkeypatch.setattr(run_cmds, "BACKEND_FACTORY", None)
    (root / M.HINTS).unlink()  # resume도 run 폴더의 hints.json만 읽는다
    code, lines, err = cli(capsys, "resume", "--run-id", "r1", "--foreground")
    assert code == 0, err
    assert any("이어 돌기 1문항 · evidence used" in x for x in lines)
    assert [r["status"] for r in R.read_jsonl(root / "runs/r1/records.jsonl")] == ["generated"] * 6
    assert all(M.EVIDENCE[1] in p for p in prompts_of(root / "runs/r1", 1))
    # hints.json이 바뀌면 거절
    monkeypatch.setattr(run_cmds, "BACKEND_FACTORY", FailSecond)
    M.add_public_hints(root)
    cli(capsys, *base, "--run-id", "r2")
    (root / "runs/r2/hints.json").write_text("[]", encoding="utf-8")
    code, _, err = cli(capsys, "resume", "--run-id", "r2")
    assert code == 1 and "evidence 파일(hints.json)이 run.json과 다릅니다" in err


def test_used_refusals(root, capsys, shop_db, tmp_path):
    q = tmp_path / "q.json"
    q.write_text(json.dumps([{"question_id": 900, "db_id": "shop", "question": "q", "difficulty": "simple"}]))
    code, _, err = cli(capsys, "run", "--backend", "fake", "--evidence", "used", "--questions", str(q), "--run-id", "a")
    assert code == 2 and "--questions 없이" in err
    (root / M.HINTS).unlink()
    code, _, err = cli(capsys, "run", "--backend", "fake", "--evidence", "used", "--run-id", "b")
    assert code == 1 and "먼저 /getbird(다시 부르면 evidence 파일을 만든다)" in err
    assert main(["register", "shop", "--sqlite", str(shop_db), "--yes"]) == 0
    assert main(["prepare", "shop"]) == 0
    assert main(["use", "shop"]) == 0
    t = tmp_path / "q.txt"
    t.write_text("How many products?\n")
    capsys.readouterr()
    for path in (t, q):
        code, _, err = cli(capsys, "run", "--backend", "fake", "--evidence", "used", "--questions", str(path))
        assert code == 2 and "이 문항에는 evidence가 없습니다" in err
    assert not (root / "runs").exists() or not any((root / "runs").iterdir())


def test_score_refuses_changed_evidence(root, capsys):
    cli(capsys, "run", "--foreground", "--backend", "fake", "--evidence", "used", "--no-score", "--run-id", "s1")
    run = root / "runs/s1"
    texts = R.read_json(run / "hints.json")
    texts[4] = texts[4] + " changed"
    R.write_json_atomic(run / "hints.json", texts)
    meta = R.read_json(run / "run.json")
    meta["evidence"]["sha256"] = R.sha256_file(run / "hints.json")
    R.write_json_atomic(run / "run.json", meta)
    code, _, err = cli(capsys, "score", "--run", "s1")
    assert code == 1 and "정답 쪽 원본과 다릅니다(위치 4)" in err
    assert not any(e in err for e in M.EVIDENCE)


def test_report_refuses_across_modes(root, capsys, monkeypatch):
    from t2s.eval import report as REP

    cli(capsys, "run", "--foreground", "--backend", "fake", "--evidence", "used", "--run-id", "u")
    cli(capsys, "run", "--foreground", "--backend", "fake", "--run-id", "n")
    cli(capsys, "run", "--foreground", "--backend", "fake", "--evidence", "used", "--run-id", "u2")
    code, lines, _ = cli(capsys, "report", "--run", "u", "--against", "n")
    assert code == 1 and "evidence 조건이 달라 견주지 않는다(이번 used, 기준 not_used)" in lines
    assert not any(x.startswith("새로 맞음") for x in lines)
    code, lines, _ = cli(capsys, "report", "--run", "n", "--against", "u")
    assert code == 1 and "evidence 조건이 달라 견주지 않는다(이번 not_used, 기준 used)" in lines
    code, lines, _ = cli(capsys, "report", "--run", "u", "--against", str(root / "runs/n/ex.jsonl"))
    assert code == 1 and "evidence 조건이 달라 견주지 않는다(이번 used, 기준 알 수 없음)" in lines
    code, lines, _ = cli(capsys, "report", "--run", "n", "--against", str(root / "runs/u/ex.jsonl"))
    assert code == 0 and any(x.startswith("새로 맞음") for x in lines)  # 방식을 모르는 쪽은 not_used로 본다
    code, lines, _ = cli(capsys, "report", "--run", "u2", "--against", "u")
    assert code == 0 and any(x.startswith("새로 맞음") for x in lines)
    data = R.read_json(root / "runs/u2/report.json")
    assert data["evidence"] == "used" and data["against_evidence"] == "used"
    # 기본 기준(not_used 기준선)만 있으면 used run은 견주지 않고 종료 코드 0
    (root / "baselines").mkdir()
    rows = [{"position": i, "question_id": 900 + i, "db_id": "shop", "ex_match": 1} for i in range(6)]
    (root / "baselines/b.json").write_text(json.dumps({"pack": "p", "run_id": "r0", "summary": {}, "rows": rows}))
    monkeypatch.setattr(REP, "DEFAULT_BASELINES", {("mini", "not_used"): "baselines/b.json"})
    code, lines, _ = cli(capsys, "report", "--run", "u")
    assert code == 0 and "비교 대상 없음(기준은 evidence not_used, 이번은 used라 견주지 않는다)" in lines
    code, lines, _ = cli(capsys, "report", "--run", "n")
    assert code == 0 and any(x.startswith("기준 baselines/b.json") for x in lines)


# ── 스터디(Olist) ───────────────────────────────────────


@pytest.fixture
def study_root(tmp_path, monkeypatch, shop_db, capsys, guard_calls):
    from fixtures import mini_study as MS

    r = MS.make_mini_study(tmp_path / "srepo", shop_db)
    _fake_model(r, monkeypatch)
    monkeypatch.setenv("T2S_ROOT", str(r))
    assert main(["prepare"]) == 0
    capsys.readouterr()
    return r, MS


def test_study_used_run(study_root, capsys):
    root, MS = study_root
    argv = ["run", "--foreground", "--backend", "fake", "--evidence", "used", "--questions", str(root / MS.INPUTS)]
    code, lines, _ = cli(capsys, *argv, "--run-id", "su")
    assert code == 0, lines
    run = root / "runs/su"
    meta = R.read_json(run / "run.json")
    assert meta["questions"]["conditions"] == "used"
    ev = meta["evidence"]
    assert ev["mode"] == "used" and ev["source"] == "task_conditions" and ev["join"] == "; " and ev["count"] == 4
    assert ev["source_path"] == MS.INPUTS and ev["source_sha256"] == R.sha256_file(root / MS.INPUTS)
    want = ["; ".join(c.strip() for c in cs) for cs in MS.CONDITIONS]
    assert R.read_json(run / "hints.json") == want
    for pos in range(4):
        ps = prompts_of(run, pos)
        assert ps and all(want[pos] in p for p in ps) and not any(MS.MARK in p for p in ps)
    score = R.read_json(run / "score.json")
    assert " / EX(스터디 결과 비교) / evidence used / 로컬 측정 / " in score["label"]
    assert score["evidence"]["checked"] is True and score["conditions"] == "used"
    assert score["leak"]["total"] == 0
    code, leak, _ = cli(capsys, "leak-audit", "--run", "su")
    assert code == 0 and "evidence 제공(used)·빠짐 0, 채점 메모 0, 다른 문항 정답 0" in leak[0]
    assert MS.MARK not in leak[0] and MS.COND_MARK not in leak[0]
