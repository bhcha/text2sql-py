"""⑩ 실행 결과 판정: 고칠지 말지와 그 까닭.

MAC-SQL의 동작을 읽고 다시 구현했다. 코드와 문장을 복사하지 않았다(docs/provenance.md 5절).
읽은 곳(https://github.com/wbbeyourself/MAC-SQL, 커밋 31a9df5e0d520be4769be57a4b9022e5e34a14f4):
`core/agents.py` `Refiner._execute_sql` 671-698행, `Refiner._is_need_refine` 700-719행, `Refiner.talk` 756-779행.
옮긴 TS 코드: engines/pure-fix-h/pack/fix.ts `hasErrorSubstring`, `decide`, `stopReason`.
실행은 DataSource.execute가 한다(읽기 전용, 끝까지 읽고 앞 5행만 남긴다).

판정 순서: 시간 초과면 고치지 않음 → 오류면 고침 → 0행이면 고침 → 앞 5행에 NULL이 있으면 고침 → 그 밖은 그대로.
0행·NULL일 때 고치기 프롬프트에 넣는 두 안내 문구는 새로 썼다.
"""

from __future__ import annotations

from dataclasses import dataclass

from t2s.datasource.base import ExecResult

NO_ROWS_MESSAGE = "the query ran without error but returned no rows"
NULL_VALUE_MESSAGE = "the first rows of the result contain NULL values; filter them out with IS NOT NULL unless the question asks for them"


@dataclass(frozen=True)
class Decision:
    refine: bool
    reason: str
    sqlite_error: str | None  # 고칠 때 템플릿의 sqlite_error 칸에 넣는 글
    exception_class: str | None  # 고칠 때 템플릿의 exception_class 칸에 넣는 글(0행·NULL이면 "")


def has_error_substring(sql: str) -> bool:
    """SQL 안에 소문자 'error'가 부분 글로 있으면 실행하지 않고 멈춘다."""
    return "error" in sql


def has_null(head: list[tuple]) -> bool:
    return any(v is None for row in head for v in row)


def keep(reason: str) -> Decision:
    return Decision(False, reason, None, None)


def decide(result: ExecResult) -> Decision:
    if result.status == "timeout":
        return keep("skipped_timeout")
    if result.status == "error":
        return Decision(True, "error", result.error, result.exception_class)
    if result.row_count == 0:
        return Decision(True, "no_rows", NO_ROWS_MESSAGE, "")
    if has_null(result.head):
        return Decision(True, "null_value", NULL_VALUE_MESSAGE, "")
    return keep("ok")


def stop_reason(reason: str) -> str:
    """고치지 않는 판정의 까닭 → 반복이 멈춘 까닭."""
    if reason == "ok":
        return "no_refine_needed"
    if reason in ("skipped_error_substring", "skipped_timeout", "skipped_exec_failed"):
        return reason
    raise ValueError(f"멈추는 까닭이 아닙니다: {reason}")
