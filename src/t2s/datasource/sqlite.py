"""SQLite 데이터 소스. 읽기 전용으로만 연다.

읽기 전용 보장(AGENTS 규칙 10):
- `file:<경로>?mode=ro&immutable=1` URI로 연다. WAL 머리를 가진 DB(card_games)도 부속 파일을 만들지 않는다.
- 모든 연결에 authorizer를 걸어 읽기(SELECT·READ·FUNCTION·RECURSIVE)와 읽기용 PRAGMA 몇 개 밖의 동작을 거절한다.
- 한 번에 한 문장만 받는다(`sqlite3` 모듈 기본 동작). `executescript`는 쓰지 않는다.
- 시간 제한은 진행 처리기(set_progress_handler)로 마감 시각을 넘으면 끊는다.

`sqlite3.connect`는 이 파일의 `_connect` 한 곳에서만 부른다(tests/test_import_rules.py가 검사한다).
값 목록·프로파일 질의의 출처는 docs/provenance.md 3절이다.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import time
import urllib.parse
from pathlib import Path

from t2s.datasource.base import (
    ColumnInfo,
    ColumnProfile,
    DataSourceError,
    Dialect,
    ExecResult,
    Fingerprint,
    ForeignKey,
    ProfileValue,
    SampleRows,
    TextValueStats,
)

SQLITE_DIALECT = Dialect(name="SQLite", quote_char="`")
SIDE_SUFFIXES = ("-wal", "-shm", "-journal")
READ_PRAGMAS = frozenset(
    {"table_info", "table_xinfo", "table_list", "foreign_key_list", "index_list", "index_info", "index_xinfo"}
)
ALLOWED_ACTIONS = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
)
STORAGE_CLASSES = ("integer", "real", "text", "blob")
# CHESS schema_generator.py 103-104행의 범주형 기준: DISTINCT … LIMIT 21의 행 수가 20보다 작으면 범주형.
CATEGORICAL_PROBE = 21
CATEGORICAL_LIMIT = 20
PROGRESS_STEPS = 1000
HASH_CHUNK = 1 << 20


def _authorize(action: int, arg1: str | None, arg2: str | None, db_name: str | None, trigger: str | None) -> int:
    if action in ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and arg1 is not None and arg1.lower() in READ_PRAGMAS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _decode_text(raw: bytes) -> str:
    # UTF-8로 풀리지 않는 TEXT도 예외 없이 대체 글자로 읽는다(node:sqlite와 같은 쪽).
    return raw.decode("utf-8", errors="replace")


def sqlite_binary_key(value: object) -> tuple:
    """SQLite `COLLATE BINARY` 순서의 정렬 키: 숫자(값으로 비교) < 글(UTF-8 바이트 순) < BLOB(바이트 순)."""
    if isinstance(value, bool):
        raise TypeError("bool은 SQLite 값이 아니다")
    if isinstance(value, (int, float)):
        return (0, value)
    if isinstance(value, str):
        return (1, value.encode("utf-8"))
    if isinstance(value, (bytes, bytearray, memoryview)):
        return (2, bytes(value))
    raise TypeError(f"정렬할 수 없는 값의 형식: {type(value).__name__}")


class SqliteSource:
    kind = "sqlite"
    dialect = SQLITE_DIALECT

    def __init__(self, path: Path | str) -> None:
        self.path = Path(os.path.realpath(path))

    # ── 연결 ──────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise DataSourceError(f"DB 파일이 없습니다: {self.path}")
        uri = "file:" + urllib.parse.quote(str(self.path)) + "?mode=ro&immutable=1"
        try:
            conn = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as e:
            raise DataSourceError(f"DB를 열지 못했습니다: {self.path} ({e})") from e
        conn.text_factory = _decode_text
        conn.set_authorizer(_authorize)
        return conn

    def _all(self, sql: str, params: tuple = ()) -> list[tuple]:
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def _q(self, name: str) -> str:
        return self.dialect.quote(name)

    # ── 정보 ──────────────────────────────────────────────

    def describe(self) -> str:
        return str(self.path)

    def fingerprint(self) -> Fingerprint:
        h = hashlib.sha256()
        with open(self.path, "rb") as f:
            while chunk := f.read(HASH_CHUNK):
                h.update(chunk)
        return Fingerprint(sha256=h.hexdigest(), size=os.stat(self.path).st_size)

    def side_files(self) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        for suffix in SIDE_SUFFIXES:
            side = Path(str(self.path) + suffix)
            if side.exists():
                st = side.stat()
                out[side.name] = (st.st_size, st.st_mtime_ns)
        return out

    def tables(self) -> list[str]:
        return [str(r[0]) for r in self._all("SELECT name FROM sqlite_master WHERE type='table'")]

    def ddl(self, table: str) -> str:
        rows = self._all("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,))
        if not rows:
            raise DataSourceError(f"테이블이 없습니다: {table}")
        return str(rows[0][0])

    def sample_rows(self, table: str, n: int) -> SampleRows:
        conn = self._connect()
        try:
            cur = conn.execute(f"SELECT * FROM {self._q(table)} LIMIT {int(n)}")
            rows = cur.fetchall()
            columns = [d[0] for d in cur.description or []]
        finally:
            conn.close()
        return SampleRows(columns=columns, rows=rows)

    def columns(self, table: str) -> list[ColumnInfo]:
        rows = self._all(f"PRAGMA table_info({self._q(table)})")
        return [ColumnInfo(name=str(r[1]), decl_type=str(r[2] or ""), notnull=bool(r[3]), pk=int(r[5])) for r in rows]

    def primary_keys(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for table in self.tables():
            pks = sorted((c for c in self.columns(table) if c.pk > 0), key=lambda c: c.pk)
            out[table] = [c.name for c in pks]
        return out

    def foreign_keys(self) -> list[ForeignKey]:
        out: list[ForeignKey] = []
        for table in self.tables():
            for r in self._all(f"PRAGMA foreign_key_list({self._q(table)})"):
                # (id, seq, table, from, to, on_update, on_delete, match)
                out.append(
                    ForeignKey(
                        table=table,
                        from_column=str(r[3]),
                        to_table=str(r[2]),
                        to_column=None if r[4] is None else str(r[4]),
                        id=int(r[0]),
                        seq=int(r[1]),
                    )
                )
        return out

    def table_row_count(self, table: str) -> int:
        return int(self._all(f"SELECT COUNT(*) FROM {self._q(table)}")[0][0])

    # ── 값 색인 질의(CHESS preprocess.py 44-51행, 65행) ──────────

    def text_value_stats(self, table: str, column: str) -> TextValueStats:
        t, c = self._q(table), self._q(column)
        sql = (
            f"SELECT SUM(LENGTH(unique_values)), COUNT(unique_values) "
            f"FROM (SELECT DISTINCT {c} AS unique_values FROM {t} WHERE {c} IS NOT NULL) AS subquery"
        )
        try:
            total, count = self._all(sql)[0]
        except sqlite3.Error:
            return TextValueStats(sum_length=0, count=0, failed=True)
        return TextValueStats(sum_length=None if total is None else int(total), count=int(count), failed=False)

    def distinct_values(self, table: str, column: str) -> list:
        t, c = self._q(table), self._q(column)
        try:
            return [r[0] for r in self._all(f"SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL")]
        except sqlite3.Error:
            return []

    # ── 열 프로파일(TS engines/pure-profile/pack/profile.ts를 다시 구현) ──

    def column_profile(self, table: str, column: str) -> ColumnProfile:
        t, c = self._q(table), self._q(column)
        conn = self._connect()
        try:

            def q(sql: str) -> list[tuple]:
                return conn.execute(sql).fetchall()

            rows = 0
            nulls = 0
            types: dict[str, int] = {}
            for kind, n in q(f"SELECT typeof({c}) AS k, COUNT(*) FROM {t} GROUP BY 1"):
                count = int(n)
                rows += count
                if kind == "null":
                    nulls += count
                elif kind in STORAGE_CLASSES:
                    types[kind] = count
                else:
                    raise DataSourceError(f"예상 밖의 typeof {kind!r}: {table}.{column}")
            # CHESS schema_generator.py 103-104행과 같은 질의(이름 감싸기만 다르다). NULL도 값 하나로 센다.
            chess = int(
                q(f"SELECT COUNT(*) FROM (SELECT DISTINCT {c} FROM {t} LIMIT {CATEGORICAL_PROBE}) AS subquery;")[0][0]
            )
            categorical = chess < CATEGORICAL_LIMIT
            distinct = int(q(f"SELECT COUNT(DISTINCT {c}) FROM {t}")[0][0])
            base = {
                "name": column,
                "rows": rows,
                "nulls": nulls,
                "distinct": distinct,
                "chess_distinct": chess,
                "categorical": categorical,
                "types": types,
            }
            if rows - nulls == 0:
                return ColumnProfile(**base, kind="empty")
            if categorical:
                groups = q(f"SELECT {c}, typeof({c}), COUNT(*) FROM {t} WHERE {c} IS NOT NULL GROUP BY {c}")
                if len(groups) != distinct:
                    raise DataSourceError(
                        f"{table}.{column}: GROUP BY 줄 {len(groups)}개, COUNT(DISTINCT) {distinct}개로 다릅니다"
                    )
                ordered = sorted(groups, key=lambda g: (-int(g[2]), sqlite_binary_key(g[0])))
                values = [ProfileValue(repr=repr(v), type=str(k), count=int(n)) for v, k, n in ordered]
                return ColumnProfile(**base, kind="values", values=values)
            if all(k in ("integer", "real") for k in types):
                lo, hi = q(f"SELECT MIN({c}), MAX({c}) FROM {t}")[0]
                return ColumnProfile(**base, kind="range", min=repr(lo), max=repr(hi))
            if "text" in types:
                lo, hi = q(f"SELECT MIN(length({c})), MAX(length({c})) FROM {t} WHERE typeof({c}) = 'text'")[0]
                return ColumnProfile(**base, kind="length", min_length=int(lo), max_length=int(hi))
            return ColumnProfile(**base, kind="counts")
        finally:
            conn.close()

    # ── 실행 ──────────────────────────────────────────────

    def execute(self, sql: str, timeout_seconds: float, keep_rows: int) -> ExecResult:
        started = time.monotonic()
        deadline = started + timeout_seconds
        interrupted = False

        def progress() -> int:
            nonlocal interrupted
            if time.monotonic() > deadline:
                interrupted = True
                return 1
            return 0

        def elapsed() -> int:
            return round((time.monotonic() - started) * 1000)

        conn = self._connect()
        conn.set_progress_handler(progress, PROGRESS_STEPS)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            columns = [d[0] for d in cur.description or []]
            head: list[tuple] = []
            count = 0
            while True:
                batch = cur.fetchmany(1000)
                if not batch:
                    break
                if len(head) < keep_rows:
                    head.extend(batch[: keep_rows - len(head)])
                count += len(batch)
            return ExecResult(
                status="rows" if count else "empty",
                row_count=count,
                column_count=len(columns),
                columns=columns,
                head=head,
                error="",
                exception_class="",
                denied=False,
                elapsed_ms=elapsed(),
            )
        except sqlite3.Error as e:
            if interrupted:
                return _failed("timeout", f"exceeded {timeout_seconds:g} seconds", str(type(e)), False, elapsed())
            message = " ".join(str(a) for a in e.args)
            # 거절은 대개 "not authorized"이고, VACUUM INTO 같은 몇 문장은 "authorization denied"(SQLITE_AUTH)로 온다.
            denied = "not authorized" in message or getattr(e, "sqlite_errorname", "") == "SQLITE_AUTH"
            return _failed("error", message, str(type(e)), denied, elapsed())
        except Exception as e:  # noqa: BLE001 — 모델 SQL의 어떤 실패도 판정 결과로 돌려준다
            return _failed("error", str(e.args), type(e).__name__, False, elapsed())
        finally:
            conn.close()


def _failed(status: str, error: str, exception_class: str, denied: bool, elapsed_ms: int) -> ExecResult:
    return ExecResult(
        status=status,
        row_count=0,
        column_count=0,
        columns=[],
        head=[],
        error=error,
        exception_class=exception_class,
        denied=denied,
        elapsed_ms=elapsed_ms,
    )
