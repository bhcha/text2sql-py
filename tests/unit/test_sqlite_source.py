import hashlib

from t2s.datasource.base import ForeignKey
from t2s.datasource.factory import open_source
from t2s.datasource.sqlite import SqliteSource, sqlite_binary_key


def test_tables_in_sqlite_master_order(shop_db):
    src = SqliteSource(shop_db)
    assert src.tables() == ["product", "order", "sqlite_sequence", "customer", "big", "cats", "mixed", "empty_table"]


def test_ddl_is_original_text(shop_db):
    ddl = SqliteSource(shop_db).ddl("customer")
    assert ddl.startswith("CREATE TABLE customer (")
    assert "cid INTEGER PRIMARY KEY" in ddl


def test_sample_rows(shop_db):
    s = SqliteSource(shop_db).sample_rows("order", 2)
    assert s.columns == ["order_no", "code", "customer_id", "status", "customer_ref"]
    assert s.rows == [(1, "P1", "C1", "paid", "1"), (2, "P2", "C2", "shipped", "2")]


def test_columns_and_primary_keys(shop_db):
    src = SqliteSource(shop_db)
    cols = src.columns("mixed")
    # PRAGMA table_info는 선언 형식을 대문자로 돌려준다(소문자 text도 TEXT로 보임, SQLite 3.50.4에서 확인)
    assert [(c.name, c.decl_type) for c in cols] == [
        ("m", ""),
        ("num_text", "INT_TEXT"),
        ("lower_t", "TEXT"),
        ("empty_t", "TEXT"),
    ]
    pks = src.primary_keys()
    assert pks["product"] == ["code"]
    assert pks["order"] == ["order_no"]
    assert pks["customer"] == ["cid"]
    assert pks["big"] == []


def test_foreign_keys_with_missing_to(shop_db):
    fks = SqliteSource(shop_db).foreign_keys()
    assert ForeignKey("order", "code", "product", "code", 1, 0) in fks
    assert ForeignKey("order", "customer_ref", "customer", None, 0, 0) in fks
    assert len(fks) == 2


def test_fingerprint_matches_hashlib(shop_db):
    fp = SqliteSource(shop_db).fingerprint()
    assert fp.sha256 == hashlib.sha256(shop_db.read_bytes()).hexdigest()
    assert fp.size == shop_db.stat().st_size


def test_factory(shop_db):
    assert open_source("sqlite", path=str(shop_db)).kind == "sqlite"


def test_binary_key_order():
    vals = ["a", b"\x00", 2.5, "B", 2]
    assert sorted(vals, key=sqlite_binary_key) == [2, 2.5, "B", "a", b"\x00"]


def test_quote_doubles_backticks():
    assert SqliteSource.dialect.quote("a`b") == "`a``b`"
