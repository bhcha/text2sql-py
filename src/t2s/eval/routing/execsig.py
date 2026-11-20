"""예측 SQL과 정답 SQL을 읽기 전용으로 다시 실행해 실행 결과 신호(오류·0행·NULL)와 행 해시를 얻는다.

AGENTS 규칙 10: 연결은 t2s.eval.study.connect(mode=ro&immutable=1, query_only, authorizer)로만, 한 번에 한 문장,
실행 앞뒤 DB sha256 비교는 호출하는 쪽(__main__)이 bird.db_state로 한다. 행 내용은 디스크에 쓰지 않는다.
예측 SQL 쪽은 캐시에 행 해시를 쓴다(F1용). 정답 SQL 쪽은 캐시에 status·행 수·NULL 여부만 쓰고, 정답 행 해시나
거기서 나온 값(set_digest 등)은 디스크에 쓰지 않는다. 정답 행 해시는 실행할 때마다 메모리에서만 만든다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path

from t2s.eval.study import connect

DEFAULT_TIMEOUT = 30.0  # 공식 채점기 meta_time_out 기본과 같다
MAX_ROWS = 200_000  # 이보다 많으면 행 해시를 남기지 않는다(F1 없음)
KEEP_ROWS = 5  # 앞 5행 NULL 신호(P3 judge의 keep_rows와 같은 뜻)
HASH_CHARS = 16


def sql_key(db_id: str, sql: str) -> str:
    return db_id + ":" + hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _canon(v) -> list:
    """행 값의 고정 표기. 정수와 같은 값의 실수(1과 1.0)는 같은 표기다(공식 EX의 집합 비교와 같은 성질)."""
    if v is None:
        return ["n"]
    if isinstance(v, bool):
        return ["i", int(v)]
    if isinstance(v, int):
        return ["i", v]
    if isinstance(v, float):
        if v.is_integer():
            return ["i", int(v)]
        return ["f", repr(v)]
    if isinstance(v, bytes):
        return ["b", v.hex()]
    return ["s", str(v)]


def row_hash(row) -> str:
    text = json.dumps([_canon(v) for v in row], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_CHARS]


def set_digest(hashes: dict | None) -> str | None:
    """행 집합(중복 무시)의 요약 해시. 두 결과의 집합이 같은지 견줄 때 쓴다."""
    if hashes is None:
        return None
    return hashlib.sha256("\n".join(sorted(hashes)).encode()).hexdigest()[:HASH_CHARS]


def run_query(con: sqlite3.Connection, sql: str, timeout_s: float = DEFAULT_TIMEOUT, max_rows: int = MAX_ROWS) -> dict:
    """한 문장 실행. status: rows·empty·error·timeout. hashes: 행 해시 → 개수(행이 너무 많으면 None)."""
    started = time.monotonic()
    con.set_progress_handler(lambda: int(time.monotonic() - started > timeout_s), 1000)
    out = {
        "status": "error",
        "row_count": 0,
        "column_count": 0,
        "null_head": False,
        "null_any": False,
        "too_many": False,
        "reason": None,
        "hashes": None,
    }
    try:
        cur = con.execute(sql)
        out["column_count"] = len(cur.description or [])
        counts: Counter = Counter()
        n = 0
        while batch := cur.fetchmany(1000):
            for row in batch:
                if any(v is None for v in row):
                    out["null_any"] = True
                    if n < KEEP_ROWS:
                        out["null_head"] = True
                if n < max_rows:
                    counts[row_hash(row)] += 1
                n += 1
        out["row_count"] = n
        out["status"] = "rows" if n else "empty"
        if n > max_rows:
            out["too_many"] = True
        else:
            out["hashes"] = dict(counts)
    except sqlite3.Error as e:
        text = str(e).lower()
        if "interrupted" in text:
            out["status"], out["reason"] = "timeout", "timeout"
        elif "not authorized" in text or "authorization denied" in text:
            out["reason"] = "denied"
        elif "one statement at a time" in text:
            out["reason"] = "multi_statement"
        else:
            out["reason"] = "error"
    except (ValueError, OverflowError, MemoryError):
        out["reason"] = "error"
    finally:
        con.set_progress_handler(None, 0)
    out["ms"] = round((time.monotonic() - started) * 1000, 1)
    return out


NO_SQL = {
    "status": "error",
    "row_count": 0,
    "column_count": 0,
    "null_head": False,
    "null_any": False,
    "too_many": False,
    "reason": "no_sql",
    "hashes": None,
    "ms": 0.0,
}


class Executor:
    """DB별 연결을 하나씩 열어 두고 실행한다. 같은 (DB, SQL)은 캐시에서 돌려준다."""

    def __init__(self, db_paths: dict[str, Path], timeout_s: float = DEFAULT_TIMEOUT, cache: dict | None = None):
        self.db_paths = db_paths
        self.timeout_s = timeout_s
        self.cache: dict[str, dict] = {} if cache is None else cache
        self.cons: dict[str, sqlite3.Connection] = {}
        self.executed = 0

    def run(self, db_id: str, sql: str | None) -> dict:
        if not sql or not sql.strip():
            return dict(NO_SQL)
        key = sql_key(db_id, sql)
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        con = self.cons.get(db_id)
        if con is None:
            con = self.cons[db_id] = connect(self.db_paths[db_id])
        res = run_query(con, sql, self.timeout_s)
        self.executed += 1
        self.cache[key] = res
        return res

    def close(self) -> None:
        for con in self.cons.values():
            con.close()
        self.cons.clear()


_WORKER: Executor | None = None


def init_worker(db_paths: dict, timeout_s: float) -> None:
    """multiprocessing 작업자 준비(작업자마다 DB 연결을 따로 연다)."""
    global _WORKER
    _WORKER = Executor({k: Path(v) for k, v in db_paths.items()}, timeout_s)


def work(task: tuple[str, list[tuple[str, str]]]) -> dict:
    """작업자: 한 문항의 (키, SQL) 목록을 실행해 키 → 결과를 돌려준다."""
    db_id, items = task
    assert _WORKER is not None
    return {key: _WORKER.run(db_id, sql) for key, sql in items}


def signals(res: dict) -> dict:
    """실행 결과 신호. error: SQL 없음·오류·거절·시간 초과. empty: 0행. null: 앞 5행 안 NULL."""
    err = res["status"] in ("error", "timeout")
    return {"error": err, "empty": res["status"] == "empty", "null": (not err) and bool(res["null_head"])}


GOLD_FIELDS = ("status", "row_count", "null_head", "null_any")  # 정답 쪽 캐시 항목에 남기는 칸(이것뿐)


def gold_entry(res: dict) -> dict:
    """정답 SQL 실행 결과의 디스크용 판: status·행 수·NULL 여부만. 행 해시와 거기서 나온 값은 없다."""
    return {"side": "gold", **{k: res[k] for k in GOLD_FIELDS}}


def load_cache(path: Path, drop: frozenset | set = frozenset()) -> dict:
    """캐시를 읽는다. 정답 쪽 항목(side=gold)과 drop에 든 키(정답 SQL 키)는 버린다. 정답은 늘 다시 실행한다."""
    out = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                key = row.pop("key")
                if row.get("side") == "gold" or key in drop:
                    continue
                out[key] = row
    return out


def append_cache(path: Path, entries: dict, gold_keys: frozenset | set = frozenset()) -> None:
    """캐시에 덧붙인다. gold_keys에 든 키(정답 SQL 키, 예측 SQL이 정답과 글자까지 같을 때도)는 gold_entry 판으로 쓴다."""
    with open(path, "a", encoding="utf-8") as f:
        f.writelines(
            json.dumps(
                {"key": key, **(gold_entry(row) if key in gold_keys else row)},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
            for key, row in entries.items()
        )
