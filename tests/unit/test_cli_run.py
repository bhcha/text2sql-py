import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from fixtures.fake_cli import make_fake_cli, read_log
from t2s.cli import data_cmds, run_cmds
from t2s.cli.main import main
from t2s.llm.base import LlmFailure, LlmSettings
from t2s.llm.fake import FakeBackend, _finish
from t2s.prep import embed
from t2s.runner import batch
from t2s.runner import records as R

TOML = """
[defaults]
profile = "session"

[paths]
data = "data"
runs = "runs"
embedding_model = "data/models/Xenova/all-mpnet-base-v2"

[datasets.two]
kind = "single"
[datasets.two.sources.a]
kind = "sqlite"
path = "two/a.sqlite"

[profiles.p3-ref]
backend = "codex-cli"
model = "gpt-5.6-terra"
reasoning_effort = "medium"
timeout_seconds = 20
item_timeout_seconds = 1530

[profiles.session]
backend = "auto"
"""


@pytest.fixture
def root(tmp_path, monkeypatch, shop_db, capsys):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "harness.toml").write_text(TOML, encoding="utf-8")
    (r / "two").mkdir()
    shutil.copy(shop_db, r / "two/a.sqlite")
    model = r / "data/models/Xenova/all-mpnet-base-v2"
    fake = {}
    for f in embed.MODEL_FILES:
        (model / f).parent.mkdir(parents=True, exist_ok=True)
        (model / f).write_bytes(f"fake {f}".encode())
        fake[f] = hashlib.sha256(f"fake {f}".encode()).hexdigest()
    monkeypatch.setattr(embed, "MODEL_SHA256", fake)
    monkeypatch.setattr(embed, "load_embedder", lambda d: embed.HashEmbedder())
    monkeypatch.setenv("T2S_ROOT", str(r))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setattr(data_cmds, "install_guard", lambda root: None)
    monkeypatch.setattr(run_cmds, "install_guard", lambda root: None)
    assert main(["register", "shop", "--sqlite", str(shop_db), "--yes"]) == 0
    assert main(["prepare", "shop"]) == 0
    assert main(["use", "shop"]) == 0
    (r / "q.txt").write_text(
        "How many products?\nList product names.\n# 주석\nHow many orders are paid?\nCount customers.\n"
    )
    capsys.readouterr()
    return r


class Spawned:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))

        class P:
            pid = 424242

        return P()


def run(capsys, *argv):
    code = main(list(argv))
    o = capsys.readouterr()
    return code, o.out.splitlines(), o.err


def test_foreground_fake(root, capsys):
    code, lines, _ = run(
        capsys,
        "run",
        "--foreground",
        "--backend",
        "fake",
        "--limit",
        "3",
        "--no-score",
        "--questions",
        str(root / "q.txt"),
        "--run-id",
        "f3",
    )
    assert code == 0
    assert lines[0].endswith("출처 가짜 백엔드") and "밖으로 나가는 것 없음(가짜 백엔드)" in lines
    run_dir = root / "runs/f3"
    rows = R.read_jsonl(run_dir / "records.jsonl")
    assert [r["question"] for r in rows] == ["How many products?", "List product names.", "How many orders are paid?"]
    meta = R.read_json(run_dir / "run.json")
    assert meta["count"] == 3 and meta["questions"]["total"] == 4 and meta["sample"] is True
    assert meta["questions"]["path"] == "runs/f3/questions.json" and meta["score_after"] is False
    assert meta["llm"]["backend"] == "fake" and meta["databases"]["shop"]["size"] > 0
    assert set(meta["versions_start"]) >= {"python", "sqlite", "codex", "claude"}
    control = R.read_json(run_dir / "control.json")
    assert control["state"] == "finished" and control["phase"] == "done"
    code, lines, _ = run(capsys, "status")
    assert code == 0 and len(lines) <= 10
    assert lines[0] == "f3 · shop · 끝남"
    assert lines[2].startswith("진행 3/3") and lines[3] == "상태 generated 3 · no_sql 0 · exception 0"


def test_detached_command(root, capsys, monkeypatch):
    sp = Spawned()
    monkeypatch.setattr(run_cmds, "_popen", sp)
    code, lines, _ = run(capsys, "run", "--backend", "fake", "--questions", str(root / "q.txt"), "--run-id", "d1")
    assert code == 0
    [(cmd, kw)] = sp.calls
    tail = [sys.executable, "-m", "t2s.cli.main", "run", "--worker", "--run-id", "d1"]
    if shutil.which("caffeinate"):
        assert cmd[1:3] == ["-i", sys.executable] and cmd[0].endswith("caffeinate")
    assert cmd[-len(tail) :] == tail
    assert kw["start_new_session"] is True and kw["env"]["T2S_ROOT"] == str(root)
    assert kw["cwd"] == str(root)
    assert any(line.startswith("시작: d1 (pid 424242)") for line in lines)
    assert R.read_json(root / "runs/d1/control.json")["pid"] == 424242


def _fake_codex(tmp_path, monkeypatch, mode):
    exe = make_fake_cli(tmp_path, "codex")
    monkeypatch.setenv("T2S_CODEX_BIN", str(exe))
    monkeypatch.setenv("FAKE_CLI_MODE", mode)
    log = tmp_path / "cli.log"
    monkeypatch.setenv("FAKE_CLI_LOG", str(log))
    return log


def test_probe_failure_leaves_no_run(root, capsys, monkeypatch, tmp_path):
    log = _fake_codex(tmp_path, monkeypatch, "exit")
    code, _, e = run(capsys, "run", "--profile", "p3-ref", "--questions", str(root / "q.txt"), "--run-id", "p1")
    assert code == 1 and "모델 호출 확인 실패: CodexExit" in e
    assert not (root / "runs/p1").exists()
    [entry] = read_log(log)
    assert entry["prompt"] == "Reply with OK."


def test_probe_ok_once_then_detach(root, capsys, monkeypatch, tmp_path):
    log = _fake_codex(tmp_path, monkeypatch, "ok")
    sp = Spawned()
    monkeypatch.setattr(run_cmds, "_popen", sp)
    code, lines, _ = run(capsys, "run", "--profile", "p3-ref", "--questions", str(root / "q.txt"), "--run-id", "p2")
    assert code == 0 and len(read_log(log)) == 1 and len(sp.calls) == 1
    assert "밖으로 나감(OpenAI): 질문, CREATE 원문·예시 3행, 질문과 비슷한 값, 열 프로파일, 실행 오류 글" in lines
    code, _, _ = run(
        capsys, "run", "--profile", "p3-ref", "--no-probe", "--questions", str(root / "q.txt"), "--run-id", "p3"
    )
    assert len(read_log(log)) == 1  # --no-probe면 부르지 않는다
    # 가짜 백엔드도 부르지 않는다
    code, _, _ = run(capsys, "run", "--backend", "fake", "--questions", str(root / "q.txt"), "--run-id", "p4")
    assert len(read_log(log)) == 1


def test_assets_missing_and_no_questions(root, capsys):
    code, _, e = run(capsys, "run", "--backend", "fake", "--run-id", "x")
    assert code == 2 and "이 데이터셋에는 문항 파일이 없습니다. --questions <파일>" in e
    assert main(["use", "two"]) == 0
    capsys.readouterr()
    code, _, e = run(capsys, "run", "--backend", "fake", "--questions", str(root / "q.txt"), "--run-id", "x")
    assert code == 5 and "준비 필요: t2s prepare two" in e


def test_refuses_when_another_runs(root, capsys):
    busy = root / "runs/busy"
    R.write_json_atomic(busy / "run.json", {"started_at": "2026-11-13T00:00:00Z"})
    R.write_json_atomic(busy / "control.json", {"state": "running", "pid": os.getpid()})
    code, _, e = run(capsys, "run", "--backend", "fake", "--questions", str(root / "q.txt"), "--run-id", "y")
    assert code == 1 and "이미 도는 run이 있습니다: busy" in e


def test_status_without_runs(root, capsys):
    code, lines, _ = run(capsys, "status")
    assert code == 0 and lines == ["시작한 run이 없습니다"]


class FailSecond:
    id = "fake"
    settings = LlmSettings("fake", "fake-model", "none", 1)

    def call(self, prompt, call_dir, *, name, attempt):
        if "List product names." in prompt:
            return _finish(self.settings, prompt, call_dir, None, LlmFailure("CodexExit", "가짜"), name, attempt)
        return FakeBackend().call(prompt, call_dir, name=name, attempt=attempt)


def test_resume(root, capsys, monkeypatch):
    monkeypatch.setattr(run_cmds, "BACKEND_FACTORY", FailSecond)
    code, _, _ = run(
        capsys,
        "run",
        "--foreground",
        "--backend",
        "fake",
        "--no-score",
        "--questions",
        str(root / "q.txt"),
        "--run-id",
        "r1",
    )
    rows = R.read_jsonl(root / "runs/r1/records.jsonl")
    assert [r["status"] for r in rows] == ["generated", "exception", "generated", "generated"]
    monkeypatch.setattr(run_cmds, "BACKEND_FACTORY", None)
    code, lines, _ = run(capsys, "resume", "--run-id", "r1", "--foreground")
    assert code == 0, lines
    rows = R.read_jsonl(root / "runs/r1/records.jsonl")
    assert [r["status"] for r in rows] == ["generated"] * 4
    assert [r["attempt"] for r in rows] == [1, 2, 1, 1]
    assert (root / "runs/r1/items/0001.attempt-1/record.json").is_file()
    m = R.read_json(root / "runs/r1/manifest.json")
    assert m["resumes"][0]["positions"] == [1]

    # 코드 판이 다르면 거절
    monkeypatch.setattr(batch, "git_info", lambda r: {"head": "other", "src_tree": "t", "dirty": False})
    code, _, e = run(capsys, "resume", "--run-id", "r1")
    assert code == 1 and "코드 판이 다릅니다" in e
    # 채점한 run은 거절
    (root / "runs/r1/score.json").write_text("{}")
    code, _, e = run(capsys, "resume", "--run-id", "r1")
    assert code == 1 and "채점한 run입니다" in e


def test_stop_sends_signal_to_group(root, capsys):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        r = root / "runs/s1"
        R.write_json_atomic(r / "run.json", {"started_at": "2026-11-13T00:00:00Z"})
        R.write_json_atomic(r / "control.json", {"state": "running", "pid": proc.pid})
        t0 = time.monotonic()
        code, lines, _ = run(capsys, "stop", "--run-id", "s1")
        assert code == 0 and lines == ["s1 멈춤"] and time.monotonic() - t0 < 10
        assert proc.wait(5) != 0
        assert R.read_json(r / "control.json")["state"] == "stopped"
        code, lines, _ = run(capsys, "stop", "--run-id", "s1")
        assert lines == ["이미 멈춘 run입니다: s1"]
    finally:
        if proc.poll() is None:
            proc.kill()


def test_json_questions_must_match_sources(root, capsys):
    (root / "bad.json").write_text(json.dumps([{"question_id": 1, "db_id": "nope", "question": "q", "difficulty": ""}]))
    code, _, e = run(capsys, "run", "--backend", "fake", "--questions", str(root / "bad.json"), "--run-id", "z")
    assert code == 2 and "소스가 아닙니다" in e
