"""단일 호출 경로: 틀·힌트 줄·응답 읽기·CREATE 원문 잇기·호출 기록."""

import json

import pytest

from t2s.datasource.sqlite import SqliteSource
from t2s.llm.base import LlmSettings, sha256_text
from t2s.llm.codex_cli import CodexBackend
from t2s.llm.fake import FakeBackend
from t2s.llm.select import ResolvedLlm, backend_for
from t2s.pipeline import single

# 형제 저장소 text2sql-model-baseline df10b12 baseline/prompt.py 5-7행과 같은 글
TEMPLATE = "Write a SQLite query for the question. Return only SQL.\n\nSchema:\n{schema}\n\nQuestion:\n{question}\n"


def test_template_is_fixed_text():
    assert single.SINGLE_TEMPLATE == TEMPLATE
    assert (
        single.single_prompt("Q?", "S", None)
        == "Write a SQLite query for the question. Return only SQL.\n\nSchema:\nS\n\nQuestion:\nQ?\n"
    )
    assert single.KNOWLEDGE_LABEL not in single.single_prompt("Q?", "S", None)


def test_hint_line():
    base = single.single_prompt("Q?", "S", None)
    assert single.single_prompt("Q?", "S", "price is cost") == base + "\nExternal Knowledge:\nprice is cost\n"
    assert single.single_prompt("Q?", "S", "") == base + "\nExternal Knowledge:\n\n"
    assert single.single_prompt("Q?", "S", "{x}") == base + "\nExternal Knowledge:\n{x}\n"


@pytest.mark.parametrize(
    ("response", "sql"),
    [
        ("  SELECT 1  \n", "SELECT 1"),
        ("```sql\nSELECT 1\n```", "SELECT 1"),
        ("```SQLite\nSELECT 2;\n```", "SELECT 2;"),
        ("```\n SELECT 3 \n```", "SELECT 3"),
        ("Here it is:\n```sql\nSELECT 1\n```", "Here it is:\n```sql\nSELECT 1\n```"),
        ("```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```", "SELECT 1\n```\n```sql\nSELECT 2"),
        ("select 1", "select 1"),
        ("", ""),
    ],
)
def test_parse_single(response, sql):
    assert single.parse_single(response) == sql


def test_create_schema_sorted_without_sqlite_tables(shop_db):
    src = SqliteSource(shop_db)
    names = sorted(t for t in src.tables() if not t.lower().startswith("sqlite_"))
    text = single.create_schema(src)
    assert text == "\n\n".join(src.ddl(t) for t in names)
    assert "sqlite_sequence" not in text
    positions = [text.index(src.ddl(t)) for t in names]
    assert positions == sorted(positions)


def test_answer_single_one_call(shop_db, tmp_path):
    src = SqliteSource(shop_db)
    fb = FakeBackend()
    trace = single.answer_single("How many products?", src, fb, calls_dir=tmp_path / "calls")
    assert [c["name"] for c in fb.calls] == ["single_sql"]
    assert fb.calls[0]["prompt"] == single.single_prompt("How many products?", single.create_schema(src), None)
    assert sorted(p.name for p in (tmp_path / "calls").iterdir()) == ["1"]
    assert (tmp_path / "calls/1/prompt.txt").read_text(encoding="utf-8") == fb.calls[0]["prompt"]
    assert trace.sql == "SELECT COUNT(*) FROM product" and trace.exception is None
    assert trace.stages["extract"] == {"sql": trace.sql, "rule": "strip-and-one-fence"}
    assert trace.stages["prompt"]["kind"] == "single_sql"
    assert trace.stages["prompt"]["sha256"] == sha256_text(fb.calls[0]["prompt"])
    for sid in ("schema", "extract_keywords", "retrieve_entity", "attach_values", "attach_profile", "execute"):
        assert trace.stages[sid] is None
    assert trace.versions["single_rules"] == "single/1" and "hint_rules" not in trace.versions
    json.dumps(trace.to_json())


def test_answer_single_hint_and_failures(shop_db):
    src = SqliteSource(shop_db)
    fb = FakeBackend()
    trace = single.answer_single("Q", src, fb, hint="cost means price")
    assert fb.calls[0]["prompt"].endswith("\nExternal Knowledge:\ncost means price\n")
    assert trace.versions["hint_rules"] == "1.0.0"

    trace = single.answer_single("Q", src, FakeBackend(fail="limit"))
    assert trace.sql is None and trace.exception["type"] == "FakeUsageLimit"
    assert trace.stages["extract"] is None and len(trace.calls) == 1 and trace.calls[0].failure == "FakeUsageLimit"

    trace = single.answer_single("Q", src, FakeBackend({"single_sql": "   "}))
    assert trace.sql is None and trace.exception is None
    assert trace.stages["extract"] == {"sql": None, "rule": "strip-and-one-fence"}
    assert trace.calls[0].parse_ok is False


def test_backend_for_single_uses_bare():
    llm = ResolvedLlm("codex-cli", "gpt-5.6-terra", "medium", 10, 100, None, "conversation", "conversation")
    b = backend_for(llm, single=True)
    assert isinstance(b, CodexBackend) and b.harness == ("bare",)
    assert backend_for(llm).harness is None
    fake = ResolvedLlm("fake", "fake-model", "none", 1, 1, None, "fake", "fake")
    assert isinstance(backend_for(fake, single=True), FakeBackend)
    assert LlmSettings("codex-cli", "gpt-5.6-terra", "medium", 10) == b.settings
