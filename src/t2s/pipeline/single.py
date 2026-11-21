"""단일 호출 경로(route = "single"): 질문과 CREATE 원문만 주는 호출 하나로 SQL을 받는다. 하네스 단계는 돌지 않는다.

출처(AGENTS 규칙 4): 형제 저장소 text2sql-model-baseline, 커밋 df10b12, `baseline/prompt.py`
- 5-7행 `TEMPLATE`(단일 호출 틀): 글자 그대로 `SINGLE_TEMPLATE`로 옮겼다.
- 10-14행 `render`(힌트 붙이기): 동작을 옮기고 힌트 줄의 이름표를 `External Knowledge:`로 바꿨다(그 저장소는 다른
  낱말을 쓴다. 이 저장소의 생성 패키지는 그 낱말을 문자열로 갖지 않는다, AGENTS 규칙 2·tests/test_import_rules.py).
- 17-20행 `parse`(응답 읽기): 같은 정규식과 규칙으로 옮겼다. 앞뒤 공백을 떼고, 응답 전체를 감싼 sql·sqlite·이름 없는
  코드 울타리 하나만 벗긴다. SELECT 보태기, 설명 지우기, 세미콜론 고치기, 실행 피드백은 하지 않는다.
바꾼 것: 스키마를 `DataSource`(`tables`·`ddl`)에서 만든다(사용자 테이블을 이름순으로, `sqlite_`로 시작하는 테이블은
뺀다), 기록을 `Trace`로 남긴다, 빈 응답은 SQL 없음으로 본다.
이 저장소에서는 배분 측정 코드(커밋 38f2740의 `src/t2s/pipeline/route.py`, 2026-11-21 지움)에 같은 틀·규칙이 있었고
이 파일은 그 부분을 되살린 것이다. 그 저장소는 최상위 LICENSE 파일이 없고, 커밋 작성자가 이 저장소와 같은 사용자다
(작성자 이름으로 확인). 라이선스 조건은 미확인이다.
측정 조건 차이: claude-code 백엔드는 이 저장소의 고정 시스템 프롬프트(`llm/claude_cli.py`)를 그대로 쓴다. 그 저장소는
CLI 기본 시스템 프롬프트를 쓰므로 claude 단일 호출 수치는 그 저장소 수치와 같은 조건이 아니다. codex-cli는 `bare`
(환경 블록·내장 스킬 목록만 끔, `llm/codex_cli.py`)로 그 저장소 `baseline/model.py` 32-58행과 같은 설정이다.
힌트(evidence used)는 `hint` 인자 하나로만 받아 이름표 줄 아래에 글자 그대로 넣는다. None이면 이름표 줄이 없다.
"""

from __future__ import annotations

import platform
import re
import sqlite3
import time
from pathlib import Path

from t2s.datasource.base import DataSource
from t2s.llm.base import LlmBackend, sha256_text
from t2s.pipeline.run import HINT_RULES_VERSION
from t2s.pipeline.trace import CallRecord, Trace

SINGLE_RULES_VERSION = "single/1"
SINGLE_CALL = "single_sql"
PARSE_RULE = "strip-and-one-fence"
SINGLE_TEMPLATE = (
    "Write a SQLite query for the question. Return only SQL.\n\nSchema:\n{schema}\n\nQuestion:\n{question}\n"
)
KNOWLEDGE_LABEL = "External Knowledge:"
_FENCE = re.compile(r"```(?:sql|sqlite)?\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)


def create_schema(source: DataSource) -> str:
    """사용자 테이블 CREATE 원문을 이름순으로 빈 줄로 잇는다(sqlite_ 로 시작하는 테이블은 뺀다)."""
    names = sorted(t for t in source.tables() if not t.lower().startswith("sqlite_"))
    return "\n\n".join(source.ddl(t) for t in names)


def single_prompt(question: str, schema: str, hint: str | None) -> str:
    text = SINGLE_TEMPLATE.format(schema=schema, question=question)
    if hint is not None:
        text += f"\n{KNOWLEDGE_LABEL}\n{hint}\n"
    return text


def parse_single(response: str) -> str:
    text = response.strip()
    m = _FENCE.fullmatch(text)
    return m.group(1).strip() if m else text


def answer_single(
    question: str,
    source: DataSource,
    llm: LlmBackend,
    *,
    calls_dir: Path | None = None,
    hint: str | None = None,
) -> Trace:
    """호출 하나(calls/1). 실패는 trace.exception, 빈 응답은 SQL 없음(extract 단계의 sql이 None)."""
    started = time.monotonic()
    trace = Trace(question=question)
    trace.versions = {
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "single_rules": SINGLE_RULES_VERSION,
    }
    if hint is not None:
        trace.versions["hint_rules"] = HINT_RULES_VERSION
    text = single_prompt(question, create_schema(source), hint)
    trace.set_stage("prompt", {"chars": len(text), "sha256": sha256_text(text), "kind": SINGLE_CALL})
    outcome = llm.call(text, None if calls_dir is None else Path(calls_dir) / "1", name=SINGLE_CALL, attempt=1)
    meta = outcome.meta
    sql = None if outcome.response is None else (parse_single(outcome.response) or None)
    call = CallRecord(
        k=1,
        name=SINGLE_CALL,
        attempt=1,
        prompt_sha256=meta.get("prompt_sha256", sha256_text(text)),
        prompt_chars=meta.get("prompt_chars", len(text)),
        response_sha256=meta.get("response_sha256"),
        response_chars=None if outcome.response is None else len(outcome.response),
        parse_ok=None if outcome.response is None else sql is not None,
        elapsed_ms=int(meta.get("elapsed_ms", 0)),
        tool_calls=int(meta.get("tool_calls", 0)),
        usage=meta.get("usage"),
        failure=None if outcome.failure is None else outcome.failure.type,
    )
    trace.calls.append(call)
    trace.set_stage(
        "model",
        {
            "k": 1,
            "model": llm.settings.model,
            "reasoning_effort": llm.settings.reasoning_effort,
            "elapsed_ms": call.elapsed_ms,
            "tool_calls": call.tool_calls,
            "prompt_chars": call.prompt_chars,
            "usage": call.usage,
            "failure": call.failure,
        },
    )
    if outcome.failure is not None or outcome.response is None:
        f = outcome.failure
        trace.exception = {
            "type": f.type if f else "NoResponse",
            "message": f"호출 1({SINGLE_CALL}): {f.message if f else '응답 없음'}",
        }
        trace.sql = None
    else:
        trace.set_stage("extract", {"sql": sql, "rule": PARSE_RULE})
        trace.sql = sql
    trace.timing["total_ms"] = round((time.monotonic() - started) * 1000)
    return trace
