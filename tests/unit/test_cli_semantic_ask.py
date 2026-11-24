"""ask --semantic used: 받아들인 지표 정의의 한 줄 글이 힌트 자리 셋(단일 호출은 이름표 줄)에 글자 그대로 들어간다.

기본(not_used)은 프롬프트가 그 전과 같다(M0 기준 sha256). 거절은 모두 모델을 부르기 전이다. 가짜 백엔드만 쓴다.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import FIXTURES
from fixtures.fake_cli import make_fake_cli
from t2s.cli import ask_cmd, data_cmds
from t2s.cli.main import main
from t2s.dataset import semantic as SEM
from t2s.llm import select
from t2s.llm.fake import FakeBackend
from t2s.pipeline import route as RT
from t2s.pipeline.run import PipelineSettings
from t2s.prep import embed

TOML = """
[defaults]
profile = "session"

[paths]
data = "data"
runs = "runs"
embedding_model = "data/models/Xenova/all-mpnet-base-v2"

[datasets.bird-minidev]
kind = "bird-minidev"
root = "data/bird-minidev/extracted/minidev/MINIDEV"
databases = "dev_databases"
questions = "data/bird-minidev/questions.public.json"

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
FIXTURE = FIXTURES / "semantic" / "shop.semantic.json"
TEXT = SEM.render(SEM.load_semantic(FIXTURE))
# M0 기준: 퓨어 조건 가짜 백엔드 ask의 프롬프트 sha256(tests/unit/test_prompt_regression.py와 같은 값)
PURE = {
    "calls/1/prompt.txt": "b027bbde739d7cdc8dd4f5b3dd747eb925806cdf0ad2fb75c1700f194e723660",
    "calls/2/prompt.txt": "301b06f36716d7deb739155c34fb83acd83df2761d44e9f0481118469ef11217",
}
GUARDED: list = []


@pytest.fixture
def root(tmp_path, monkeypatch, shop_db, capsys):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "harness.toml").write_text(TOML, encoding="utf-8")
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
    monkeypatch.setattr(data_cmds, "install_guard", lambda root, **kw: None)
    GUARDED.clear()
    monkeypatch.setattr(ask_cmd, "install_guard", lambda root, **kw: GUARDED.append(kw))
    db = tmp_path / "dbs" / "shop.sqlite"
    db.parent.mkdir()
    shutil.copy(shop_db, db)
    assert main(["register", "shop", "--sqlite", str(db), "--yes"]) == 0
    assert main(["prepare", "shop"]) == 0
    assert main(["use", "shop"]) == 0
    capsys.readouterr()
    return r


@pytest.fixture
def imported(root, capsys):
    inbox = root / "var/semantic-inbox/shop.json"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, inbox)
    assert main(["semantic-import", "shop", "--yes"]) == 0
    capsys.readouterr()
    GUARDED.clear()
    return root


def ask(capsys, *argv):
    code = main(["ask", *argv])
    o = capsys.readouterr()
    return code, o.out.splitlines(), o.err.splitlines()


def _run_dir(root, lines):
    return root / lines[-1].removeprefix("실행 폴더: ")


def _prompts(run_dir) -> dict[str, str]:
    return {
        p.relative_to(run_dir).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted((run_dir / "calls").rglob("prompt.txt"))
    }


def test_used_text_in_all_three_slots(imported, capsys, monkeypatch):
    fb = FakeBackend({"generate_sql": "SELECT * FROM nope", "refine_sql": "```sql\nSELECT * FROM nope2\n```"})
    monkeypatch.setattr(select, "backend_for", lambda llm, **kw: fb)
    code, lines, _ = ask(capsys, "--backend", "fake", "--semantic", "used", QUESTION)
    names = [c["name"] for c in fb.calls]
    assert names[:2] == ["extract_keywords", "generate_sql"] and "refine_sql" in names
    for c in fb.calls:
        assert TEXT in c["prompt"], c["name"]
    gen = next(c["prompt"] for c in fb.calls if c["name"] == "generate_sql")
    assert f"-- External Knowledge: {TEXT}\n" in gen
    kw = next(c["prompt"] for c in fb.calls if c["name"] == "extract_keywords")
    assert f"Hint: {TEXT}\n" in kw
    assert GUARDED == [{"allow_semantic": True}]
    run_dir = _run_dir(imported, lines)
    rec = json.loads((run_dir / "ask.json").read_text())
    copy = run_dir / "semantic.json"
    st = json.loads((imported / "var/state.json").read_text())["semantic"]["shop"]
    assert json.loads(copy.read_text()) == {"text": TEXT, "source_sha256": st["sha256"], "rules": "1.0.0"}
    assert rec["semantic"] == {
        "mode": "used",
        "path": os.path.relpath(copy, imported),
        "sha256": hashlib.sha256(copy.read_bytes()).hexdigest(),
        "source_path": "data/shop/semantic.approved.json",
        "source_sha256": st["sha256"],
        "source": st["source"],
        "metrics": 3,
        "chars": len(TEXT),
        "rules": "1.0.0",
    }
    assert code in (0, 1)


def test_used_screen_lines_fake(imported, capsys):
    code, lines, _ = ask(capsys, "--backend", "fake", "--semantic", "used", QUESTION)
    assert code == 0
    assert lines[2] == "밖으로 나가는 것 없음(가짜 백엔드)"
    st = json.loads((imported / "var/state.json").read_text())["semantic"]["shop"]
    assert lines[3] == f"지표 정의: 모델 shop-fixture 판 fixture- · 승인 지표 3개 · 받은 때 {st['accepted_at'][:10]}"
    prompts = _prompts(_run_dir(imported, lines))
    assert len(prompts) == 2 and all(TEXT in p for p in prompts.values())
    assert all(TEXT not in line for line in lines)  # 정의 본문은 화면에 내지 않는다


def test_used_outgoing_line_real_backend(imported, capsys, monkeypatch, tmp_path):
    exe = make_fake_cli(tmp_path, "codex")
    monkeypatch.setenv("T2S_CODEX_BIN", str(exe))
    monkeypatch.setenv("FAKE_CLI_RESPONSES", json.dumps(['["Apple Juice"]', "SELECT COUNT(*) FROM product"]))
    monkeypatch.setenv("FAKE_CLI_STATE", str(tmp_path / "state"))
    code, lines, _ = ask(capsys, "--profile", "p3-ref", "--semantic", "used", QUESTION)
    assert code == 0
    assert lines[2] == (
        "밖으로 나감(OpenAI): 질문, CREATE 원문·예시 3행, 질문과 비슷한 값, 열 프로파일, 실행 오류 글, 지표 정의 글"
    )
    assert lines[3].startswith("지표 정의: 모델 shop-fixture 판 fixture- · 승인 지표 3개 · 받은 때 ")


def test_used_single_route(imported, capsys, monkeypatch):
    decision = RT.RouteDecision(RT.SINGLE, RT.SINGLE, "forced", "forced", None, settings=PipelineSettings())
    monkeypatch.setattr(RT, "resolve_route", lambda **kw: decision)
    fb = FakeBackend({"single_sql": "SELECT COUNT(*) FROM product"})
    monkeypatch.setattr(select, "backend_for", lambda llm, **kw: fb)
    code, _, _ = ask(capsys, "--backend", "fake", "--semantic", "used", QUESTION)
    assert code == 0
    [call] = fb.calls
    assert f"\nExternal Knowledge:\n{TEXT}\n" in call["prompt"]
    assert (
        ask_cmd.outgoing_line("codex-cli", RT.SINGLE, semantic=True)
        == "밖으로 나감(OpenAI): 질문, CREATE 원문, 지표 정의 글"
    )


def test_not_used_unchanged(imported, capsys):
    code, lines, _ = ask(capsys, "--backend", "fake", QUESTION)
    assert code == 0 and GUARDED == [{}]
    run_dir = _run_dir(imported, lines)
    prompts = _prompts(run_dir)
    assert {k: hashlib.sha256(v.encode()).hexdigest() for k, v in prompts.items()} == PURE
    assert all(TEXT not in p and "Approved metric definitions" not in p for p in prompts.values())
    assert not any(line.startswith("지표 정의:") for line in lines)
    assert lines[2] == "밖으로 나가는 것 없음(가짜 백엔드)"
    assert json.loads((run_dir / "ask.json").read_text())["semantic"] == {"mode": "not_used"}
    assert sorted(p.name for p in run_dir.iterdir()) == ["ask.json", "calls", "trace.json"]
    assert ask_cmd.outgoing_line("codex-cli") == (
        "밖으로 나감(OpenAI): 질문, CREATE 원문·예시 3행, 질문과 비슷한 값, 열 프로파일, 실행 오류 글"
    )


def _no_model(monkeypatch):
    calls: list = []
    monkeypatch.setattr(select, "backend_for", lambda llm, **kw: calls.append(llm) or FakeBackend())
    return calls


def test_reject_no_definition_and_bird(root, capsys, monkeypatch):
    calls = _no_model(monkeypatch)
    code, lines, errs = ask(capsys, "--backend", "fake", "--semantic", "used", QUESTION)
    assert (code, lines, errs) == (2, [], ["받아들인 지표 정의가 없습니다. 먼저 /getmetrics"])
    assert main(["use", "bird-minidev"]) == 0
    capsys.readouterr()
    code, lines, errs = ask(capsys, "--backend", "fake", "--semantic", "used", QUESTION)
    assert (code, lines, errs) == (2, [], ["BIRD Mini-Dev에는 지표 정의가 없습니다"])
    assert calls == [] and not (root / "runs").exists()


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ("file", "오류: 지표 정의 파일이 받아들인 뒤 바뀌었습니다. /getmetrics를 다시"),
        ("missing", "오류: 받아들인 지표 정의 파일이 없습니다. /getmetrics를 다시"),
        ("db", "오류: DB가 받아들인 뒤 바뀌었습니다. /getmetrics를 다시"),
        ("rules", "오류: 지표 정의 글의 규칙 판이 받아들인 때와 다릅니다. /getmetrics를 다시"),
        ("shape", "오류: 정의 파일의 dialect가 sqlite가 아닙니다"),
    ],
)
def test_reject_changed(imported, capsys, monkeypatch, change, words):
    calls = _no_model(monkeypatch)
    approved = imported / "data/shop/semantic.approved.json"
    state_path = imported / "var/state.json"
    st = json.loads(state_path.read_text())
    if change == "file":
        approved.write_bytes(approved.read_bytes().replace(b"paid orders", b"paid  orders"))
    elif change == "missing":
        approved.unlink()
    elif change == "db":
        st["semantic"]["shop"]["db_fingerprint"]["sha256"] = "0" * 64
    elif change == "rules":
        st["semantic"]["shop"]["rules"] = "0.9.0"
    else:  # sha256까지 맞춘, 꼴이 틀린 파일
        raw = approved.read_bytes().replace(b'"dialect": "sqlite"', b'"dialect": "postgres"')
        approved.write_bytes(raw)
        st["semantic"]["shop"]["sha256"] = hashlib.sha256(raw).hexdigest()
    state_path.write_text(json.dumps(st), encoding="utf-8")
    code, lines, errs = ask(capsys, "--backend", "fake", "--semantic", "used", QUESTION)
    assert (code, lines, errs) == (1, [], [words])
    assert calls == [] and not (imported / "runs").exists()


def test_guard_blocks_definition_unless_used(imported, tmp_path):
    """감시를 실제로 건 하위 프로세스: ask(기본)는 정의 파일을 열지 못하고, --semantic used면 연다."""
    script = (
        "import sys\n"
        "from t2s.cli import ask_cmd\n"
        "from t2s.cli.main import build_parser\n"
        "from t2s.llm import select\n"
        "def probe(*a, **k):\n"
        "    try:\n"
        "        open(sys.argv[1] + '/data/shop/semantic.approved.json').read(); print('ok')\n"
        "    except Exception as e:\n"
        "        print(type(e).__name__)\n"
        "    raise select.ModelUnknown('stop')\n"
        "select.select_from_args = probe\n"
        "sys.exit(ask_cmd.run(build_parser().parse_args(['ask', *sys.argv[2:], 'q'])))\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("T2S_")}
    env["T2S_ROOT"] = str(imported)
    got = {}
    for mode in ("not_used", "used"):
        p = subprocess.run(
            [sys.executable, "-c", script, str(imported), "--semantic", mode],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        got[mode] = (p.returncode, p.stdout.strip())
    assert got == {"not_used": (2, "GoldAccessBlocked"), "used": (2, "ok")}


def test_route_warning_when_basis_semantic_differs(imported, capsys, monkeypatch):
    """not_used로 잰 근거(semantic 칸 없음)의 프로필 경로를 ask --semantic used가 따르면 잠정 경로 경고가 붙는다."""

    def decided(**kw):
        basis = {"route": RT.HARNESS, "evidence": "not_used"}
        return RT.RouteDecision(RT.HARNESS, RT.HARNESS, "profile", "basis", "p", basis=basis, settings=None)

    monkeypatch.setattr(RT, "resolve_route", decided)
    warning = "근거 semantic 조건이 다릅니다(not_used → used). 잠정 경로입니다"
    code, lines, _ = ask(capsys, "--backend", "fake", "--semantic", "used", QUESTION)
    assert code == 0
    rec = json.loads((_run_dir(imported, lines) / "ask.json").read_text())
    assert warning in rec["route"]["warnings"]
    code, lines, _ = ask(capsys, "--backend", "fake", QUESTION)
    assert code == 0
    rec = json.loads((_run_dir(imported, lines) / "ask.json").read_text())
    assert all("근거 semantic 조건" not in w for w in rec["route"]["warnings"])
    assert all("근거 semantic 조건" not in line for line in lines)
