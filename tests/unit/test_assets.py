import json
import shutil

import pytest

from conftest import make_db
from t2s.datasource.sqlite import SqliteSource
from t2s.prep import assets

HASHES = {
    "config.json": "c",
    "tokenizer.json": "t",
    "tokenizer_config.json": "tc",
    "special_tokens_map.json": "s",
    "onnx/model.onnx": "m",
}


def _build(src, out):
    return assets.build_assets(src, out, dataset="shop", source_name="shop", model_hashes=HASHES)


def test_build_then_ok(shop_db, tmp_path):
    src = SqliteSource(shop_db)
    out = tmp_path / "assets" / "shop" / "shop"
    m = _build(src, out)
    assert assets.asset_state(src, out, HASHES).state == "ok"
    assert m["format"] == "t2s-assets/1"
    assert m["database_unchanged"] is True
    assert set(m["files"]) == set(assets.ASSET_FILES)
    assert m["counts"]["values"] == 19
    assert m["counts"]["tables"] == 8
    assert m["sqlite_version"] and m["python_version"].startswith("3.13")
    assert json.loads((out / "manifest.json").read_text())["counts"] == m["counts"]
    assert not (tmp_path / "assets" / "shop" / "shop.part").exists()
    loaded = assets.load_assets(src, out, HASHES)
    assert loaded.profile["db_id"] == "shop"
    assert len(loaded.index.entries) == 19
    # 다시 만들어도(덮어쓰기) 상태는 ok
    _build(src, out)
    assert assets.asset_state(src, out, HASHES).state == "ok"


def test_missing(shop_db, tmp_path):
    st = assets.asset_state(SqliteSource(shop_db), tmp_path / "none", HASHES)
    assert st.state == "missing"


def test_db_changed(shop_db, tmp_path):
    out = tmp_path / "a"
    _build(SqliteSource(shop_db), out)
    other = make_db(tmp_path / "other.sqlite", "CREATE TABLE z (a TEXT);")
    shutil.copy(other, shop_db)
    st = assets.asset_state(SqliteSource(shop_db), out, HASHES)
    assert st.state == "stale" and "DB 바뀜" in st.reasons
    with pytest.raises(assets.AssetsStale):
        assets.load_assets(SqliteSource(shop_db), out, HASHES)


def test_asset_file_changed(shop_db, tmp_path):
    src = SqliteSource(shop_db)
    out = tmp_path / "a"
    _build(src, out)
    (out / "values.json").write_text("{}\n")
    st = assets.asset_state(src, out, HASHES)
    assert st.state == "stale" and "자산 파일 바뀜" in st.reasons


def test_code_changed(shop_db, tmp_path, monkeypatch):
    src = SqliteSource(shop_db)
    out = tmp_path / "a"
    _build(src, out)
    real = assets.prep_code_hashes()
    monkeypatch.setattr(assets, "prep_code_hashes", lambda: {**real, "prep/profile.py": "0" * 64})
    st = assets.asset_state(src, out, HASHES)
    assert st.state == "stale" and "코드 바뀜" in st.reasons


def test_model_changed(shop_db, tmp_path):
    src = SqliteSource(shop_db)
    out = tmp_path / "a"
    _build(src, out)
    st = assets.asset_state(src, out, {**HASHES, "onnx/model.onnx": "other"})
    assert st.state == "stale" and "모델 바뀜" in st.reasons


def test_db_changes_during_build_leaves_nothing(shop_db, tmp_path, monkeypatch):
    src = SqliteSource(shop_db)
    out = tmp_path / "a"
    from t2s.prep import profile

    real = profile.profile_source

    def tamper(source, name):
        result = real(source, name)
        with open(shop_db, "ab") as f:
            f.write(b"\0" * 4096)
        return result

    monkeypatch.setattr(profile, "profile_source", tamper)
    with pytest.raises(assets.PrepFailed):
        _build(src, out)
    assert not out.exists()
    assert not (tmp_path / "a.part").exists()
