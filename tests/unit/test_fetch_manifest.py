import tomllib

from conftest import REPO
from t2s.fetch.bird import load_manifest
from t2s.prep import embed


def test_manifest_shape():
    m = load_manifest()
    assert m["format"] == "t2s-fetch/1"
    assert len(m["databases"]) == 11
    assert len(m["model"]["sha256"]) == 5
    assert m["questions_public"]["count"] == 500
    assert m["archive"]["sha256"].startswith("cc48ba16")


def test_model_hashes_match_embed():
    assert load_manifest()["model"]["sha256"] == embed.MODEL_SHA256


def test_archive_hash_matches_harness_toml():
    raw = tomllib.loads((REPO / "harness.toml").read_text(encoding="utf-8"))
    assert raw["datasets"]["bird-minidev"]["archive_sha256"] == load_manifest()["archive"]["sha256"]


def test_paths_match_harness_toml():
    raw = tomllib.loads((REPO / "harness.toml").read_text(encoding="utf-8"))
    m = load_manifest()
    assert raw["paths"]["embedding_model"] == m["model"]["path"]
    assert raw["datasets"]["bird-minidev"]["questions"] == m["questions_public"]["path"]
    assert raw["datasets"]["bird-minidev"]["root"] == m["extract_root"] + "/minidev/MINIDEV"


def test_olist_manifest_shape():
    from t2s.fetch.olist import load_manifest as load_olist

    m = load_olist()
    assert m["format"] == "t2s-fetch-olist/1"
    assert len(m["source"]["commit"]) == 40
    files = m["files"]
    assert len(files) == 5
    gold = [f for f in files if f["role"] == "gold"]
    assert len(gold) == 1 and gold[0]["path"].startswith("gold/olist/")
    assert all(f["path"].startswith("data/olist/") for f in files if f["role"] != "gold")
    assert all(len(f["sha256"]) == 64 and f["size"] > 0 for f in files)
    assert m["model_inputs_allowed_item_keys"] == ["id", "question", "question_type", "task_conditions"]
