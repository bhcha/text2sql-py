"""값 색인: 값 모으기와 3-gram 정확 자카드 역색인.

출처(복사 후 수정): CHESS `src/database_utils/db_values/preprocess.py` `_get_unique_values` 10-73행
(https://github.com/ShayanTalaei/CHESS, 커밋 3d6e835f858d26885d21d4bc0215aeecf855efbe, Apache License 2.0,
LICENSES/Apache-2.0-CHESS.txt). 열을 고르는 규칙(기본 키 이름 제외, 이름 조건, 크기 조건)은 원본 그대로다.
바꾼 것:
- SQL 실행을 DataSource(text_value_stats, distinct_values)로 옮겼다. 이름은 늘 제대로 감싼다(원본·TS는 감싸지 않음).
- 원본의 질의 시간 제한(480초)을 두지 않았다(읽기 전용, 등록 때 한 번).
- 크기 질의가 실패한 열 수를 센다(query_errors).
- MinHash LSH(75-153행)는 가져오지 않았다. 대신 정확한 3-gram 자카드 역색인을 새로 썼다(자체 코드, TS
  engines/pure-values/pack/values.ts buildShingleIndex·jaccardTopN, index.ts serializeShingles와 같은 정의).

TS 산출물과 같은 바이트로 쓴다.
"""

from __future__ import annotations

import hashlib
import json
import sys
from array import array
from dataclasses import dataclass, field
from pathlib import Path

from t2s.datasource.base import DataSource

EXCLUDED_NAME_PARTS = ("_id", " id", "url", "email", "web", "time", "phone", "date", "address")
N_GRAM = 3
TOP_N = 10
INDEX_FILES = ("values.json", "shingles.json", "postings.u32", "offsets.u32", "sizes.u32")
SKIP_TABLE = "sqlite_sequence"

UniqueValues = list[tuple[str, list[tuple[str, list[str]]]]]


def excluded_by_name(column: str) -> bool:
    """이름 조건(원본 40행): 빼면 True."""
    lower = column.lower()
    return any(part in lower for part in EXCLUDED_NAME_PARTS) or column.endswith("Id")


def keep_by_size(column: str, sum_length: int, count: int) -> bool:
    """크기 조건(원본 62행): 값을 모으면 True."""
    average = sum_length / count
    return (
        ("name" in column.lower() and sum_length < 5_000_000)
        or (sum_length < 2_000_000 and average < 25)
        or count < 100
    )


@dataclass
class UniqueValuesResult:
    values: UniqueValues
    primary_key_names: list[str]
    excluded_by_primary_key: int
    excluded_by_name: int
    excluded_by_size: int
    query_errors: int


def unique_values(source: DataSource) -> UniqueValuesResult:
    """원본 `_get_unique_values`(10-73행)의 규칙으로 값을 모은다. 순서는 SQLite가 돌려준 그대로."""
    tables = source.tables()
    columns = {t: source.columns(t) for t in tables}
    pk_names: list[str] = []
    for t in tables:
        for c in columns[t]:
            if c.pk > 0 and not any(p.lower() == c.name.lower() for p in pk_names):
                pk_names.append(c.name)
    pk_lower = {p.lower() for p in pk_names}
    out: UniqueValues = []
    by_pk = by_name = by_size = errors = 0
    for t in tables:
        if t == SKIP_TABLE:
            continue
        text_cols = [c.name for c in columns[t] if "TEXT" in c.decl_type]
        kept = [c for c in text_cols if c.lower() not in pk_lower]
        by_pk += len(text_cols) - len(kept)
        table_values: list[tuple[str, list[str]]] = []
        for column in kept:
            if excluded_by_name(column):
                by_name += 1
                continue
            stats = source.text_value_stats(t, column)
            if stats.failed:
                errors += 1
                continue
            if stats.sum_length is None or stats.count == 0:
                continue
            if not keep_by_size(column, stats.sum_length, stats.count):
                by_size += 1
                continue
            table_values.append((column, [str(v) for v in source.distinct_values(t, column)]))
        out.append((t, table_values))
    return UniqueValuesResult(
        values=out,
        primary_key_names=pk_names,
        excluded_by_primary_key=by_pk,
        excluded_by_name=by_name,
        excluded_by_size=by_size,
        query_errors=errors,
    )


# ── 3-gram 역색인 ────────────────────────────────────────


@dataclass(frozen=True)
class ValueEntry:
    table: str
    column: str
    value: str


@dataclass
class ShingleIndex:
    entries: list[ValueEntry]
    sizes: list[int]
    postings: dict[str, list[int]]  # 조각 → 값 번호들(오름차순)
    empty: list[int] = field(default_factory=list)  # 조각 없는(3글자 미만) 값 번호


def shingles(text: str) -> dict[str, None]:
    """원본 `_create_minhash`의 조각 `text[i:i+3]`(코드 포인트, 대소문자 그대로). 넣은 순서를 지키는 집합."""
    return dict.fromkeys(text[i : i + N_GRAM] for i in range(len(text) - N_GRAM + 1))


def flatten(values: UniqueValues) -> list[ValueEntry]:
    return [ValueEntry(t, c, v) for t, cols in values for c, vals in cols for v in vals]


def build_shingle_index(values: UniqueValues) -> ShingleIndex:
    entries = flatten(values)
    sizes: list[int] = []
    postings: dict[str, list[int]] = {}
    empty: list[int] = []
    for i, e in enumerate(entries):
        s = shingles(e.value)
        sizes.append(len(s))
        if not s:
            empty.append(i)
        for g in s:
            postings.setdefault(g, []).append(i)
    return ShingleIndex(entries=entries, sizes=sizes, postings=postings, empty=empty)


def jaccard_top_n(index: ShingleIndex, query: str, top_n: int = TOP_N) -> list[tuple[int, float]]:
    """정확한 자카드 상위 n. 질의 조각이 없으면 조각 없는 값들과 1.0. 같으면 값 번호 순."""
    q = shingles(query)
    if not q:
        return [(i, 1.0) for i in index.empty[:top_n]]
    inter: dict[int, int] = {}
    for g in q:
        for i in index.postings.get(g, ()):
            inter[i] = inter.get(i, 0) + 1
    hits = [(i, n / (len(q) + index.sizes[i] - n)) for i, n in inter.items()]
    hits.sort(key=lambda h: (-h[1], h[0]))
    return hits[:top_n]


def utf16_key(s: str) -> bytes:
    """JavaScript 기본 정렬(UTF-16 코드 단위 순)과 같은 정렬 키."""
    return s.encode("utf-16-be", errors="surrogatepass")


def _u32_bytes(data: list[int]) -> bytes:
    a = array("I", data)
    if a.itemsize != 4:
        a = array("L", data)
    if a.itemsize != 4:
        raise RuntimeError("부호 없는 32비트 배열을 만들 수 없습니다")
    if sys.byteorder == "big":
        a.byteswap()
    return a.tobytes()


def _u32_read(raw: bytes) -> list[int]:
    a = array("I")
    if a.itemsize != 4:
        a = array("L")
    a.frombytes(raw)
    if sys.byteorder == "big":
        a.byteswap()
    return a.tolist()


def dumps_compact(obj: object) -> str:
    """TS JSON.stringify와 같은 꼴(빈칸 없음, 비ASCII 그대로) + 줄바꿈."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"


def serialize_index(values: UniqueValues, index: ShingleIndex) -> dict[str, bytes]:
    keys = sorted(index.postings, key=utf16_key)
    postings: list[int] = []
    offsets = [0]
    for k in keys:
        postings.extend(index.postings[k])
        offsets.append(len(postings))
    return {
        "values.json": dumps_compact({"values": values}).encode("utf-8"),
        "shingles.json": dumps_compact({"shingles": keys}).encode("utf-8"),
        "postings.u32": _u32_bytes(postings),
        "offsets.u32": _u32_bytes(offsets),
        "sizes.u32": _u32_bytes(index.sizes),
    }


def write_index(out_dir: Path, values: UniqueValues, index: ShingleIndex) -> dict[str, dict]:
    """다섯 파일을 쓴다. 돌려주는 것: 파일 → {sha256, size}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict] = {}
    for name, data in serialize_index(values, index).items():
        (out_dir / name).write_bytes(data)
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    return files


def read_index(out_dir: Path) -> tuple[UniqueValues, ShingleIndex]:
    raw = json.loads((out_dir / "values.json").read_text(encoding="utf-8"))["values"]
    values: UniqueValues = [(t, [(c, list(vs)) for c, vs in cols]) for t, cols in raw]
    keys = json.loads((out_dir / "shingles.json").read_text(encoding="utf-8"))["shingles"]
    postings_flat = _u32_read((out_dir / "postings.u32").read_bytes())
    offsets = _u32_read((out_dir / "offsets.u32").read_bytes())
    sizes = _u32_read((out_dir / "sizes.u32").read_bytes())
    entries = flatten(values)
    if len(offsets) != len(keys) + 1 or len(sizes) != len(entries):
        raise ValueError(f"값 색인 파일의 길이가 맞지 않습니다: {out_dir}")
    postings = {k: postings_flat[offsets[i] : offsets[i + 1]] for i, k in enumerate(keys)}
    empty = [i for i, n in enumerate(sizes) if n == 0]
    return values, ShingleIndex(entries=entries, sizes=sizes, postings=postings, empty=empty)
