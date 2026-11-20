"""배분 측정(route 브랜치): 배분 모델이 문항마다 후보 하나를 고르고, 고른 후보만 돌린다.

자체 코드다(다른 저장소 코드를 복사하지 않았다).
- 후보: 같은 벤더(Codex) 모델 넷 × 순수 단일 호출 / P3 하네스, 그리고 배분 모델이 직접 푸는 단일 호출 하나.
- 배분 모델에는 질문, CREATE 원문(이름순, 빈 줄로 이음), 힌트를 줄 때만 힌트 글, 후보 설명과 상대 비용 순서만 준다.
  정답 SQL·채점 결과는 주지 않는다.
- 단일 호출 틀은 형제 저장소 text2sql-model-baseline의 단일 호출 틀과 같은 글이다. 다른 점 하나: 힌트 줄의 이름표가
  `External Knowledge:`다(그 저장소는 다른 낱말을 쓴다. 이 저장소의 생성 패키지는 그 낱말을 쓰지 않는다, AGENTS 규칙 2).
- 단일 호출 응답 읽기는 그 저장소와 같은 규칙이다. 앞뒤 공백을 떼고, 응답 전체를 감싼 sql·sqlite·이름 없는 코드 울타리
  하나만 벗긴다. SELECT 보태기, 설명 지우기, 세미콜론 고치기, 실행 피드백은 하지 않는다.
- 힌트 None(evidence not_used)이면 배분·단일 호출 프롬프트에 힌트 줄이 없다. P3 후보는 pipeline.run.answer를 그대로 부른다.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from t2s.datasource.base import DataSource
from t2s.llm.base import LlmBackend, sha256_text
from t2s.pipeline.run import PipelineSettings, answer
from t2s.pipeline.trace import CallRecord, Trace
from t2s.prep.assets import LoadedAssets

if TYPE_CHECKING:
    from t2s.pipeline import value_search
    from t2s.prep.embed import Embedder

ROUTE_RULES_VERSION = "route/1"
ROUTER_CALL = "route"
SINGLE_CALL = "single_sql"
SINGLE, P3, DIRECT = "single", "p3", "direct"


@dataclass(frozen=True)
class Candidate:
    id: str
    model: str
    reasoning_effort: str
    method: str  # single | p3 | direct
    timeout_seconds: float
    harness: tuple[str, ...] | None  # codex 하네스 인자. None이면 P3 기본(minimal, task)
    description: str


SINGLE_DESC = "one call that sees the question and the CREATE statements below and returns SQL."
P3_DESC = (
    "a multi-step pipeline: keyword extraction call, retrieval of database values similar to the question, "
    "schema with 3 example rows and column profiles, one SQL generation call, then up to 3 repair calls "
    "driven by execution results."
)
BARE = ("bare",)

# 싼 것부터 비싼 것 차례(배분 모델에 알리는 상대 비용 순서). 순서는 이 측정의 가정이다(값 비용으로 재지 않았다).
CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        "luna-single", "gpt-5.6-luna", "medium", SINGLE, 150, BARE, f"gpt-5.6-luna (reasoning medium), {SINGLE_DESC}"
    ),
    Candidate(
        "terra-single", "gpt-5.6-terra", "medium", SINGLE, 150, BARE, f"gpt-5.6-terra (reasoning medium), {SINGLE_DESC}"
    ),
    Candidate(
        "sol-single", "gpt-5.6-sol", "medium", SINGLE, 150, BARE, f"gpt-5.6-sol (reasoning medium), {SINGLE_DESC}"
    ),
    Candidate(
        "sol62-single", "gpt-6.2-sol", "medium", SINGLE, 150, BARE, f"gpt-6.2-sol (reasoning medium), {SINGLE_DESC}"
    ),
    Candidate("luna-p3", "gpt-5.6-luna", "medium", P3, 150, None, f"gpt-5.6-luna (reasoning medium) in {P3_DESC}"),
    Candidate("terra-p3", "gpt-5.6-terra", "medium", P3, 150, None, f"gpt-5.6-terra (reasoning medium) in {P3_DESC}"),
    Candidate("sol-p3", "gpt-5.6-sol", "medium", P3, 150, None, f"gpt-5.6-sol (reasoning medium) in {P3_DESC}"),
    Candidate("sol62-p3", "gpt-6.2-sol", "medium", P3, 150, None, f"gpt-6.2-sol (reasoning medium) in {P3_DESC}"),
    Candidate(
        "astra-direct",
        "gpt-6-astra",
        "xhigh",
        DIRECT,
        300,
        BARE,
        "you (gpt-6-astra, reasoning xhigh) write the SQL yourself in one call that sees the question and the "
        "CREATE statements below.",
    ),
)
BY_ID = {c.id: c for c in CANDIDATES}

ROUTER_HEAD = (
    "You are routing one text-to-SQL question to one solver. Choose the solver that will produce a correct "
    "SQLite query at the lowest cost. Do not write SQL.\n\n"
    "Solvers, from cheapest (1) to most expensive ({n}):\n"
)
ROUTER_TAIL = '\nReply with exactly one line of JSON and nothing else: {{"solver": "<id>"}}\n'
SINGLE_TEMPLATE = (
    "Write a SQLite query for the question. Return only SQL.\n\nSchema:\n{schema}\n\nQuestion:\n{question}\n"
)
KNOWLEDGE_LABEL = "External Knowledge:"
_FENCE = re.compile(r"```(?:sql|sqlite)?\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
_JSON = re.compile(r"\{[^{}]*\}")


def candidate_lines() -> str:
    return "".join(f"{i}. {c.id}: {c.description}\n" for i, c in enumerate(CANDIDATES, 1))


def create_schema(source: DataSource) -> str:
    """사용자 테이블 CREATE 원문을 이름순으로 빈 줄로 잇는다(sqlite_ 로 시작하는 테이블은 뺀다)."""
    names = sorted(t for t in source.tables() if not t.lower().startswith("sqlite_"))
    return "\n\n".join(source.ddl(t) for t in names)


def _knowledge(hint: str | None) -> str:
    return "" if hint is None else f"\n{KNOWLEDGE_LABEL}\n{hint}\n"


def single_prompt(question: str, schema: str, hint: str | None) -> str:
    return SINGLE_TEMPLATE.format(schema=schema, question=question) + _knowledge(hint)


def router_prompt(question: str, schema: str, hint: str | None) -> str:
    return (
        ROUTER_HEAD.format(n=len(CANDIDATES))
        + candidate_lines()
        + f"\nSchema:\n{schema}\n\nQuestion:\n{question}\n"
        + _knowledge(hint)
        + ROUTER_TAIL.format()
    )


def parse_single(response: str) -> str:
    text = response.strip()
    m = _FENCE.fullmatch(text)
    return m.group(1).strip() if m else text


def parse_route(response: str) -> str | None:
    """응답의 JSON 한 덩이에서 solver 값. 후보 id가 아니면 None. 여러 덩이면 마지막 것."""
    found = None
    for m in _JSON.finditer(response or ""):
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("solver"), str):
            found = obj["solver"].strip()
    return found if found in BY_ID else None


def route_meta() -> dict:
    """run.json에 남길 배분 설정(후보 표, 배분 틀 원문, 단일 호출 틀 원문)."""
    return {
        "rules": ROUTE_RULES_VERSION,
        "candidates": [
            {
                "rank": i,
                "id": c.id,
                "model": c.model,
                "reasoning_effort": c.reasoning_effort,
                "method": c.method,
                "timeout_seconds": c.timeout_seconds,
                "harness": None if c.harness is None else list(c.harness),
                "description": c.description,
            }
            for i, c in enumerate(CANDIDATES, 1)
        ],
        "router_head": ROUTER_HEAD,
        "router_candidate_lines": candidate_lines(),
        "router_tail": ROUTER_TAIL.format(),
        "single_template": SINGLE_TEMPLATE,
        "knowledge_label": KNOWLEDGE_LABEL,
    }


class RouteFailed(Exception):
    def __init__(self, type_: str, message: str) -> None:
        super().__init__(message)
        self.type = type_
        self.message = message


@dataclass
class RouteResult:
    trace: Trace  # calls[0]은 배분 호출. 그 뒤는 고른 후보의 호출
    choice: str | None
    router_response_chars: int | None
    timing: dict


def _record(k: int, name: str, attempt: int, text: str, outcome, parsed) -> CallRecord:
    meta = outcome.meta
    return CallRecord(
        k=k,
        name=name,
        attempt=attempt,
        prompt_sha256=meta.get("prompt_sha256", sha256_text(text)),
        prompt_chars=meta.get("prompt_chars", len(text)),
        response_sha256=meta.get("response_sha256"),
        response_chars=None if outcome.response is None else len(outcome.response),
        parse_ok=parsed,
        elapsed_ms=int(meta.get("elapsed_ms", 0)),
        tool_calls=int(meta.get("tool_calls", 0)),
        usage=meta.get("usage"),
        failure=None if outcome.failure is None else outcome.failure.type,
    )


def route_answer(
    question: str,
    source: DataSource,
    assets: LoadedAssets,
    router: LlmBackend,
    backends: Mapping[str, LlmBackend],
    *,
    embedder: Embedder | value_search.CachedEmbedder,
    settings: PipelineSettings,
    calls_dir: Path | None,
    hint: str | None = None,
) -> RouteResult:
    """배분 호출 하나(calls/1) 뒤 고른 후보를 돌린다(calls/2부터). 실패는 trace.exception에 남긴다."""
    t_all = time.monotonic()
    timing: dict = {}
    schema = create_schema(source)
    rp = router_prompt(question, schema, hint)
    t0 = time.monotonic()
    outcome = router.call(rp, None if calls_dir is None else Path(calls_dir) / "1", name=ROUTER_CALL, attempt=1)
    timing["route_ms"] = round((time.monotonic() - t0) * 1000)
    choice = None if outcome.response is None else parse_route(outcome.response)
    router_call = _record(1, ROUTER_CALL, 1, rp, outcome, None if outcome.response is None else choice is not None)
    base = Trace(question=question)
    base.versions = {"route_rules": ROUTE_RULES_VERSION}
    base.calls.append(router_call)

    def fail(trace: Trace, type_: str, message: str) -> RouteResult:
        trace.exception = {"type": type_, "message": message}
        trace.sql = None
        timing["total_ms"] = round((time.monotonic() - t_all) * 1000)
        return RouteResult(trace, choice, None if outcome.response is None else len(outcome.response), timing)

    if outcome.failure is not None or outcome.response is None:
        f = outcome.failure
        return fail(base, f.type if f else "NoResponse", f"호출 1({ROUTER_CALL}): {f.message if f else '응답 없음'}")
    if choice is None:
        return fail(base, "RouteUnparsed", "배분 응답에서 후보 id를 읽지 못함")

    cand = BY_ID[choice]
    backend = backends[choice]
    t0 = time.monotonic()
    if cand.method == P3:
        trace = answer(
            question,
            source,
            assets,
            backend,
            embedder=embedder,
            settings=settings,
            calls_dir=calls_dir,
            hint=hint,
            call_offset=1,
        )
        trace.calls.insert(0, router_call)
        trace.versions["route_rules"] = ROUTE_RULES_VERSION
    else:
        trace = base
        sp = single_prompt(question, schema, hint)
        out = backend.call(sp, None if calls_dir is None else Path(calls_dir) / "2", name=SINGLE_CALL, attempt=1)
        sql = None if out.response is None else parse_single(out.response)
        trace.calls.append(_record(2, SINGLE_CALL, 1, sp, out, None if out.response is None else bool(sql)))
        if out.failure is not None or out.response is None:
            f = out.failure
            timing["exec_ms"] = round((time.monotonic() - t0) * 1000)
            return fail(
                trace, f.type if f else "NoResponse", f"호출 2({SINGLE_CALL}): {f.message if f else '응답 없음'}"
            )
        trace.set_stage("prompt", {"chars": len(sp), "sha256": sha256_text(sp), "kind": SINGLE_CALL})
        trace.set_stage("extract", {"sql": sql or None, "rule": "strip-and-one-fence"})
        trace.sql = sql or None
    timing["exec_ms"] = round((time.monotonic() - t0) * 1000)
    timing["total_ms"] = round((time.monotonic() - t_all) * 1000)
    return RouteResult(trace, choice, len(outcome.response), timing)
