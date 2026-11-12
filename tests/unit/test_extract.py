import pytest

from t2s.pipeline.extract import STOP, cut_at_stop, extract_sql, has_js_only_line_end, line_start_sql, open_parens

NBSP = chr(0xA0)
BOM = chr(0xFEFF)


def ex(text):
    r = extract_sql(text)
    return r.sql, r.check.branch


def test_stop_list():
    assert STOP == ("--", "\n\n", ";", "#")


# TS engines/pure-fix-h/tests/pack.test.ts 'five branches and the stop strings'
def test_ts_three_cases():
    assert [extract_sql(t).sql for t in ("The SQL is:\nSELECT a FROM t;", "COUNT(*) FROM t", " \n")] == [
        "SELECT a FROM t",
        "SELECT COUNT(*) FROM t",
        None,
    ]


# TS tests/fake-codex.mjs의 응답 꼴과 pack.test.ts의 기대값
@pytest.mark.parametrize(
    ("text", "sql", "branch"),
    [
        (
            "1. find rows; 2. count them. Finally the SQL is: SELECT COUNT(*) FROM t WHERE a = 1;\n\nDone.",
            "SELECT COUNT(*) FROM t WHERE a = 1",
            "cot_split",
        ),
        ("```sql\nSELECT 2\n```", "SELECT 2", "starts_sql"),
        ("We need the count.\nselect count(*) from t\n\n-- explanation", "select count(*) from t", "line_start_sql"),
        ("COUNT(*) FROM t", "SELECT COUNT(*) FROM t", "continuation"),
        (
            "DISTINCT a\nFROM t\nWHERE b > (\n  SELECT AVG(b) FROM t\n);",
            "SELECT DISTINCT a\nFROM t\nWHERE b > (\n  SELECT AVG(b) FROM t\n)",
            "continuation",
        ),
    ],
)
def test_ts_fake_modes(text, sql, branch):
    assert ex(text) == (sql, branch)


def test_empty_branch():
    for text in ("  \n", "```sql\n```", "```\n   \n```"):
        r = extract_sql(text)
        assert r.sql is None and r.check.branch == "empty"
        assert r.exception == {"type": "EmptyResponse", "message": "모델 응답이 비어 있습니다"}


def test_cot_split_forms():
    assert ex("The answer:\n\nSELECT 1") == ("SELECT 1", "cot_split")
    assert ex("Use this: WITH x AS (SELECT 1) SELECT * FROM x") == ("WITH x AS (SELECT 1) SELECT * FROM x", "cot_split")
    # 소문자 머리는 가르지 않는다
    r = extract_sql("note: select 1")
    assert r.check.branch == "continuation" and r.check.cot_split_count == 0
    assert extract_sql("a: SELECT 1; b: SELECT 2").check.cot_split_count == 2


def test_starts_sql_forms():
    assert ex("  select 1") == ("select 1", "starts_sql")
    assert ex("WITH RECURSIVE c(x) AS (SELECT 1) SELECT x FROM c") == (
        "WITH RECURSIVE c(x) AS (SELECT 1) SELECT x FROM c",
        "starts_sql",
    )
    for name in ('"my t"', "`my t`", "[my t]", "t_1"):
        assert ex(f"with {name} as (select 1) select 1")[1] == "starts_sql"
    # SELECT 뒤가 낱말 글자면 머리가 아니다
    assert ex("SELECTED rows")[1] == "continuation"


def test_with_word_is_not_head():
    assert ex("With the schema we count.")[1] == "continuation"


def test_line_start_skips():
    assert ex("Explain first.\n  SELECT a FROM t") == ("SELECT a FROM t", "line_start_sql")
    text = "We need:\n(\nSELECT 1\n)"
    assert line_start_sql(text) == (-1, 1)
    r = extract_sql("COUNT(*) FROM a UNION ALL\nSELECT COUNT(*) FROM b")
    assert r.check.branch == "continuation" and r.check.line_start_skipped == 1
    # 설명의 '1)'이 괄호 계산을 흐리지 않는다
    assert ex("1) count rows\nSELECT 1") == ("SELECT 1", "line_start_sql")


def test_open_parens():
    assert open_parens("(()") == 1
    assert open_parens("))(") == 1


@pytest.mark.parametrize(
    ("stop", "text"), [("--", "SELECT 1 -- c"), ("\n\n", "SELECT 1\n\nx"), (";", "SELECT 1;"), ("#", "SELECT 1 #")]
)
def test_each_stop(stop, text):
    r = extract_sql(text)
    assert r.sql == "SELECT 1" and r.check.stop_hit == stop


def test_first_stop_wins():
    assert cut_at_stop("a # b; c -- d") == ("a ", "#")
    assert cut_at_stop("abc") == ("abc", None)


def test_js_trim_chars():
    assert extract_sql(f"{NBSP}{BOM}SELECT 1{NBSP}").sql == "SELECT 1"
    assert extract_sql(f"{BOM}SELECT 1").check.branch == "starts_sql"


def test_counts_always_recorded():
    r = extract_sql("x: SELECT 1\n(\nSELECT 2")
    assert r.check.branch == "cot_split" and r.check.cot_split_count == 1


def test_js_only_line_end():
    assert has_js_only_line_end("a\rb") and not has_js_only_line_end("a\nb")
