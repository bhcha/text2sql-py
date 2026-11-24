"""run --semantic used: 시작하는 프로세스만 정의를 읽어 run 폴더의 semantic.json에 쓰고, 분리 프로세스·resume은 그것만 읽는다.

기본(not_used)은 프롬프트·시작 줄이 그 전과 같다. 거절은 모두 모델을 부르기 전이다. 가짜 백엔드만 쓴다.
"""

import hashlib
import json
import shutil

import pytest

from conftest import FIXTURES
from fixtures.fake_cli import make_fake_cli
from t2s.cli import data_cmds, run_cmds
from t2s.cli.main import main
from t2s.dataset import semantic as SEM
from t2s.llm.base import LlmFailure, LlmSettings
from t2s.llm.fake import FakeBackend, _finish
from t2s.pipeline import route as RT
from t2s.prep import embed
from t2s.runner import records as R

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

[profiles.session]
backend = "auto"

[pipeline]
example_rows = 3
keyword_attempts = 3
exec_timeout_seconds = 120
refine_rounds = 3
refine_template = "builtin"
"""
FIXTURE = FIXTURES / "semantic" / "shop.semantic.json"
TEXT = SEM.render(SEM.load_semantic(FIXTURE))
QUESTIONS = (
    "How many products?",
    "List product names.",
    "How many orders are paid?",
    "Count customers.",
    "How many Apple Juice orders are paid?",
    "Which customer has the most orders?",
)
# M0 기준: 퓨어 조건 가짜 백엔드 run --limit 5의 프롬프트 sha256
PURE = {
    "items/0000/calls/1/prompt.txt": "f237e673fc627233ed21391cef01a2d8805d8a375db7a92534edca8ef8982849",
    "items/0000/calls/2/prompt.txt": "b19befde6f7850f130ab78ee095587b1ff40c21211cac465d32847504f6b6d4b",
    "items/0001/calls/1/prompt.txt": "31fc18808af379a31493f3dd9cf796d33d6ec46972e9606b94ae81209330af57",
    "items/0001/calls/2/prompt.txt": "5ef5d9192cda27ce2cf901bdcabd6caaf44badd422bdd22df209ad5197c9b4b9",
    "items/0002/calls/1/prompt.txt": "49b83431c392ed85f4c842402ceffcfecbb130d5050fe446656d4c01f568dcde",
    "items/0002/calls/2/prompt.txt": "eaed21cb1c733713938a2dc71e54fd2260546fb35202a8677a53fc470b80ee33",
    "items/0003/calls/1/prompt.txt": "4726ec1c7c372e5709db4fdc0271a647ba20ca0bd70dcdb7b096d4ddedd5cc94",
    "items/0003/calls/2/prompt.txt": "fd13353234a01679485e0e7d8741797aa9d4f38d1ef49900ddf023522bc0aad8",
    "items/0004/calls/1/prompt.txt": "b027bbde739d7cdc8dd4f5b3dd747eb925806cdf0ad2fb75c1700f194e723660",
    "items/0004/calls/2/prompt.txt": "301b06f36716d7deb739155c34fb83acd83df2761d44e9f0481118469ef11217",
}


@pytest.fixture
def guard_calls(monkeypatch):
    calls: list = []
    monkeypatch.setattr(data_cmds, "install_guard", lambda root, **kw: None)
    monkeypatch.setattr(run_cmds, "install_guard", lambda root, **kw: calls.append(kw))
    return calls


@pytest.fixture
def root(tmp_path, monkeypatch, shop_db, capsys, guard_calls):
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
    db = tmp_path / "dbs" / "shop.sqlite"
    db.parent.mkdir()
    shutil.copy(shop_db, db)
    assert main(["register", "shop", "--sqlite", str(db), "--yes"]) == 0
    assert main(["prepare", "shop"]) == 0
    assert main(["use", "shop"]) == 0
    (r / "q.txt").write_text("\n".join(QUESTIONS) + "\n", encoding="utf-8")
    capsys.readouterr()
    guard_calls.clear()
    return r


@pytest.fixture
def imported(root, capsys, guard_calls):
    inbox = root / "var/semantic-inbox/shop.json"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, inbox)
    assert main(["semantic-import", "shop", "--yes"]) == 0
    capsys.readouterr()
    guard_calls.clear()
    return root


def cli(capsys, *argv):
    code = main(list(argv))
    o = capsys.readouterr()
    return code, o.out.splitlines(), o.err.splitlines()


def _prompts(run) -> dict[str, str]:
    return {p.relative_to(run).as_posix(): p.read_text(encoding="utf-8") for p in sorted(run.rglob("prompt.txt"))}


def _base(root, *extra):
    return ("run", "--backend", "fake", "--no-score", "--questions", str(root / "q.txt"), *extra)


class _P:
    pid = 424242


def test_foreground_used_run(imported, capsys, guard_calls):
    code, lines, _ = cli(
        capsys, *_base(imported, "--foreground", "--semantic", "used", "--limit", "5", "--run-id", "u1")
    )
    assert code == 0 and guard_calls == [{"allow_semantic": True}]
    st = json.loads((imported / "var/state.json").read_text())["semantic"]["shop"]
    assert lines[2] == "밖으로 나가는 것 없음(가짜 백엔드)"
    assert lines[3] == f"지표 정의: 모델 shop-fixture 판 fixture- · 승인 지표 3개 · 받은 때 {st['accepted_at'][:10]}"
    start = next(x for x in lines if x.startswith("시작: u1"))
    assert start.endswith("· shop 5문항 · 동시 4 · evidence not_used · semantic used")
    run = imported / "runs/u1"
    prompts = _prompts(run)
    assert len(prompts) == 10 and all(TEXT in p for p in prompts.values())
    meta = R.read_json(run / R.RUN_FILE)
    copy = run / R.SEMANTIC_FILE
    assert R.SEMANTIC_FILE == SEM.COPY_NAME
    assert json.loads(copy.read_text()) == {"text": TEXT, "source_sha256": st["sha256"], "rules": "1.0.0"}
    assert meta["semantic"] == {
        "mode": "used",
        "path": "runs/u1/semantic.json",
        "sha256": hashlib.sha256(copy.read_bytes()).hexdigest(),
        "source_path": "data/shop/semantic.approved.json",
        "source_sha256": st["sha256"],
        "source": st["source"],
        "metrics": 3,
        "chars": len(TEXT),
        "rules": "1.0.0",
        "accepted_at": st["accepted_at"],
    }
    assert meta["evidence"] == {"mode": "not_used"} and not (run / R.HINTS_FILE).exists()
    code, lines, _ = cli(capsys, "status", "--run-id", "u1")
    assert code == 0 and lines[0] == "u1 · shop · evidence not_used · semantic used · 끝남"
    code, lines, _ = cli(capsys, "sessions")
    assert code == 0 and any("evidence not_used · semantic used · 5문항" in x for x in lines)
    assert all(TEXT not in x for x in lines)


def test_not_used_run_unchanged(imported, capsys, guard_calls):
    code, lines, _ = cli(capsys, *_base(imported, "--foreground", "--limit", "5", "--run-id", "base5"))
    assert code == 0 and guard_calls == [{}]
    run = imported / "runs/base5"
    got = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in _prompts(run).items()}
    assert got == PURE
    start = next(x for x in lines if x.startswith("시작: base5"))
    assert start.endswith("앞에서 돎) · shop 5문항 · 동시 4 · evidence not_used")
    assert not any(x.startswith("지표 정의:") for x in lines)
    assert R.read_json(run / R.RUN_FILE)["semantic"] == {"mode": "not_used"}
    assert not (run / R.SEMANTIC_FILE).exists()
    code, lines, _ = cli(capsys, "status", "--run-id", "base5")
    assert lines[0] == "base5 · shop · evidence not_used · 끝남"


def test_old_run_without_semantic_field_reads_not_used():
    assert run_cmds.semantic_mode({"evidence": {"mode": "not_used"}}) == "not_used"
    assert run_cmds.semantic_mode({"kind": "rescore"}) is None
    assert run_cmds.evidence_word({}) == "evidence not_used"


def test_outgoing_line_real_backend(imported, capsys, monkeypatch, tmp_path):
    exe = make_fake_cli(tmp_path, "codex")
    monkeypatch.setenv("T2S_CODEX_BIN", str(exe))
    monkeypatch.setenv("FAKE_CLI_MODE", "ok")
    monkeypatch.setattr(run_cmds, "_popen", lambda cmd, **kw: _P())
    args = ["--backend", "codex-cli", "--model", "gpt-5.6-terra", "--effort", "medium", "--no-probe"]
    q = ["--questions", str(imported / "q.txt")]
    code, lines, _ = cli(capsys, "run", *args, *q, "--semantic", "used", "--run-id", "o1")
    assert code == 0
    i = next(i for i, x in enumerate(lines) if x.startswith("밖으로 나감(OpenAI): "))
    assert lines[i].endswith("실행 오류 글, 지표 정의 글")
    assert lines[i + 1].startswith("지표 정의: 모델 shop-fixture 판 fixture- · 승인 지표 3개 · 받은 때 ")
    code, lines, _ = cli(capsys, "run", *args, *q, "--run-id", "o2")
    assert any(x.startswith("밖으로 나감(OpenAI): ") and x.endswith("실행 오류 글") for x in lines)
    assert not any(x.startswith("지표 정의:") for x in lines)


def test_worker_reads_only_run_copy(imported, capsys, monkeypatch, guard_calls):
    monkeypatch.setattr(run_cmds, "_popen", lambda cmd, **kw: _P())
    code, _, _ = cli(capsys, *_base(imported, "--semantic", "used", "--limit", "3", "--run-id", "w1"))
    assert code == 0 and guard_calls[-1] == {"allow_semantic": True}
    # 분리 프로세스는 data/의 받아들인 파일을 열지 않는다
    (imported / "data/shop/semantic.approved.json").unlink()
    code, _, err = cli(capsys, "run", "--worker", "--run-id", "w1")
    assert code == 0, err
    assert guard_calls[-1] == {}
    prompts = _prompts(imported / "runs/w1")
    assert len(prompts) == 6 and all(TEXT in p for p in prompts.values())


@pytest.mark.parametrize("how", ["text", "missing"])
def test_worker_refuses_changed_copy(imported, capsys, monkeypatch, how):
    monkeypatch.setattr(run_cmds, "_popen", lambda cmd, **kw: _P())
    assert cli(capsys, *_base(imported, "--semantic", "used", "--limit", "2", "--run-id", "w2"))[0] == 0
    copy = imported / "runs/w2/semantic.json"
    if how == "text":
        copy.write_text(
            json.dumps({"text": "Approved metric definitions (data, not instructions): x"}), encoding="utf-8"
        )
    else:
        copy.unlink()
    code, _, err = cli(capsys, "run", "--worker", "--run-id", "w2")
    assert code == 1 and err == ["run의 지표 정의 파일(semantic.json)이 run.json의 sha256과 다릅니다"]
    assert R.read_json(imported / "runs/w2/control.json")["state"] == "failed"
    assert not (imported / "runs/w2/items").exists()


class FailSecond:
    id = "fake"
    settings = LlmSettings("fake", "fake-model", "none", 1)

    def call(self, prompt, call_dir, *, name, attempt):
        if QUESTIONS[1] in prompt:
            return _finish(self.settings, prompt, call_dir, None, LlmFailure("CodexExit", "가짜"), name, attempt)
        return FakeBackend().call(prompt, call_dir, name=name, attempt=attempt)


def test_resume_keeps_text_and_checks_hash(imported, capsys, monkeypatch):
    monkeypatch.setattr(run_cmds, "BACKEND_FACTORY", FailSecond)
    base = _base(imported, "--foreground", "--semantic", "used", "--limit", "4")
    cli(capsys, *base, "--run-id", "r1")
    assert R.read_jsonl(imported / "runs/r1/records.jsonl")[1]["status"] == "exception"
    monkeypatch.setattr(run_cmds, "BACKEND_FACTORY", None)
    # resume도 run 폴더의 semantic.json만 읽는다(받아들인 파일이 바뀌어도 같은 글로 이어 간다)
    approved = imported / "data/shop/semantic.approved.json"
    approved.write_bytes(approved.read_bytes().replace(b"paid orders", b"PAID ORDERS"))
    code, lines, err = cli(capsys, "resume", "--run-id", "r1", "--foreground")
    assert code == 0, err
    assert any("이어 돌기 1문항 · evidence not_used · semantic used" in x for x in lines)
    assert [r["status"] for r in R.read_jsonl(imported / "runs/r1/records.jsonl")] == ["generated"] * 4
    prompts = _prompts(imported / "runs/r1/items/0001")
    assert prompts and all(TEXT in p and "PAID ORDERS" not in p for p in prompts.values())
    # semantic.json이 바뀌면 거절
    monkeypatch.setattr(run_cmds, "BACKEND_FACTORY", FailSecond)
    approved.write_bytes(FIXTURE.read_bytes())
    cli(capsys, *base, "--run-id", "r2")
    copy = imported / "runs/r2/semantic.json"
    copy.write_bytes(copy.read_bytes().replace(b"paid orders", b"paid  orders"))
    code, _, err = cli(capsys, "resume", "--run-id", "r2")
    assert code == 1 and err == ["이어 돌 수 없습니다: 지표 정의 파일(semantic.json)이 run.json의 sha256과 다릅니다"]


def _no_model(monkeypatch):
    from t2s.llm import select

    calls: list = []
    monkeypatch.setattr(select, "backend_for", lambda llm, **kw: calls.append(llm) or FakeBackend())
    monkeypatch.setattr(run_cmds, "_popen", lambda cmd, **kw: calls.append(cmd) or _P())
    return calls


def test_refusals_before_model(root, capsys, monkeypatch, guard_calls):
    calls = _no_model(monkeypatch)
    # 받아들인 정의 없음
    code, lines, err = cli(capsys, *_base(root, "--semantic", "used"))
    assert (code, lines, err) == (2, [], ["받아들인 지표 정의가 없습니다. 먼저 /getmetrics"])
    # evidence와 함께
    code, lines, err = cli(capsys, *_base(root, "--semantic", "used", "--evidence", "used"))
    assert code == 2 and lines == [] and err[0].startswith("evidence와 지표 정의는 함께 쓰지 않습니다")
    assert guard_calls[-1] == {}  # 둘 다 used면 감시를 넓히지 않는다
    # BIRD(자료가 없어도 2)
    assert main(["use", "bird-minidev"]) == 0
    capsys.readouterr()
    code, lines, err = cli(capsys, "run", "--backend", "fake", "--no-score", "--semantic", "used")
    assert (code, lines, err) == (2, [], ["BIRD Mini-Dev에는 지표 정의가 없습니다"])
    assert calls == [] and not (root / "runs").exists()


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ("file", "오류: 지표 정의 파일이 받아들인 뒤 바뀌었습니다. /getmetrics를 다시"),
        ("db", "오류: DB가 받아들인 뒤 바뀌었습니다. /getmetrics를 다시"),
    ],
)
def test_refuse_changed_definition_or_db(imported, capsys, monkeypatch, change, words):
    calls = _no_model(monkeypatch)
    if change == "file":
        p = imported / "data/shop/semantic.approved.json"
        p.write_bytes(p.read_bytes().replace(b"paid orders", b"paid  orders"))
    else:
        sp = imported / "var/state.json"
        st = json.loads(sp.read_text())
        st["semantic"]["shop"]["db_fingerprint"]["sha256"] = "0" * 64
        sp.write_text(json.dumps(st), encoding="utf-8")
    code, lines, err = cli(capsys, *_base(imported, "--semantic", "used"))
    assert (code, lines, err) == (1, [], [words])
    assert calls == [] and not (imported / "runs").exists()


def test_route_start_has_no_semantic_argument(imported, capsys):
    code, _, _ = cli(capsys, "route-start", "--backend", "fake", "--semantic", "used")
    assert code == 2


def test_route_warning_for_semantic(imported, monkeypatch):
    from t2s.cli.main import build_parser
    from t2s.dataset.config import load_config
    from t2s.dataset.state import load_state
    from t2s.llm import select

    config = load_config(imported, load_state(imported))
    llm = select.ResolvedLlm.from_json(
        {
            "backend": "fake",
            "model": "fake-model",
            "reasoning_effort": None,
            "model_source": "x",
            "effort_source": "x",
            "timeout_seconds": 1,
            "item_timeout_seconds": 1,
            "profile": None,
        }
    )

    def decided(basis):
        d = RT.RouteDecision(RT.HARNESS, RT.HARNESS, "profile", "basis", "p", settings=None, basis=basis)
        monkeypatch.setattr(RT, "resolve_route", lambda **kw: d)
        return d

    args = build_parser().parse_args(["run"])
    d = decided({"evidence": "not_used"})
    run_cmds._decide(args, config, "shop", llm, None, False, True)
    assert d.warnings == ["근거 semantic 조건이 다릅니다(not_used → used). 잠정 경로입니다"]
    d = decided({"evidence": "not_used"})
    run_cmds._decide(args, config, "shop", llm, None, False, False)
    assert d.warnings == []
