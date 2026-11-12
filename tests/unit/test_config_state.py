import json
import os

import pytest

from t2s.dataset import config as cfg
from t2s.dataset import state as st

TOML = """
[defaults]
dataset = "bird-minidev"
profile = "session"

[paths]
data = "data"
runs = "runs"
embedding_model = "data/models/m"

[datasets.bird-minidev]
kind = "bird-minidev"
root = "data/bird"
databases = "dev_databases"
questions = "data/q.json"
archive_sha256 = "abc"

[datasets.shop]
kind = "single"
[datasets.shop.sources.shop]
kind = "sqlite"
path = "dbs/shop.sqlite"

[profiles.p3-ref]
backend = "codex-cli"
"""


@pytest.fixture
def root(tmp_path):
    (tmp_path / "harness.toml").write_text(TOML, encoding="utf-8")
    return tmp_path


def test_load_and_resolve(root):
    c = cfg.load_config(root, st.State())
    assert c.data_dir == root / "data"
    assert c.embedding_model_dir == root / "data/models/m"
    assert list(c.datasets) == ["bird-minidev", "shop"]
    b = c.datasets["bird-minidev"]
    assert b.missing == "자료 없음: /getbird" and b.sources == {}
    assert b.questions == root / "data/q.json"
    assert c.datasets["shop"].sources["shop"].path == root / "dbs/shop.sqlite"
    assert c.datasets["shop"].missing == "DB 파일 없음"
    assert c.profiles["p3-ref"]["backend"] == "codex-cli"


def test_bird_sources_found(root):
    for db in ("b_db", "a_db"):
        d = root / "data/bird/dev_databases" / db
        d.mkdir(parents=True)
        (d / f"{db}.sqlite").write_bytes(b"")
    c = cfg.load_config(root, st.State())
    assert list(c.datasets["bird-minidev"].sources) == ["a_db", "b_db"]
    assert c.datasets["bird-minidev"].missing is None


def test_registered_merge_and_conflict(root, tmp_path):
    db = tmp_path / "x.sqlite"
    db.write_bytes(b"")
    reg = {"kind": "single", "sources": {"mine": {"kind": "sqlite", "path": str(db)}}}
    c = cfg.load_config(root, st.State(registered={"mine": reg}))
    assert c.datasets["mine"].origin == "state"
    assert c.datasets["mine"].missing is None
    with pytest.raises(cfg.ConfigError, match="겹칩니다"):
        cfg.load_config(root, st.State(registered={"shop": reg}))


def test_current_dataset_priority(root):
    c = cfg.load_config(root, st.State())
    assert st.current_dataset(c, st.State()) == ("bird-minidev", "harness.toml")
    assert st.current_dataset(c, st.State(current_dataset="shop")) == ("shop", "state")


def test_save_and_load_state_atomic(root):
    s = st.State(current_dataset="shop", registered={"a": {"kind": "single", "sources": {}}})
    st.save_state(root, s)
    p = root / "var" / "state.json"
    raw = json.loads(p.read_text())
    assert raw["format"] == "t2s-state/1"
    assert not (root / "var" / "state.json.tmp").exists()
    assert st.load_state(root) == s
    assert st.load_state(root / "nowhere") == st.State()


def test_find_root(root, monkeypatch):
    sub = root / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.delenv("T2S_ROOT", raising=False)
    assert cfg.find_root(sub) == root.resolve()
    monkeypatch.setenv("T2S_ROOT", str(root))
    assert cfg.find_root(sub / "..") == root.resolve()
    monkeypatch.setenv("T2S_ROOT", os.fspath(root / "a"))
    with pytest.raises(cfg.ConfigError):
        cfg.find_root()
