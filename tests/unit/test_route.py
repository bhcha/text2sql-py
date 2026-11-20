import dataclasses
import json

from test_batch import FAKE_LLM, env, finish_run, make_ctx, qs  # noqa: F401

from t2s.llm.base import LlmSettings
from t2s.llm.codex_cli import CodexBackend, harness_args
from t2s.llm.fake import FakeBackend
from t2s.pipeline import route as RT
from t2s.runner import batch
from t2s.runner import records as R

ROUTE_LLM = dataclasses.replace(FAKE_LLM, strategy="route")


def test_parse_route():
    assert RT.parse_route('{"solver": "terra-single"}') == "terra-single"
    assert RT.parse_route('blah\n{"solver": "sol62-p3"}\n') == "sol62-p3"
    assert RT.parse_route('{"solver": "nope"}') is None
    assert RT.parse_route("terra-single") is None
    assert RT.parse_route("") is None


def test_parse_single_same_rule_as_baseline():
    assert RT.parse_single("  SELECT 1  ") == "SELECT 1"
    assert RT.parse_single("```sql\nSELECT 1\n```") == "SELECT 1"
    assert RT.parse_single("```\nSELECT 1;\n```") == "SELECT 1;"
    # 울타리 앞에 설명이 있으면 벗기지 않는다(그대로 실행 실패가 된다)
    assert RT.parse_single("Here:\n```sql\nSELECT 1\n```") == "Here:\n```sql\nSELECT 1\n```"


def test_prompts_without_hint_have_no_knowledge_line():
    for build in (RT.single_prompt, RT.router_prompt):
        text = build("Q?", "CREATE TABLE t(a)", None)
        assert RT.KNOWLEDGE_LABEL not in text
        given = build("Q?", "CREATE TABLE t(a)", "a means b")
        assert f"\n{RT.KNOWLEDGE_LABEL}\na means b\n" in given
    assert RT.single_prompt("Q?", "S", None) == (
        "Write a SQLite query for the question. Return only SQL.\n\nSchema:\nS\n\nQuestion:\nQ?\n"
    )


def test_router_prompt_lists_all_candidates_in_cost_order():
    text = RT.router_prompt("Q?", "S", None)
    ids = [c.id for c in RT.CANDIDATES]
    assert [text.index(f". {i}: ") for i in ids] == sorted(text.index(f". {i}: ") for i in ids)
    assert text.endswith('{"solver": "<id>"}\n')
    meta = RT.route_meta()
    assert [c["rank"] for c in meta["candidates"]] == list(range(1, 10))


def test_bare_harness_args():
    assert harness_args(("bare",)) == [
        "-c",
        "include_environment_context=false",
        "-c",
        "skills.include_instructions=false",
    ]
    b = CodexBackend(LlmSettings("codex-cli", "m", "medium", 1), harness=("bare",))
    assert b.harness == ("bare",)


def _factory(choice, single="SELECT COUNT(*) FROM product"):
    def make():
        return FakeBackend({"route": json.dumps({"solver": choice}), "single_sql": single})

    return make


def _route_ctx(env, factory, name):  # noqa: F811
    ctx = make_ctx(env, factory, name)
    ctx.llm = ROUTE_LLM
    return ctx


def test_route_single_records(env):  # noqa: F811
    ctx = _route_ctx(env, _factory("terra-single", "```sql\nSELECT COUNT(*) FROM product\n```"), "rs")
    m = finish_run(ctx, env, qs(3))
    rows = R.read_jsonl(ctx.run / "records.jsonl")
    assert all(r["status"] == "generated" and r["sql"] == "SELECT COUNT(*) FROM product" for r in rows)
    rec = rows[0]
    assert rec["route"]["choice"] == "terra-single"
    assert rec["route"]["router"]["calls"] == 1 and rec["route"]["exec"]["calls"] == 1
    assert rec["llm"]["call_count"] == 2 and m["llm_totals"]["call_count"] == 6
    assert rec["final_exec"]["status"] == "rows" and rec["final_exec"]["source"] == "extra"
    calls = R.item_dir(ctx.run, 0) / "calls"
    assert {p.name for p in calls.iterdir()} == {"1", "2"}
    assert "Solvers, from cheapest" in (calls / "1" / "prompt.txt").read_text()
    assert (calls / "2" / "prompt.txt").read_text().startswith("Write a SQLite query")


def test_route_p3_calls_shifted(env):  # noqa: F811
    ctx = _route_ctx(env, _factory("sol62-p3"), "rp")
    finish_run(ctx, env, qs(1))
    rec = R.read_jsonl(ctx.run / "records.jsonl")[0]
    assert rec["status"] == "generated" and rec["route"]["choice"] == "sol62-p3"
    calls = R.item_dir(ctx.run, 0) / "calls"
    assert {p.name for p in calls.iterdir()} == {"1", "2", "3"}  # 배분 1 + 키워드 1 + 생성 1
    trace = json.loads((R.item_dir(ctx.run, 0) / "trace.json").read_text())
    assert [c["k"] for c in trace["calls"]] == [1, 2, 3]
    assert [c["name"] for c in trace["calls"]] == ["route", "extract_keywords", "generate_sql"]
    assert rec["route"]["exec"]["calls"] == 2 and rec["llm"]["call_count"] == 3


def test_route_unparsed_is_exception(env):  # noqa: F811
    ctx = _route_ctx(env, lambda: FakeBackend({"route": "I pick terra"}), "ru")
    finish_run(ctx, env, qs(2))
    rec = R.read_jsonl(ctx.run / "records.jsonl")[0]
    assert rec["status"] == "exception" and rec["exception"]["type"] == "RouteUnparsed"
    assert rec["route"]["choice"] is None and rec["llm"]["call_count"] == 1


def test_route_hint_reaches_both_prompts(env):  # noqa: F811
    ctx = _route_ctx(env, _factory("luna-single"), "rh")
    ctx.hints = ["paid means status = 'paid'"]
    finish_run(ctx, env, qs(1))
    calls = R.item_dir(ctx.run, 0) / "calls"
    for k in ("1", "2"):
        assert "External Knowledge:\npaid means status = 'paid'\n" in (calls / k / "prompt.txt").read_text()


def test_route_halts_on_first_usage_limit(env):  # noqa: F811
    ctx = _route_ctx(env, lambda: FakeBackend(fail="limit"), "rl")
    m = finish_run(ctx, env, qs(6), workers=1)
    assert m["halted"] == {"reason": "usage_limit", "after": batch.ROUTE_HALT_AFTER}
    assert m["status_counts"]["not_started"] == 5
