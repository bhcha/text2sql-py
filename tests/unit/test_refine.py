import pytest

from t2s.datasource.sqlite import SqliteSource
from t2s.pipeline import refine
from t2s.pipeline.refine import (
    TEMPLATE_FIELDS,
    add_select_prefix,
    foreign_key_text,
    parse_sql_block,
    refine_loop,
    refine_prompt,
)

FIELDS = {f: f"<{f}>" for f in TEMPLATE_FIELDS}


class LlmCallFailed(Exception):
    pass


def make_ask(responses, *, fail_at=None):
    calls = []

    def ask(name, prompt, attempt, parse):
        calls.append({"name": name, "prompt": prompt, "attempt": attempt})
        if fail_at is not None and len(calls) == fail_at:
            raise LlmCallFailed("호출 실패")
        text = responses[min(len(calls) - 1, len(responses) - 1)]
        parse(text)
        return text, len(calls) + 2

    return ask, calls


def loop(shop_db, sql, responses, **kw):
    src = SqliteSource(shop_db)
    ask, calls = make_ask(responses, fail_at=kw.pop("fail_at", None))
    fix = refine_loop(sql, question="q?", schema_text="SCHEMA {sql}", tables=src.tables(), source=src, ask=ask, **kw)
    return fix, calls


def test_add_select_prefix():
    assert add_select_prefix("SELECT 1") == "SELECT 1"
    assert add_select_prefix("select 1") == "select 1"
    assert add_select_prefix("Select 1") == "SELECTSelect 1"
    assert add_select_prefix("WITH x AS (SELECT 1) SELECT * FROM x") == "SELECTWITH x AS (SELECT 1) SELECT * FROM x"


def test_parse_sql_block():
    assert parse_sql_block("no block") == (None, 0)
    assert parse_sql_block("```sql\n  SELECT 1 \n```") == ("SELECT 1", 1)
    assert parse_sql_block("```sql\nSELECT 1\n```\nthen\n```sql\nSELECT 2\n```") == ("SELECT 2", 2)
    assert parse_sql_block("```SQL\nSELECT 2\n```") == (None, 0)


def test_refine_prompt_fills_once():
    p = refine_prompt({**FIELDS, "sql": "SELECT '{question}'"})
    for f in TEMPLATE_FIELDS:
        if f != "sql":
            assert f"<{f}>" in p
    assert "SELECT '{question}'" in p


def test_refine_prompt_unknown_field(monkeypatch):
    monkeypatch.setattr(refine, "template_text", lambda: "{question} {other}")
    with pytest.raises(KeyError):
        refine_prompt(FIELDS)


def test_foreign_key_text(shop_db):
    src = SqliteSource(shop_db)
    fk = foreign_key_text(src, src.tables())
    assert fk.text == "order.`customer_ref` = customer.`cid`\norder.`code` = product.`code`"
    assert (fk.lines, fk.to_from_primary_key, fk.table_case_fixed, fk.unresolved, fk.duplicates) == (2, 1, 0, 0, 0)


def test_foreign_key_unresolved_when_table_missing(shop_db):
    src = SqliteSource(shop_db)
    fk = foreign_key_text(src, ["order", "product"])
    assert fk.text == "order.`code` = product.`code`"
    assert fk.unresolved == 1


def test_no_refine_needed(shop_db):
    fix, calls = loop(shop_db, "SELECT COUNT(*) FROM product", ["x"])
    assert calls == []
    assert (len(fix.rounds), fix.stop, fix.final, fix.final_executed, fix.refine_calls) == (
        1,
        "no_refine_needed",
        "old",
        True,
        0,
    )
    assert fix.fk is None and fix.rounds[0].head_text == "[(3,)]"


def test_fixed_in_second_round(shop_db):
    fix, calls = loop(shop_db, "SELECT * FROM nope", ["```sql\nSELECT COUNT(*) FROM product\n```"])
    assert len(calls) == 1 and calls[0]["attempt"] == 1
    assert [r.executed for r in fix.rounds] == [True, True]
    assert (fix.stop, fix.final, fix.final_executed, fix.sql) == (
        "no_refine_needed",
        "new",
        True,
        "SELECT COUNT(*) FROM product",
    )
    r1 = fix.rounds[0]
    assert (r1.status, r1.reason, r1.call_k, r1.parse, r1.blocks) == ("error", "error", 3, "sql_block", 1)
    assert r1.sqlite_error == "no such table: nope"
    prompt = calls[0]["prompt"]
    assert "SCHEMA {sql}" in prompt and "no such table: nope" in prompt and "sqlite3.OperationalError" in prompt
    assert "order.`code` = product.`code`" in prompt


def test_three_refines_still_empty(shop_db):
    empty = "```sql\nSELECT code FROM product WHERE 0\n```"
    fix, calls = loop(shop_db, "SELECT code FROM product WHERE 0", [empty])
    assert len(calls) == 3 and [c["attempt"] for c in calls] == [1, 2, 3]
    assert sum(r.executed for r in fix.rounds) == 3
    assert (fix.stop, fix.final, fix.final_executed, fix.refine_calls) == ("max_rounds", "new", False, 3)
    assert all(r.reason == "no_rows" for r in fix.rounds)


def test_no_sql_block_keeps_previous(shop_db):
    fix, calls = loop(shop_db, "SELECT price FROM product", ["I could not find a fix."])
    assert len(calls) == 1
    assert (fix.stop, fix.final, fix.sql, len(fix.rounds)) == ("no_sql_block", "old", "SELECT price FROM product", 1)
    assert fix.rounds[0].parse == "no_sql_block" and fix.rounds[0].blocks == 0
    assert fix.final_executed is True


def test_error_substring_stops_without_executing(shop_db):
    fix, calls = loop(shop_db, "SELECT 1 AS error FROM product WHERE 0", ["x"])
    assert calls == [] and fix.rounds[0].executed is False
    assert (fix.stop, fix.rounds[0].reason) == ("skipped_error_substring", "skipped_error_substring")


def test_error_substring_in_refined_sql(shop_db):
    fix, calls = loop(shop_db, "SELECT * FROM nope", ["```sql\nSELECT 'error'\n```"])
    assert len(calls) == 1 and len(fix.rounds) == 2
    assert fix.stop == "skipped_error_substring" and fix.final_executed is False and fix.sql == "SELECT 'error'"


def test_timeout_stops(shop_db):
    fix, calls = loop(shop_db, "SELECT COUNT(*) FROM big a, big b, big c", ["x"], exec_timeout_seconds=0.3)
    assert calls == [] and fix.stop == "skipped_timeout" and fix.rounds[0].status == "timeout"


def test_prefix_recorded(shop_db):
    fix, calls = loop(shop_db, "WITH x AS (SELECT 1 WHERE 0) SELECT * FROM x", ["no block"])
    assert fix.rounds[0].sql_arg_prefixed is True
    assert "SELECTWITH x AS" in calls[0]["prompt"]


def test_refine_call_failure_raises_and_keeps_state(shop_db):
    src = SqliteSource(shop_db)
    ask, _ = make_ask(["x"], fail_at=1)
    state = refine.FixState(old_sql="SELECT * FROM nope", sql="SELECT * FROM nope")
    with pytest.raises(LlmCallFailed):
        refine_loop(state.sql, question="q", schema_text="S", tables=src.tables(), source=src, ask=ask, state=state)
    assert state.stop == "refine_call_failed" and state.refine_calls == 1


def test_exec_failure_is_kept(shop_db, tmp_path):
    src = SqliteSource(tmp_path / "missing.sqlite")
    ask, calls = make_ask(["x"])
    fix = refine_loop("SELECT 1", question="q", schema_text="S", tables=[], source=src, ask=ask)
    assert calls == [] and fix.stop == "skipped_exec_failed"
