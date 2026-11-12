import pytest

from t2s.datasource.sqlite import SqliteSource

DENIED = [
    "INSERT INTO customer VALUES (9, 'x')",
    "UPDATE customer SET name = 'x'",
    "DELETE FROM customer",
    "DROP TABLE customer",
    "CREATE TABLE t (a)",
    "CREATE TEMP TABLE t (a)",
    "ATTACH DATABASE ':memory:' AS other",
    "PRAGMA journal_mode=wal",
    "VACUUM INTO '/tmp/should-not-exist.sqlite'",
]


@pytest.fixture
def src(shop_db):
    s = SqliteSource(shop_db)
    before = (s.fingerprint(), s.side_files())
    yield s
    assert (s.fingerprint(), s.side_files()) == before


@pytest.mark.parametrize("sql", DENIED)
def test_write_like_statements_denied(src, sql):
    r = src.execute(sql, 5, 5)
    assert r.status == "error"
    assert r.denied is True
    assert "not authorized" in r.error or r.error == "authorization denied"


def test_two_statements_rejected(src):
    r = src.execute("SELECT 1; SELECT 2", 5, 5)
    assert r.status == "error"
    assert "one statement" in r.error
    assert r.denied is False


def test_nul_char_rejected(src):
    r = src.execute("SELECT 1\x00", 5, 5)
    assert r.status == "error"


@pytest.mark.parametrize("sql", ["", "   ", "-- 주석뿐", "/* 주석 */"])
def test_blank_or_comment_only_is_empty(src, sql):
    r = src.execute(sql, 5, 5)
    assert r.status == "empty"
    assert r.row_count == 0


def test_read_pragmas_allowed(src):
    assert src.execute("PRAGMA table_info(customer)", 5, 5).status == "rows"
    assert src.execute("SELECT name FROM pragma_table_info('customer')", 5, 5).status == "rows"


def test_double_quoted_string_literal_accepted(src):
    r = src.execute('SELECT cid FROM customer WHERE name = "ab"', 5, 5)
    assert r.status == "rows"
    assert r.head == [(3,)]
