"""스터디 채점 규칙(eval/study.py의 execute·compare)을 손으로 만든 가짜 문항으로 시험한다."""

import sqlite3

import pytest

from t2s.eval import study as S


def item(rows, *, cols=None, row_order="required", tol=0.0001):
    return {
        "expected_columns": cols if cols is not None else [f"c{i}" for i in range(len(rows[0]) if rows else 1)],
        "expected_rows": rows,
        "comparison": {"row_order": row_order, "numeric_abs_tolerance": tol},
    }


def test_column_names_ignored_and_counts_must_match():
    it = item([[1, "a"]], cols=["x", "y"])
    assert S.compare(it, ["other", "names"], [[1, "a"]])[:2] == (True, "일치")
    assert S.compare(it, ["x"], [[1]])[2] == "column_count"
    assert S.compare(it, ["x", "y"], [[1, "a"], [2, "b"]])[2] == "row_count"
    assert S.compare(it, None, None)[2] == "exec_failed"


def test_column_order_permutations_up_to_six():
    six = item([[1, 2, 3, 4, 5, 6]])
    assert S.compare(six, list("abcdef"), [[6, 5, 4, 3, 2, 1]])[2] == "match_column_order"
    seven = item([[1, 2, 3, 4, 5, 6, 7]])
    assert S.compare(seven, list("abcdefg"), [[1, 2, 3, 4, 5, 6, 7]])[2] == "match"
    assert S.compare(seven, list("abcdefg"), [[2, 1, 3, 4, 5, 6, 7]])[2] == "value_diff"


@pytest.mark.parametrize("word", ["any", "ignored", "not_required", "unordered", " ANY "])
def test_unordered_words(word):
    it = item([[1], [2]], row_order=word)
    assert S.compare(it, ["a"], [[2], [1]])[0] is True


@pytest.mark.parametrize("word", ["required", "ORDER BY price DESC", "", "정렬해서 낸다"])
def test_other_row_order_text_is_ordered(word):
    it = item([[1], [2]], row_order=word)
    assert S.compare(it, ["a"], [[2], [1]])[0] is False
    assert S.compare(it, ["a"], [[1], [2]])[0] is True


def test_missing_row_order_means_ordered():
    it = {"expected_columns": ["a"], "expected_rows": [[1], [2]], "comparison": {}}
    assert S.compare(it, ["a"], [[2], [1]])[0] is False


def test_unordered_keeps_duplicates():
    it = item([[1], [1], [2]], row_order="any")
    assert S.compare(it, ["a"], [[1], [2], [1]])[0] is True
    assert S.compare(it, ["a"], [[1], [2], [2]])[0] is False


def test_numbers_tolerance_and_null():
    it = item([[1.0, None, "x"]])
    assert S.compare(it, ["a", "b", "c"], [[1.00009, None, "x"]])[0] is True
    assert S.compare(it, ["a", "b", "c"], [[1.0002, None, "x"]])[0] is False
    assert S.compare(it, ["a", "b", "c"], [[1, 0, "x"]])[0] is False  # NULL은 NULL과만
    assert S.compare(item([["1"]]), ["a"], [[1]])[0] is False  # 글과 수는 다르다
    assert S.compare(item([[3]], tol=0.5), ["a"], [[3.4]])[0] is True
    no_tol = {"expected_columns": ["a"], "expected_rows": [[1.0]], "comparison": {}}
    assert S.compare(no_tol, ["a"], [[1.00009]])[0] is True


def test_empty_expected():
    it = {"expected_columns": ["a", "b"], "expected_rows": [], "comparison": {}}
    assert S.compare(it, ["a", "b"], [])[0] is True
    assert S.compare(it, ["a"], [])[2] == "column_count"


@pytest.fixture
def con(shop_db):
    c = S.connect(shop_db)
    yield c
    c.close()


def test_strip_and_multi_statement(con):
    assert S.strip_sql("  SELECT 1 ;; ") == "SELECT 1"
    assert S.execute(con, S.strip_sql("SELECT 1;"))[3] == "ok"
    cols, _rows, err, code = S.execute(con, "SELECT 1; SELECT 2")
    assert cols is None and code == "multi_statement" and err == "여러 문장은 실행하지 않음"


def test_too_many_rows_and_timeout(con):
    cols, rows, err, code = S.execute(con, "SELECT n FROM big")
    assert code == "too_many_rows" and len(rows) == 1000 and err
    endless = "WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM r) SELECT COUNT(*) FROM r"
    cols, rows, err, code = S.execute(con, endless, timeout_s=0.2)
    assert code == "timeout" and cols is None


def test_authorizer_and_read_only(con):
    assert S.execute(con, "SELECT load_extension('x')")[3] == "denied"
    assert S.execute(con, "PRAGMA table_info(product)")[3] == "denied"
    assert S.execute(con, "DELETE FROM product")[3] == "denied"
    assert S.execute(con, "SELECT * FROM no_such_table")[3] == "error"
    with pytest.raises(sqlite3.Error):
        con.execute("CREATE TABLE z(a)")


def test_judge(con):
    it = {"expected_columns": ["n"], "expected_rows": [[3]], "comparison": {}}
    assert S.judge(con, it, "SELECT COUNT(*) FROM product;", 10)["ex_match"] == 1
    assert S.judge(con, it, None, 10) == {"ex_match": 0, "exec": "no_sql", "reason": "SQL 없음", "code": "no_sql"}
    bad = S.judge(con, it, "SELECT 1; SELECT 2", 10)
    assert bad["ex_match"] == 0 and bad["exec"] == "multi_statement"
