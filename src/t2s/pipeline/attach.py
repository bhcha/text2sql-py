"""④ 값 줄과 ⑤ 프로파일 줄: 테이블 블록 끝에 주석 한 줄씩 붙인다.

자체 코드다(오픈소스 출처 없음). TS engines/pure-fix-h/pack/attach.ts(guardComment, valueList, valuesLine,
orderColumns, attachValues)와 profile-line.ts(typesText, columnItem, profileLine, attachProfile)를 Python으로 옮겼다.
TS의 pyStrRepr는 Python `repr()`이다. 글은 TS와 바이트까지 같다.

꼴:
- 값 줄: `{블록} \\n /* values similar to the question: {열} = ['값', …]; … */` (값이 하나라도 있는 테이블만)
- 프로파일 줄: `{블록} \\n /* column profile: {열}: N distinct, M null[, types …][, values […] | , min a, max b | , length a-b]; … */`
- 줄 안의 `*/`는 `*\\x2f`로 바꿔 주석이 일찍 닫히지 않게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from t2s.prep.value_index import UniqueValues

VALUES_LEAD = "/* values similar to the question: "
PROFILE_LEAD = "/* column profile: "
TAIL = " */"
JOINER = " \n "
ITEM_SEPARATOR = "; "
BLOCK_SEPARATOR = "\n\n"

PerColumn = list[tuple[str, list[tuple[str, list[str]]]]]


class ProfileMissing(Exception):
    pass


def guard_comment(text: str) -> str:
    return text.replace("*/", "*\\x2f")


def value_list(values: list[str]) -> str:
    return "[" + ", ".join(guard_comment(repr(v)) for v in values) + "]"


def values_line(columns: list[tuple[str, list[str]]]) -> str | None:
    items = [f"{guard_comment(c)} = {value_list(vs)}" for c, vs in columns if vs]
    return VALUES_LEAD + ITEM_SEPARATOR.join(items) + TAIL if items else None


def order_columns(table: str, columns, index_values: UniqueValues) -> list[tuple[str, list[str]]]:
    """열을 그 테이블의 값 색인 순서로 놓고(색인에 없는 열은 뒤에 검색 순서대로), 한 열 안의 같은 값은 처음 것만 남긴다."""
    order = next(([c for c, _ in cols] for t, cols in index_values if t == table), [])

    def rank(c: str) -> int:
        return order.index(c) if c in order else len(order)

    deduped = [(c, list(dict.fromkeys(vs)), i) for i, (c, vs) in enumerate(columns)]
    deduped.sort(key=lambda x: (rank(x[0]), x[2]))
    return [(c, vs) for c, vs, _ in deduped]


@dataclass
class Attached:
    blocks: list[str]
    text: str
    attached: PerColumn
    unmatched_tables: list[str]
    chars_added: int


def attach_values(tables: list[str], blocks: list[str], found: PerColumn, index_values: UniqueValues) -> Attached:
    if len(tables) != len(blocks):
        raise ValueError("tables와 blocks의 길이가 다릅니다")
    by_table = dict(found)
    attached: PerColumn = []
    out: list[str] = []
    for table, block in zip(tables, blocks, strict=True):
        cols = by_table.get(table)
        if cols is None:
            out.append(block)
            continue
        ordered = order_columns(table, cols, index_values)
        line = values_line(ordered)
        if line is None:
            out.append(block)
            continue
        attached.append((table, [(c, vs) for c, vs in ordered if vs]))
        out.append(block + JOINER + line)
    text = BLOCK_SEPARATOR.join(out)
    return Attached(
        blocks=out,
        text=text,
        attached=attached,
        unmatched_tables=[t for t, _ in found if t not in tables],
        chars_added=len(text) - len(BLOCK_SEPARATOR.join(blocks)),
    )


def types_text(types: dict[str, int]) -> str | None:
    entries = [(k, n) for k, n in types.items() if n > 0]
    if len(entries) < 2:
        return None
    entries.sort(key=lambda e: (-e[1], e[0]))
    return ", ".join(f"{k} {n}" for k, n in entries)


def column_item(column: dict) -> str:
    out = f"{guard_comment(column['name'])}: {column['distinct']} distinct, {column['nulls']} null"
    types = types_text(column.get("types", {}))
    if types is not None:
        out += f", types {types}"
    kind = column.get("kind")
    if kind == "values":
        out += ", values [" + ", ".join(guard_comment(v["repr"]) for v in column["values"]) + "]"
    elif kind == "range":
        out += f", min {guard_comment(column['min'])}, max {guard_comment(column['max'])}"
    elif kind == "length":
        out += f", length {column['min_length']}-{column['max_length']}"
    return out


def profile_line(columns: list[dict]) -> str | None:
    return PROFILE_LEAD + ITEM_SEPARATOR.join(column_item(c) for c in columns) + TAIL if columns else None


@dataclass
class Profiled:
    blocks: list[str]
    text: str
    line_chars: dict[str, int]
    chars_added: int


def attach_profile(tables: list[str], blocks: list[str], profile: dict) -> Profiled:
    if len(tables) != len(blocks):
        raise ValueError("tables와 blocks의 길이가 다릅니다")
    by_table = {t["name"]: t for t in profile.get("tables", [])}
    line_chars: dict[str, int] = {}
    out: list[str] = []
    for table, block in zip(tables, blocks, strict=True):
        entry = by_table.get(table)
        if entry is None:
            raise ProfileMissing(f"프로파일에 테이블이 없습니다: {table}")
        line = profile_line(entry.get("columns", []))
        line_chars[table] = 0 if line is None else len(JOINER + line)
        out.append(block if line is None else block + JOINER + line)
    text = BLOCK_SEPARATOR.join(out)
    return Profiled(
        blocks=out, text=text, line_chars=line_chars, chars_added=len(text) - len(BLOCK_SEPARATOR.join(blocks))
    )
