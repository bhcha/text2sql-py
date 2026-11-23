"""session open·note·close: 지표 길의 세션 폴더(session.json)만 쓰고 DB를 열지 않는다. 읽기 감시를 건다."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from t2s.cli import session_cmds
from t2s.cli.main import main
from t2s.session import index as session_index
from t2s.session import names as session_names

TOML = """
[paths]
data = "data"
runs = "sessions"
"""

GUARDED: list = []


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / ".text2sql"
    r.mkdir()
    (r / "harness.toml").write_text(TOML, encoding="utf-8")
    monkeypatch.setenv("T2S_ROOT", str(r))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    GUARDED.clear()
    monkeypatch.setattr(session_cmds, "install_guard", lambda root: GUARDED.append(root))  # 감시 훅은 뗄 수 없다
    return r


def cli(capsys, *argv):
    code = main(list(argv))
    o = capsys.readouterr()
    return code, o.out.splitlines(), o.err


def _session(folder: Path) -> dict:
    return json.loads((folder / "session.json").read_text(encoding="utf-8"))


def test_open_note_close(root, capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "conv-1")
    code, lines, _ = cli(capsys, "session", "open", "--kind", "metric", "--question", "지난달 매출은?")
    assert code == 0 and len(lines) == 1
    folder = Path(lines[0])
    assert folder.parent == root.resolve() / "sessions" and folder.is_dir()
    parsed = session_names.parse(folder.name)
    assert parsed.kind == "metric" and parsed.dataset == "none" and not parsed.legacy
    rec = _session(folder)
    assert rec["kind"] == "metric" and rec["question"] == "지난달 매출은?" and rec["dataset"] is None
    assert rec["closed_at"] is None and rec["conversation"] == {"client": "claude", "id": "conv-1"}
    assert rec["leak_audit"].startswith("대상 아님")
    assert sorted(p.name for p in folder.iterdir()) == ["session.json"]
    [row] = session_index.read(root / "sessions")
    assert (row["name"], row["kind"], row["dataset"]) == (folder.name, "metric", None)
    assert GUARDED == [root.resolve()]

    code, lines, _ = cli(capsys, "session", "note", folder.name, "--key", "model", "--value", "sales")
    assert code == 0 and lines == [f"적음: {folder.name} · model"]
    code, _, _ = cli(capsys, "session", "note", folder.name, "--key", "version_id", "--value", "v3")
    assert code == 0
    code, lines, _ = cli(capsys, "session", "note", folder.name, "--key", "model", "--value", "sales")
    assert code == 0 and lines[0].startswith("이미 적혀 있습니다")
    code, _, err = cli(capsys, "session", "note", folder.name, "--key", "model", "--value", "other")
    assert code == 1 and "덮어쓰지 않음" in err
    rec = _session(folder)
    assert rec["model"] == "sales" and rec["version_id"] == "v3"

    code, lines, _ = cli(capsys, "session", "close", folder.name)
    assert code == 0 and lines[0].startswith(f"닫음: {folder.name} (")
    closed = _session(folder)["closed_at"]
    assert closed
    code, lines, _ = cli(capsys, "session", "close", folder.name)
    assert code == 0 and lines[0].startswith("이미 닫힌 세션입니다") and _session(folder)["closed_at"] == closed
    code, _, err = cli(capsys, "session", "note", folder.name, "--key", "late", "--value", "x")
    assert code == 1 and "닫힌 세션" in err and "late" not in _session(folder)
    assert len(session_index.read(root / "sessions")) == 1  # 적기·닫기는 색인에 더하지 않는다


def test_open_uses_current_dataset(root, capsys):
    (root / "var").mkdir()
    db = root / "shop.sqlite"
    db.write_bytes(b"")
    state = {
        "format": "t2s-state/1",
        "current_dataset": "shop",
        "registered": {"shop": {"kind": "single", "sources": {"shop": {"kind": "sqlite", "path": str(db)}}}},
    }
    (root / "var/state.json").write_text(json.dumps(state), encoding="utf-8")
    code, lines, err = cli(capsys, "session", "open", "--kind", "explore")
    assert code == 0, err
    folder = Path(lines[0])
    assert session_names.parse(folder.name).dataset == "shop" and _session(folder)["dataset"] == "shop"
    assert _session(folder)["question"] is None


def test_wrong_kind_and_missing_session(root, capsys):
    code, _, _ = cli(capsys, "session", "open", "--kind", "set")
    assert code == 2
    code, _, _ = cli(capsys, "session", "open", "--kind", "ask")
    assert code == 2
    code, _, _ = cli(capsys, "session", "open")
    assert code == 2
    assert not (root / "sessions").exists()
    missing = session_names.new_name("none", "metric")
    code, _, err = cli(capsys, "session", "close", missing)
    assert code == 1 and f"세션이 없습니다: {missing}" in err
    for bad in ("../x", "r5", "20261121-bird-minidev-665ae5", session_names.new_name("shop", "set")):
        code, _, err = cli(capsys, "session", "note", bad, "--key", "model", "--value", "x")
        assert code == 2 and "지표 길 세션 이름이 아닙니다" in err, bad


def test_note_refuses_bad_keys_and_long_values(root, capsys):
    code, lines, _ = cli(capsys, "session", "open", "--kind", "check")
    name = Path(lines[0]).name
    for key in ("kind", "closed_at", "Model", "1x", "a-b", "leak_audit"):
        code, _, err = cli(capsys, "session", "note", name, "--key", key, "--value", "x")
        assert code == 2 and "쓸 수 없는 칸 이름" in err, key
    code, _, err = cli(capsys, "session", "note", name, "--key", "rows", "--value", "x" * 501)
    assert code == 2 and "결과 행은 적지 않는다" in err
    assert _session(root / "sessions" / name)["kind"] == "check" and "rows" not in _session(root / "sessions" / name)


def test_does_not_write_into_run_sessions(root, capsys):
    """session note·close는 run·ask 세션 폴더(session.json이 없는 폴더)에 쓰지 않는다."""
    name = session_names.new_name("shop", "metric")
    run = root / "sessions" / name
    run.mkdir(parents=True)
    (run / "run.json").write_text("{}", encoding="utf-8")
    code, _, err = cli(capsys, "session", "close", name)
    assert code == 1 and "세션이 없습니다" in err
    assert sorted(p.name for p in run.iterdir()) == ["run.json"]


# ── sessions(목록) ──


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


LLM = {"backend": "fake", "model": "fake-model", "reasoning_effort": None}
LABEL = "BIRD Mini-Dev / SQLite / ab12cd34 / 6문제 / EX / evidence not_used / 로컬 측정 / fake-model·추론 none·fake"


def test_sessions_empty(root, capsys):
    code, lines, _ = cli(capsys, "sessions")
    assert code == 0 and lines == [f"세션 폴더: {root.resolve() / 'sessions'}", "세션이 없습니다"]
    assert GUARDED == [root.resolve()]


def test_sessions_lists_newest_first_without_reading_big_files(root, capsys):
    s = root / "sessions"
    ask = "2026-11-23-shop-ask-aaaaaa"
    _write(s / ask / "ask.json", {"llm": LLM, "source": "shop", "exit_code": 0})
    scored = "2026-11-23-mini-set-bbbbbb"
    _write(s / scored / "run.json", {"dataset": "mini", "llm": LLM, "count": 6, "started_at": "2026-11-23T02:00:00Z"})
    _write(s / scored / "control.json", {"state": "finished", "pid": None})
    _write(s / scored / "report.json", {"kind": "scored", "label": LABEL, "ex_total": {"correct": 2, "denominator": 6}})
    (s / scored / "score.json").write_text("{}", encoding="utf-8")
    running = "2026-11-23-mini-set-cccccc-harness"
    _write(s / running / "run.json", {"dataset": "mini", "llm": LLM, "count": 3, "evidence": {"mode": "used"}})
    _write(s / running / "control.json", {"state": "running", "pid": os.getpid(), "phase": "generate"})
    # 큰 파일은 읽지 않는다: 읽으려 하면 권한 오류가 나게 막아 둔다
    big = [s / scored / "records.jsonl", s / scored / "items/0000/record.json", s / running / "records.jsonl"]
    for f in big:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("{}", encoding="utf-8")
        f.chmod(0)
    old = "20261120-mini-dddddd"  # 색인에 없는 옛 이름 run
    _write(s / old / "run.json", {"dataset": "mini", "llm": LLM, "count": 6, "started_at": "2026-11-20T00:00:00Z"})
    _write(s / old / "control.json", {"state": "stopped"})
    rows = [
        (ask, "ask", "shop", "2026-11-23T01:00:00Z"),
        (scored, "set", "mini", "2026-11-23T02:00:00Z"),
        (running, "set", "mini", "2026-11-23T04:00:00Z"),
    ]
    for name, kind, ds, at in rows:
        session_index.append(s, session_index.entry(name, kind, ds, at, {}))
    code, lines, _ = cli(capsys, "session", "open", "--kind", "metric", "--question", "q" * 50)
    metric = Path(lines[0]).name
    try:
        code, lines, _ = cli(capsys, "sessions")
    finally:
        for f in big:
            f.chmod(0o644)
    assert code == 0, lines
    assert lines[0] == f"세션 폴더: {root.resolve() / 'sessions'}"
    assert lines[1] == f'{metric} · metric · none · 열림 · 질문 "{"q" * 39}…"'
    assert lines[2] == f"{running} · set · mini · 진행 중(generate) · fake-model·추론 none·fake · evidence used · 3문항"
    assert lines[3] == f"{scored} · set · mini · 끝남(보고까지) · EX 2/6 · {LABEL}"
    assert lines[4] == f"{ask} · ask · shop · 끝남(종료 코드 0) · fake-model·추론 none·fake · 소스 shop"
    assert lines[5] == f"{old} · set · mini · 멈춤 · fake-model·추론 none·fake · evidence not_used · 6문항"
    assert len(lines) == 6


def test_sessions_shows_twenty(root, capsys):
    s = root / "sessions"
    for i in range(23):
        name = f"2026-11-23-mini-ask-{i:06x}"
        _write(s / name / "ask.json", {"llm": LLM, "source": "shop", "exit_code": 0})
        session_index.append(s, session_index.entry(name, "ask", "mini", f"2026-11-23T00:00:{i:02d}Z", {}))
    session_index.append(
        s, session_index.entry("2026-11-23-mini-ask-ffffff", "ask", "mini", "2026-11-23T01:00:00Z", {})
    )
    code, lines, _ = cli(capsys, "sessions")
    assert code == 0 and len(lines) == 1 + 20 + 1
    assert lines[1] == "2026-11-23-mini-ask-ffffff · ask · mini · 폴더 없음"
    assert lines[2].startswith("2026-11-23-mini-ask-000016 · ") and lines[-1] == "… 그 밖 4개(더 오래된 것)"
