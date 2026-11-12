from conftest import make_db
from t2s.datasource.sqlite import SqliteSource


def test_rows_and_head(shop_db):
    r = SqliteSource(shop_db).execute("SELECT n FROM big ORDER BY n", 5, 3)
    assert r.status == "rows"
    assert r.row_count == 2000
    assert r.head == [(1,), (2,), (3,)]
    assert r.columns == ["n"]
    assert r.column_count == 1
    assert r.error == "" and r.exception_class == ""


def test_empty(shop_db):
    r = SqliteSource(shop_db).execute("SELECT * FROM customer WHERE cid = 99", 5, 5)
    assert r.status == "empty"
    assert r.row_count == 0
    assert r.column_count == 2


def test_error_text_and_class(shop_db):
    r = SqliteSource(shop_db).execute("SELECT nope FROM customer", 5, 5)
    assert r.status == "error"
    assert r.error == "no such column: nope"
    assert r.exception_class == "<class 'sqlite3.OperationalError'>"


def test_invalid_utf8_text_read_without_exception(tmp_path):
    db = make_db(tmp_path / "bad.sqlite", "CREATE TABLE t (a TEXT); INSERT INTO t VALUES (CAST(x'61ff62' AS TEXT));")
    r = SqliteSource(db).execute("SELECT a FROM t", 5, 5)
    assert r.status == "rows"
    assert r.head == [("a�b",)]
