import pytest

from t2s.datasource.sqlite import SqliteSource
from t2s.pipeline import judge
from t2s.pipeline.judge import NO_ROWS_MESSAGE, NULL_VALUE_MESSAGE, decide, has_error_substring, stop_reason


def run(shop_db, sql, timeout=5):
    return decide(SqliteSource(shop_db).execute(sql, timeout, 5))


def test_error_refines(shop_db):
    d = run(shop_db, "SELECT * FROM no_such_table")
    assert (d.refine, d.reason) == (True, "error")
    assert d.sqlite_error == "no such table: no_such_table"
    assert d.exception_class == "<class 'sqlite3.OperationalError'>"


def test_no_rows(shop_db):
    d = run(shop_db, "SELECT code FROM product WHERE 0")
    assert (d.refine, d.reason, d.sqlite_error, d.exception_class) == (True, "no_rows", NO_ROWS_MESSAGE, "")


def test_null_in_first_five(shop_db):
    d = run(shop_db, "SELECT price FROM product")
    assert (d.refine, d.reason, d.sqlite_error, d.exception_class) == (True, "null_value", NULL_VALUE_MESSAGE, "")


def test_null_only_in_sixth_row_is_ok(shop_db):
    d = run(shop_db, "SELECT CASE WHEN n = 6 THEN NULL ELSE n END FROM big ORDER BY n LIMIT 6")
    assert (d.refine, d.reason) == (False, "ok")


def test_timeout_keeps(shop_db):
    d = run(shop_db, "SELECT COUNT(*) FROM big a, big b, big c", timeout=0.5)
    assert (d.refine, d.reason, d.sqlite_error) == (False, "skipped_timeout", None)


def test_write_is_denied_error(shop_db):
    src = SqliteSource(shop_db)
    r = src.execute("DELETE FROM product", 5, 5)
    assert r.status == "error" and r.denied
    assert decide(r).reason == "error"


def test_unbound_placeholder_is_error(shop_db):
    r = SqliteSource(shop_db).execute("SELECT * FROM product WHERE price > :lo", 5, 5)
    assert r.status == "error"
    assert r.error.startswith("Incorrect number of bindings supplied")
    assert r.exception_class == "<class 'sqlite3.ProgrammingError'>"
    assert decide(r).refine


def test_error_substring_is_lowercase_part():
    assert has_error_substring("SELECT 1 AS error_count")
    assert has_error_substring("SELECT 'terror'")
    assert not has_error_substring("SELECT 1 AS ERROR")


def test_stop_reason():
    assert stop_reason("ok") == "no_refine_needed"
    for r in ("skipped_error_substring", "skipped_timeout", "skipped_exec_failed"):
        assert stop_reason(r) == r
    with pytest.raises(ValueError):
        stop_reason("error")


def test_messages_are_the_constants():
    assert judge.NO_ROWS_MESSAGE == "the query ran without error but returned no rows"
    assert judge.NULL_VALUE_MESSAGE.startswith("the first rows of the result contain NULL values")
