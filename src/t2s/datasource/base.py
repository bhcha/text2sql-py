"""데이터 소스 프로토콜과 자료형.

SQL 방언이 드러나는 질의는 구현(예: sqlite.py) 안에 둔다. 이 파일은 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class DataSourceError(Exception):
    """소스를 열거나 읽지 못했을 때."""


@dataclass(frozen=True)
class Dialect:
    name: str  # "SQLite" — 프롬프트의 "valid SQLite" 자리
    quote_char: str = "`"

    def quote(self, identifier: str) -> str:
        """이름을 감싼다. 이름 안의 감싸는 글자는 두 번 쓴다."""
        q = self.quote_char
        return q + identifier.replace(q, q + q) + q


@dataclass(frozen=True)
class Fingerprint:
    sha256: str
    size: int


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    decl_type: str
    notnull: bool
    pk: int  # table_info의 pk 번호(0이면 기본 키 아님)


@dataclass(frozen=True)
class ForeignKey:
    table: str
    from_column: str
    to_table: str
    to_column: str | None
    id: int
    seq: int


@dataclass(frozen=True)
class SampleRows:
    columns: list[str]
    rows: list[tuple]


@dataclass(frozen=True)
class TextValueStats:
    sum_length: int | None
    count: int
    failed: bool


@dataclass(frozen=True)
class ProfileValue:
    repr: str
    type: str
    count: int


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    rows: int
    nulls: int
    distinct: int
    chess_distinct: int
    categorical: bool
    types: dict[str, int]
    kind: str  # empty | values | range | length | counts
    values: list[ProfileValue] | None = None
    min: str | None = None
    max: str | None = None
    min_length: int | None = None
    max_length: int | None = None

    def to_json(self) -> dict:
        """TS profile.json과 같은 키 순서. 해당 없는 키는 뺀다."""
        out: dict = {
            "name": self.name,
            "rows": self.rows,
            "nulls": self.nulls,
            "distinct": self.distinct,
            "chess_distinct": self.chess_distinct,
            "categorical": self.categorical,
            "types": dict(self.types),
            "kind": self.kind,
        }
        if self.kind == "values":
            out["values"] = [{"repr": v.repr, "type": v.type, "count": v.count} for v in self.values or []]
        elif self.kind == "range":
            out["min"] = self.min
            out["max"] = self.max
        elif self.kind == "length":
            out["min_length"] = self.min_length
            out["max_length"] = self.max_length
        return out


@dataclass(frozen=True)
class ExecResult:
    status: str  # rows | empty | error | timeout
    row_count: int
    column_count: int
    columns: list[str]
    head: list[tuple]  # 앞 keep_rows 행
    error: str  # 성공이면 ""
    exception_class: str  # 성공이면 ""
    denied: bool  # authorizer가 거절했는가
    elapsed_ms: int


class DataSource(Protocol):
    kind: str  # "sqlite"
    dialect: Dialect

    def describe(self) -> str: ...  # 사람에게 보일 위치(파일 경로)

    def fingerprint(self) -> Fingerprint: ...

    def side_files(self) -> dict[str, tuple[int, int]]: ...  # 부속 파일 이름 → (크기, 수정 시각 ns)

    def tables(self) -> list[str]: ...

    def ddl(self, table: str) -> str: ...

    def sample_rows(self, table: str, n: int) -> SampleRows: ...

    def columns(self, table: str) -> list[ColumnInfo]: ...

    def primary_keys(self) -> dict[str, list[str]]: ...

    def foreign_keys(self) -> list[ForeignKey]: ...

    def table_row_count(self, table: str) -> int: ...

    def text_value_stats(self, table: str, column: str) -> TextValueStats: ...

    def distinct_values(self, table: str, column: str) -> list: ...

    def column_profile(self, table: str, column: str) -> ColumnProfile: ...

    def execute(self, sql: str, timeout_seconds: float, keep_rows: int) -> ExecResult: ...
