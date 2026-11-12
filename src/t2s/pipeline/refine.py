"""⑪ 고치기: 실행·판정(⑩)과 고치기 호출을 차례마다 되풀이한다(최대 3차례).

MAC-SQL의 동작을 읽고 다시 구현했다. 코드와 문장을 복사하지 않았다(docs/provenance.md 5절).
읽은 곳(https://github.com/wbbeyourself/MAC-SQL, 커밋 31a9df5e0d520be4769be57a4b9022e5e34a14f4):
`core/agents.py` `Refiner.talk` 740-786행, `Refiner._refine` 721-738행, `Selector._get_db_desc_str` 511-523행(외래키 글 꼴),
`core/const.py` 1행(차례 수 3)·565-595행(고치기 템플릿의 칸 구성만), `core/chat_manager.py` 46-62행(차례 흐름),
`core/utils.py` 346-356행(마지막 ```sql 블록)·415-418행(SELECT 붙이기).
옮긴 TS 코드: engines/pure-fix-h/pack/fix.ts `addPrefix`, `parseSqlFromString`, `refinerPrompt`, `fkString`, run.ts의 반복.

고치기 템플릿 `templates/refine.txt`는 새로 쓴 것이다. MAC 템플릿의 일곱 칸과 순서·뜻이 같고, 문장은 옮기지 않았다.
칸: question, knowledge(늘 빈 글), schema(생성 프롬프트에 넣은 스키마 글), foreign_keys, sql, sqlite_error, exception_class.

차례마다:
1. 지금 SQL에 'error' 글이 있으면 실행하지 않고 멈춘다.
2. 실행하고 판정한다. 고치지 않는 판정이면 멈춘다.
3. 고치기 호출 한 번. 답에 ```sql 블록이 없으면 지금 SQL을 그대로 두고 멈춘다(원본은 오류 글을 SQL로 내보낸다. TS처럼 이전 SQL).
4. 있으면 마지막 블록을 새 SQL로 하고 다음 차례로. 마지막 차례에 고친 SQL은 실행하지 않고 최종 SQL이 된다.
호출이 실패하면 예외가 그대로 올라간다(이전 SQL로 돌아가지 않는다).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from t2s.datasource.base import DataSource
from t2s.llm.base import sha256_text
from t2s.pipeline import judge
from t2s.pipeline.trace import RoundRecord

TEMPLATE_FILE = Path(__file__).resolve().parent / "templates" / "refine.txt"
TEMPLATE_FIELDS = ("question", "knowledge", "schema", "foreign_keys", "sql", "sqlite_error", "exception_class")
MAX_ROUNDS = 3
_FIELD = re.compile(r"\{(\w+)\}")
_SQL_BLOCK = re.compile(r"```sql(.*?)```", re.DOTALL)

AskFn = Callable[[str, str, int, Callable[[str], bool | None]], tuple[str, int]]


@cache
def template_text() -> str:
    return TEMPLATE_FILE.read_text(encoding="utf-8")


def template_sha256() -> str:
    return hashlib.sha256(TEMPLATE_FILE.read_bytes()).hexdigest()


def refine_prompt(fields: dict[str, str]) -> str:
    """템플릿의 `{이름}`을 한 번에 바꾼다. 넣은 값 안의 중괄호는 다시 바꾸지 않는다."""

    def fill(m: re.Match) -> str:
        name = m.group(1)
        if name not in TEMPLATE_FIELDS:
            raise KeyError(f"고치기 템플릿에 모르는 칸이 있습니다: {name}")
        return fields[name]

    return _FIELD.sub(fill, template_text())


def add_select_prefix(sql: str) -> str:
    """'SELECT'·'select'로 시작하지 않으면 앞에 'SELECT'를 공백 없이 붙인다(P3와 같게 둔다)."""
    if not sql.startswith("SELECT") and not sql.startswith("select"):
        return "SELECT" + sql
    return sql


def parse_sql_block(text: str) -> tuple[str | None, int]:
    """(마지막 ```sql 블록의 앞뒤 공백을 뺀 글 또는 None, 블록 수)."""
    blocks = [m.group(1).strip() for m in _SQL_BLOCK.finditer(text)]
    return (blocks[-1] if blocks else None), len(blocks)


@dataclass(frozen=True)
class ForeignKeyText:
    text: str
    lines: int
    to_from_primary_key: int
    table_case_fixed: int
    unresolved: int
    duplicates: int

    def to_json(self) -> dict:
        return {
            "text": self.text,
            "lines": self.lines,
            "to_from_primary_key": self.to_from_primary_key,
            "table_case_fixed": self.table_case_fixed,
            "unresolved": self.unresolved,
            "duplicates": self.duplicates,
        }


def _tick(name: str) -> str:
    return name if "`" in name else f"`{name}`"


def foreign_key_text(source: DataSource, tables: list[str]) -> ForeignKeyText:
    """`{테이블}.{열} = {가리킨 테이블}.{가리킨 열}` 줄들. 열 이름만 백틱으로 감싼다. 같은 줄은 한 번."""
    actual = {t.lower(): t for t in tables}
    all_fks = source.foreign_keys()
    pks: dict[str, list[str]] | None = None
    lines: list[str] = []
    to_pk = case_fixed = unresolved = duplicates = 0
    for table in tables:
        for fk in (f for f in all_fks if f.table == table):
            to_table = fk.to_table
            if to_table not in tables:
                fixed = actual.get(to_table.lower())
                if fixed is None:
                    unresolved += 1
                    continue
                to_table = fixed
                case_fixed += 1
            to_column = fk.to_column
            if to_column is None:
                if pks is None:
                    pks = source.primary_keys()
                keys = pks.get(to_table, [])
                if fk.seq >= len(keys):
                    unresolved += 1
                    continue
                to_column = keys[fk.seq]
                to_pk += 1
            link = f"{table}.{_tick(fk.from_column)} = {to_table}.{_tick(to_column)}"
            if link in lines:
                duplicates += 1
                continue
            lines.append(link)
    return ForeignKeyText("\n".join(lines).strip(), len(lines), to_pk, case_fixed, unresolved, duplicates)


@dataclass
class FixState:
    old_sql: str  # ⑨가 꺼낸 SQL
    sql: str  # 최종 SQL
    rounds: list[RoundRecord] = field(default_factory=list)
    fk: ForeignKeyText | None = None  # 처음 고칠 때 한 번 만든다
    refine_calls: int = 0
    stop: str = "max_rounds"
    final: str = "old"  # old | new
    final_executed: bool = False


def _record_exec(r: RoundRecord, result) -> None:
    r.status = result.status
    r.row_count = result.row_count
    r.column_count = result.column_count
    r.has_null = judge.has_null(result.head)
    if result.status in ("rows", "empty"):
        r.head_text = str(result.head)
    else:
        r.head_text = result.error
        r.sqlite_error = result.error
    r.exception_class = result.exception_class or None
    r.denied = result.denied
    r.elapsed_ms = result.elapsed_ms


def refine_loop(
    sql: str,
    *,
    question: str,
    schema_text: str,
    tables: list[str],
    source: DataSource,
    ask: AskFn,
    rounds: int = MAX_ROUNDS,
    exec_timeout_seconds: float = 120,
    keep_rows: int = 5,
    state: FixState | None = None,
) -> FixState:
    """state를 주면 그것을 채운다(호출이 실패해 예외가 올라가도 그때까지의 기록이 남는다)."""
    fix = state if state is not None else FixState(old_sql=sql, sql=sql)
    for round_no in range(1, rounds + 1):
        r = RoundRecord(round=round_no, old_sql=sql)
        fix.rounds.append(r)
        if judge.has_error_substring(sql):
            r.reason, r.refine = "skipped_error_substring", False
            fix.stop = "skipped_error_substring"
            break
        r.executed = True
        try:
            result = source.execute(sql, exec_timeout_seconds, keep_rows)
        except Exception as e:  # noqa: BLE001 — DB를 열지 못하는 등. 고치지 않고 멈춘다
            r.status, r.sqlite_error, r.head_text = "exec_failed", f"{type(e).__name__}: {e}", None
            r.reason, r.refine = "skipped_exec_failed", False
            fix.stop = "skipped_exec_failed"
            break
        _record_exec(r, result)
        decision = judge.decide(result)
        r.reason, r.refine = decision.reason, decision.refine
        if not decision.refine:
            fix.stop = judge.stop_reason(decision.reason)
            break
        if fix.fk is None:
            fix.fk = foreign_key_text(source, tables)
        sql_arg = add_select_prefix(sql)
        r.sql_arg_prefixed = sql_arg != sql
        prompt = refine_prompt(
            {
                "question": question,
                "knowledge": "",
                "schema": schema_text,
                "foreign_keys": fix.fk.text,
                "sql": sql_arg,
                "sqlite_error": decision.sqlite_error or "",
                "exception_class": decision.exception_class or "",
            }
        )
        r.prompt_chars = len(prompt)
        r.prompt_sha256 = sha256_text(prompt)
        fix.refine_calls += 1
        fix.stop = "refine_call_failed"  # 호출이 실패하면 이 값으로 남는다
        response, k = ask("refine_sql", prompt, round_no, lambda text: parse_sql_block(text)[0] is not None)
        r.call_k = k
        new_sql, blocks = parse_sql_block(response)
        r.blocks = blocks
        if new_sql is None:
            r.parse = "no_sql_block"
            fix.stop = "no_sql_block"
            break
        r.parse = "sql_block"
        r.new_sql = new_sql
        fix.final = "new"
        sql = new_sql
        fix.sql = new_sql
        fix.stop = "max_rounds"
    last = fix.rounds[-1]
    fix.final_executed = last.executed and last.new_sql is None
    return fix
