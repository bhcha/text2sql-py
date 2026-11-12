import hashlib
import json

import pytest

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


GUARDED: list = []


@pytest.fixture
def root(tmp_path, monkeypatch):
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
    # 감시 훅은 뗄 수 없으므로 pytest 프로세스에는 걸지 않고, 걸려고 했는지만 본다(훅 자체는 test_guard.py)
    monkeypatch.setattr(data_cmds, "install_guard", GUARDED.append)
    return r


def _state(root):
    p = root / "var/state.json"
    return p.read_bytes() if p.exists() else None


def test_register_prepare_use_datasets(root, shop_db, capsys):
    rc = main(["register", "shop", "--sqlite", str(shop_db)])
    o = capsys.readouterr().out
    assert rc == 3
    assert "모델 제공자 서버(OpenAI 또는 Anthropic)로 나갑니다" in o
    assert "다른 프로그램이 이 DB에 쓰고 있지 않아야 합니다." in o
    assert "민감한 DB에는 쓰지 마세요." in o
    assert "--yes" in o
    assert _state(root) is None
    assert GUARDED == [root.resolve()]  # 읽기 감시를 걸었다

    assert main(["register", "shop", "--sqlite", str(shop_db), "--yes"]) == 0
    o = capsys.readouterr().out
    assert "[datasets.shop]" in o and "다음: t2s prepare shop" in o
    reg = json.loads(_state(root))["registered"]["shop"]
    assert reg["sources"]["shop"]["path"] == str(shop_db.resolve())
    assert reg["fingerprint"]["sha256"] == hashlib.sha256(shop_db.read_bytes()).hexdigest()

    assert main(["datasets"]) == 0
    o = capsys.readouterr().out
    assert "shop  single  소스 1  준비 안 됨" in o
    assert "bird-minidev  bird-minidev  소스 0  자료 없음" in o

    assert main(["prepare", "shop"]) == 0
    o = capsys.readouterr().out
    assert "shop: 값 19" in o and "합계: 소스 1(만듦 1, 건너뜀 0)" in o
    assert main(["prepare", "shop"]) == 0
    assert "건너뜀(준비됨)" in capsys.readouterr().out

    assert main(["use", "shop"]) == 0
    assert "지금 바라보는 데이터셋: shop (준비됨 1/1)" in capsys.readouterr().out
    assert main(["datasets"]) == 0
    o = capsys.readouterr().out
    assert o.splitlines()[0] == "지금 바라보는 데이터셋: shop (출처 state)"
    assert "* shop  single  소스 1  준비됨 1/1  (state)" in o
    assert "임베딩 모델: 있음(sha256 5/5 일치)" in o

    assert main(["register", "--remove", "shop"]) == 0
    capsys.readouterr()
    s = json.loads(_state(root))
    assert s["registered"] == {} and s["current_dataset"] is None
    assert not (root / "data/assets/shop").exists()


def test_wal_side_file_warning(root, shop_db, capsys):
    side = shop_db.with_name(shop_db.name + "-wal")
    side.write_bytes(b"")
    assert main(["register", "shop", "--sqlite", str(shop_db)]) == 3
    assert "주의: 부속 파일 shop.sqlite-wal" in capsys.readouterr().out


def test_use_unknown_and_bad_name(root, shop_db, capsys):
    assert main(["use", "nope"]) == 1
    assert "없는 데이터셋: nope" in capsys.readouterr().err
    assert main(["register", "Bad!", "--sqlite", str(shop_db)]) == 2
    assert main(["register", "bird-minidev", "--sqlite", str(shop_db)]) == 1
    assert main(["register", "--remove", "bird-minidev"]) == 1


def test_prepare_bird_missing_and_model_missing(root, shop_db, capsys, monkeypatch):
    assert main(["prepare", "bird-minidev"]) == 1
    assert "자료 없음" in capsys.readouterr().err
    assert main(["register", "shop", "--sqlite", str(shop_db), "--yes"]) == 0
    monkeypatch.setattr(embed, "MODEL_SHA256", dict.fromkeys(embed.MODEL_FILES, "0" * 64))
    capsys.readouterr()
    assert main(["prepare", "shop"]) == 1
    assert "먼저 /getbird 또는 t2s fetch model" in capsys.readouterr().err
