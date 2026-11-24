"""명령 semantic-check·semantic-import·semantic-remove와, 그에 따른 datasets·register --remove의 동작.

시험용 정의 파일(fixtures/semantic/shop.semantic.json)을 받기 자리에 손으로 놓는다. 네트워크도 모델 호출도 없다.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys

import pytest

from conftest import FIXTURES
from t2s.cli import data_cmds
from t2s.cli.main import main
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
"""
FIXTURE = FIXTURES / "semantic" / "shop.semantic.json"
# 정의 본문의 글자(출력 어디에도 나오면 안 된다)
BODY_WORDS = ("Number of orders whose status is paid", "settled orders", "COUNT(DISTINCT customer_id)", "WHERE price")
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
    monkeypatch.setenv("T2S_ROOT", str(r))
    GUARDED.clear()
    monkeypatch.setattr(data_cmds, "install_guard", lambda root, **kw: GUARDED.append(kw))
    assert main(["register", "shop", "--sqlite", str(shop_db), "--yes"]) == 0
    capsys.readouterr()
    GUARDED.clear()
    return r


def _inbox(root, name="shop", doc=None) -> bytes:
    p = root / "var/semantic-inbox" / f"{name}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = FIXTURE.read_bytes() if doc is None else json.dumps(doc, ensure_ascii=False).encode()
    p.write_bytes(raw)
    return raw


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _state(root) -> dict:
    return json.loads((root / "var/state.json").read_text(encoding="utf-8"))


def call(capsys, *argv):
    code = main(list(argv))
    o = capsys.readouterr()
    for w in BODY_WORDS:
        assert w not in o.out and w not in o.err, w
    return code, o.out.splitlines(), o.err.splitlines()


def test_import_notice_then_yes(root, capsys):
    raw = _inbox(root)
    code, lines, _ = call(capsys, "semantic-import", "shop")
    assert code == 3
    assert lines == [
        (
            "받아들일 지표 정의: 모델 shop-fixture (판 fixture-, 승인 지표 3개, 뺀 것: 승인 전 지표 0개·"
            "승인 전 연결을 기다리는 지표 0개)"
        ),
        (
            "semantic used로 돌릴 때 이 정의 글(지표 이름·설명·동의어·쓰임 안내·기준 SQL, 608자)이 "
            "모델 제공자 서버(OpenAI 또는 Anthropic)로 나갑니다."
        ),
        "말하지 않으면 쓰지 않습니다(기본은 semantic not_used).",
        "밖으로 내보내면 안 되는 내용이 든 정의에는 쓰지 마세요.",
        "승인하면 --yes를 붙여 다시 실행: t2s semantic-import shop --yes",
    ]
    assert not (root / "data/shop/semantic.approved.json").exists()
    assert "semantic" not in _state(root)
    assert (root / "var/semantic-inbox/shop.json").is_file()
    assert GUARDED == [{"allow_semantic": True}]

    code, lines, _ = call(capsys, "semantic-import", "shop", "--yes")
    sha = hashlib.sha256(raw).hexdigest()
    assert code == 0
    assert lines == [f"받아들임: shop (모델 shop-fixture 판 fixture-, 승인 지표 3개, sha256 {sha[:8]})"]
    dest = root / "data/shop/semantic.approved.json"
    assert dest.read_bytes() == raw
    assert not (root / "var/semantic-inbox/shop.json").exists()
    assert sorted(os.listdir(root / "var/semantic-inbox")) == []
    rec = _state(root)["semantic"]["shop"]
    db_sha = _state(root)["registered"]["shop"]["fingerprint"]["sha256"]
    assert rec["path"] == "data/shop/semantic.approved.json" and rec["sha256"] == sha and rec["size"] == len(raw)
    assert rec["format"] == "t2s-semantic/1" and rec["source"] == _fixture()["source"]
    assert (rec["metrics"], rec["chars"], rec["rules"]) == (3, 608, "1.0.0")
    assert rec["db_fingerprint"]["sha256"] == db_sha and rec["accepted_at"].endswith("Z")

    code, lines, _ = call(capsys, "datasets")
    assert code == 0
    i = next(i for i, line in enumerate(lines) if "shop  single" in line)
    day = rec["accepted_at"][:10]
    assert lines[i + 1] == f"    지표 정의: 모델 shop-fixture 판 fixture- · 승인 지표 3개 · 받은 때 {day}"
    assert sum("지표 정의:" in line for line in lines) == 1


def test_reimport_changes_sha_and_shows_old(root, capsys):
    raw = _inbox(root)
    assert call(capsys, "semantic-import", "shop", "--yes")[0] == 0
    doc = _fixture()
    doc["source"]["version_id"] = "fixture-shop-v2"
    raw2 = _inbox(root, doc=doc)
    code, lines, _ = call(capsys, "semantic-import", "shop", "--yes")
    old, new = hashlib.sha256(raw).hexdigest(), hashlib.sha256(raw2).hexdigest()
    assert code == 0 and old != new
    assert lines == [
        f"받아들임: shop (모델 shop-fixture 판 fixture-, 승인 지표 3개, sha256 {new[:8]})",
        f"바꿈: 이전 정의 sha256 {old[:8]}",
    ]
    assert _state(root)["semantic"]["shop"]["sha256"] == new
    assert (root / "data/shop/semantic.approved.json").read_bytes() == raw2


@pytest.mark.parametrize("command", ["semantic-import", "semantic-check"])
def test_unapproved_item_rejected_nothing_moved(root, capsys, command):
    doc = _fixture()
    doc["metrics"][1]["status"] = "draft"
    _inbox(root, doc=doc)
    code, lines, errs = call(capsys, command, "shop", *(["--yes"] if command == "semantic-import" else []))
    assert code == 1 and lines == []
    assert errs == ["오류: 승인 전 항목이 들어 있어 받아들이지 않습니다(지표 1번)"]
    assert not (root / "data/shop/semantic.approved.json").exists()
    assert "semantic" not in _state(root)
    # check는 성공이든 실패든 받기 자리를 비운다. import는 실패하면 그대로 둔다
    assert (root / "var/semantic-inbox/shop.json").exists() is (command == "semantic-import")


def test_check_matches_and_clears_inbox(root, capsys):
    _inbox(root)
    code, lines, _ = call(capsys, "semantic-check", "shop")
    assert code == 0
    assert lines == [
        (
            "이 DB에 맞는 활성 지표 모델이 있습니다: shop-fixture (판 fixture-, 승인 지표 3개). "
            "지표 질문은 /ask로 물으세요. /text2sql과 /runset의 SQL 생성에는 이 정의를 넣지 않습니다."
        )
    ]
    assert not (root / "var/semantic-inbox/shop.json").exists()
    assert not (root / "data/shop/semantic.approved.json").exists() and "semantic" not in _state(root)
    assert GUARDED == [{"allow_semantic": True}]


def test_check_zero_metrics(root, capsys):
    doc = _fixture()
    doc["metrics"] = []
    _inbox(root, doc=doc)
    code, lines, _ = call(capsys, "semantic-check", "shop")
    assert code == 0 and "(판 fixture-, 승인 지표 0개)" in lines[0]
    _inbox(root, doc=doc)
    code, _, errs = call(capsys, "semantic-import", "shop")
    assert code == 1 and errs == ["오류: 승인 지표가 없습니다"]


def test_missing_tables_check_tells_import_refuses(root, capsys):
    doc = _fixture()
    doc["tables"] = ["order", "warehouse", "region"]
    _inbox(root, doc=doc)
    code, lines, _ = call(capsys, "semantic-check", "shop")
    assert code == 0
    assert lines == [
        "활성 지표 모델 shop-fixture은 이 DB와 맞지 않습니다(모델의 테이블 3개 가운데 2개가 이 DB에 없음)."
    ]
    _inbox(root, doc=doc)
    code, lines, errs = call(capsys, "semantic-import", "shop", "--yes")
    assert code == 1 and lines == []
    assert errs == ["정의의 테이블 3개 가운데 2개가 이 DB에 없어 받아들이지 않습니다"]
    assert not (root / "data/shop/semantic.approved.json").exists()


def test_no_inbox_file(root, capsys):
    for command in ("semantic-check", "semantic-import"):
        code, _, errs = call(capsys, command, "shop")
        assert code == 1 and errs == ["받기 자리에 지표 정의 파일이 없습니다: var/semantic-inbox/shop.json"]


@pytest.mark.parametrize(
    ("name", "code", "words"),
    [
        ("bird-minidev", 2, "BIRD Mini-Dev에는 지표 정의가 없습니다"),
        ("nope", 2, "없는 데이터셋: nope"),
        ("../x", 2, "데이터셋 이름 꼴이 아닙니다"),
    ],
)
def test_not_a_single_sqlite_dataset(root, capsys, name, code, words):
    _inbox(root, "bird-minidev")
    for command in ("semantic-check", "semantic-import"):
        got, lines, errs = call(capsys, command, name)
        assert got == code and lines == [] and words in errs[0], command
    assert not (root / "data/bird-minidev/semantic.approved.json").exists()


def test_check_bad_json_clears_inbox(root, capsys):
    p = root / "var/semantic-inbox/shop.json"
    p.parent.mkdir(parents=True)
    p.write_text('{"format": "t2s-semantic/1", "SECRETVALUE": 1', encoding="utf-8")
    code, _, errs = call(capsys, "semantic-check", "shop")
    assert code == 1 and errs[0].startswith("오류: 정의 파일을 JSON으로 읽지 못했습니다")
    assert "SECRETVALUE" not in "\n".join(errs) and not p.exists()


def test_semantic_remove(root, capsys):
    _inbox(root)
    assert call(capsys, "semantic-remove", "shop", "--inbox") == (0, ["지움: 받기 자리의 지표 정의 파일(shop)"], [])
    assert call(capsys, "semantic-remove", "shop", "--inbox") == (0, ["받기 자리에 지울 파일이 없습니다(shop)"], [])
    assert call(capsys, "semantic-remove", "shop") == (1, [], ["받아들인 지표 정의가 없습니다: shop"])
    _inbox(root)
    assert call(capsys, "semantic-import", "shop", "--yes")[0] == 0
    GUARDED.clear()
    assert call(capsys, "semantic-remove", "shop") == (0, ["지움: shop의 지표 정의(받아들인 파일과 고정 기록)"], [])
    assert GUARDED == [{}]  # 지우기만 하므로 기본 감시
    assert not (root / "data/shop/semantic.approved.json").exists() and "semantic" not in _state(root)


def test_register_remove_also_removes_definition(root, capsys):
    _inbox(root)
    assert call(capsys, "semantic-import", "shop", "--yes")[0] == 0
    _inbox(root)  # 받기 자리에 남은 것도 함께 지운다
    code, lines, _ = call(capsys, "register", "shop", "--remove")
    assert code == 0
    assert lines == ["지움: shop (등록 기록과 자산)", "지움: shop의 지표 정의(받아들인 파일과 고정 기록)"]
    assert not (root / "data/shop/semantic.approved.json").exists()
    assert not (root / "var/semantic-inbox/shop.json").exists()
    st = _state(root)
    assert "semantic" not in st and "shop" not in st["registered"]


def test_other_commands_use_default_guard(root, capsys):
    for argv in (["datasets"], ["use", "shop"], ["register", "shop", "--remove"]):
        GUARDED.clear()
        main(argv)
        assert GUARDED == [{}], argv
    capsys.readouterr()


def test_old_state_without_semantic_reads_empty(root, capsys):
    st = _state(root)
    assert "semantic" not in st
    assert call(capsys, "datasets")[0] == 0
    assert "semantic" not in _state(root)


def test_real_guard_subprocess(tmp_path, shop_db):
    """감시를 실제로 건 하위 프로세스에서: check·import는 두 자리를 열고, 다른 명령은 열지 못한다."""
    r = tmp_path / "repo"
    r.mkdir()
    (r / "harness.toml").write_text(TOML, encoding="utf-8")
    db = tmp_path / "dbs" / "shop.sqlite"
    db.parent.mkdir()
    shutil.copy(shop_db, db)
    env = {k: v for k, v in os.environ.items() if not k.startswith("T2S_")}
    env["T2S_ROOT"] = str(r)

    def t2s(*argv):
        return subprocess.run(
            [sys.executable, "-m", "t2s.cli.main", *argv],
            env=env,
            capture_output=True,
            text=True,
            cwd=tmp_path,
            check=False,
        )

    assert t2s("register", "shop", "--sqlite", str(db), "--yes").returncode == 0
    _inbox(r)
    assert t2s("semantic-check", "shop").returncode == 0
    _inbox(r)
    p = t2s("semantic-import", "shop", "--yes")
    assert p.returncode == 0, p.stderr
    assert (r / "data/shop/semantic.approved.json").is_file()
    # 기본 감시가 걸린 명령에서 정의 파일을 열려 하면 막힌다(같은 감시 함수로)
    code = (
        "import sys; from pathlib import Path; from t2s.runner.guard import install_guard, GoldAccessBlocked\n"
        "install_guard(Path(sys.argv[1]))\n"
        "try:\n    open(sys.argv[1] + '/data/shop/semantic.approved.json').read(); print('ok')\n"
        "except GoldAccessBlocked:\n    print('blocked')\n"
    )
    q = subprocess.run([sys.executable, "-c", code, str(r)], capture_output=True, text=True, check=True)
    assert q.stdout.strip() == "blocked"
    d = t2s("datasets")
    assert d.returncode == 0 and "    지표 정의: 모델 shop-fixture 판 fixture- · 승인 지표 3개 · 받은 때 " in d.stdout
