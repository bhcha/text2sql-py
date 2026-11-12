import hashlib
import json
import shutil

import pytest

from fixtures.fake_cli import make_fake_cli
from t2s.cli import ask_cmd, data_cmds
from t2s.cli.main import main
from t2s.llm import select
from t2s.llm.fake import FakeBackend
from t2s.prep import embed

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
[datasets.two.sources.b]
kind = "sqlite"
path = "two/b.sqlite"

[profiles.p3-ref]
backend = "codex-cli"
model = "gpt-5.6-terra"
reasoning_effort = "medium"
timeout_seconds = 20
item_timeout_seconds = 1530

[profiles.session]
backend = "auto"

[pipeline]
example_rows = 3
keyword_attempts = 3
exec_timeout_seconds = 120
refine_rounds = 3
refine_template = "builtin"
"""
QUESTION = "How many Apple Juice orders are paid?"
GUARDED: list = []


@pytest.fixture
def root(tmp_path, monkeypatch, shop_db):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "harness.toml").write_text(TOML, encoding="utf-8")
    (r / "two").mkdir()
    shutil.copy(shop_db, r / "two/a.sqlite")
    shutil.copy(shop_db, r / "two/b.sqlite")
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
    GUARDED.clear()
    monkeypatch.setattr(data_cmds, "install_guard", GUARDED.append)
    monkeypatch.setattr(ask_cmd, "install_guard", GUARDED.append)
    return r


@pytest.fixture
def shop(root, shop_db, capsys):
    assert main(["register", "shop", "--sqlite", str(shop_db), "--yes"]) == 0
    assert main(["prepare", "shop"]) == 0
    assert main(["use", "shop"]) == 0
    capsys.readouterr()
    GUARDED.clear()
    return root


def ask(capsys, *argv):
    code = main(["ask", *argv])
    o = capsys.readouterr()
    return code, o.out.splitlines(), o.err


def test_fake_ask(shop, capsys):
    code, lines, _ = ask(capsys, "--backend", "fake", QUESTION)
    assert code == 0
    assert lines[0] == "모델 fake-model · 추론 none · 백엔드 fake · 출처 가짜 백엔드"
    assert lines[1].startswith("데이터셋 shop · 소스 shop · ")
    assert lines[2] == "밖으로 나가는 것 없음(가짜 백엔드)"
    assert lines[3] == "SQL:" and lines[4] == "SELECT COUNT(*) FROM product"
    assert lines[5].startswith("실행: rows 1행 · 열 COUNT(*)")
    assert lines[6] == "  6" or lines[6].startswith("  ")
    assert any(line.startswith("단계: 키워드 2개 · 붙은 값 ") for line in lines)
    assert any(line.startswith("호출 2번 · ") and "응답 모델 기록 없음" in line for line in lines)
    fp = next(line for line in lines if line.startswith("DB 지문:"))
    assert fp.endswith("· 같음")
    run_dir = shop / lines[-1].removeprefix("실행 폴더: ")
    assert (run_dir / "trace.json").is_file() and (run_dir / "ask.json").is_file()
    assert (run_dir / "calls/1/prompt.txt").is_file() and (run_dir / "calls/2/prompt.txt").is_file()
    rec = json.loads((run_dir / "ask.json").read_text())
    assert rec["database_unchanged"] is True and rec["exit_code"] == 0 and rec["llm"]["backend"] == "fake"
    assert GUARDED == [shop.resolve()]
    assert len(lines) <= 35


def test_rows_capped_at_ten(shop, capsys, monkeypatch):
    fb = FakeBackend({"generate_sql": "SELECT n FROM big ORDER BY n LIMIT 12"})
    monkeypatch.setattr(select, "backend_for", lambda llm: fb)
    code, lines, _ = ask(capsys, "--backend", "fake", "list twelve numbers")
    assert code == 0
    i = lines.index("SELECT n FROM big ORDER BY n LIMIT 12")
    assert lines[i + 1].startswith("실행: rows 12행 · 열 n")
    rows = [line for line in lines[i + 2 :] if line.startswith("  ")]
    assert len(rows) == 10


def test_many_sources_needs_choice(root, capsys):
    assert main(["use", "two"]) == 0
    capsys.readouterr()
    code, _, e = ask(capsys, "--backend", "fake", QUESTION)
    assert code == 2 and "소스를 정해야 합니다" in e and "소스: a, b" in e
    code, _, e = ask(capsys, "--backend", "fake", "--source", "zz", QUESTION)
    assert code == 2 and "소스: a, b" in e


def test_model_unknown(shop, capsys):
    code, lines, e = ask(capsys, QUESTION)
    assert code == 2 and e.strip() == select.QUESTION and lines == []


def test_assets_missing_needs_prep(root, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(select, "backend_for", lambda llm: calls.append(llm) or FakeBackend())
    assert main(["use", "two"]) == 0
    capsys.readouterr()
    code, _, e = ask(capsys, "--backend", "fake", "--source", "a", QUESTION)
    assert code == 5 and "준비 필요: t2s prepare two" in e
    assert calls == []


def test_llm_failure(shop, capsys, monkeypatch):
    monkeypatch.setattr(select, "backend_for", lambda llm: FakeBackend(fail="exit"))
    code, lines, _ = ask(capsys, "--backend", "fake", QUESTION)
    assert code == 1
    assert any(line.startswith("예외: FakeExit: ") for line in lines)
    assert "(생성 없음)" in lines


def test_fake_codex_profile(shop, capsys, monkeypatch, tmp_path):
    exe = make_fake_cli(tmp_path, "codex")
    monkeypatch.setenv("T2S_CODEX_BIN", str(exe))
    monkeypatch.setenv("FAKE_CLI_RESPONSES", json.dumps(['["Apple Juice"]', "SELECT COUNT(*) FROM product"]))
    monkeypatch.setenv("FAKE_CLI_STATE", str(tmp_path / "state"))
    code, lines, _ = ask(capsys, "--profile", "p3-ref", QUESTION)
    assert code == 0
    assert lines[0] == "모델 gpt-5.6-terra · 추론 medium · 백엔드 codex-cli · 출처 대화(p3-ref)"
    assert lines[2].startswith("밖으로 나감(OpenAI): 질문, CREATE 원문")
    assert "SELECT COUNT(*) FROM product" in lines
    calls = next(line for line in lines if line.startswith("호출 "))
    assert calls.startswith("호출 2번 · ") and "입력 200 · 출력 14" in calls and "도구 호출 0" in calls
    run_dir = shop / lines[-1].removeprefix("실행 폴더: ")
    meta = json.loads((run_dir / "calls/2/llm-meta.json").read_text())
    assert meta["backend"] == "codex-cli" and meta["work_dir_files"] == 0
    assert (run_dir / "calls/2/cli-events.jsonl").is_file()
