"""① 스키마 글: CREATE 원문 + 테이블마다 예시 행 표.

출처(복사 후 수정): DAMO-ConvAI `bird/llm/src/gpt_request.py` `nice_look_table` 44-58행, `generate_schema_prompt` 60-97행
(https://github.com/AlibabaResearch/DAMO-ConvAI, 커밋 faf754671f5b850e1b4d1ff870db37a6a2480df6,
MIT License, Copyright (c) 2022 Alibaba Research, LICENSES/MIT-DAMO-ConvAI.txt).
바꾼 것:
- `sqlite3.connect` 직접 호출을 DataSource(tables, ddl, sample_rows)로 바꿨다. 연결은 읽기 전용이다.
- 예시 행 질의는 sample_rows가 이름을 늘 감싸 실행한다(원본은 order·by·group 밖의 이름을 감싸지 않아, 따옴표가 필요한 이름이면
  SQL 오류가 난다). 프롬프트에 적는 `SELECT * FROM {이름} LIMIT {n};` 글은 원본 그대로 order·by·group만 백틱으로 감싼다.
- 테이블 블록 목록과 테이블별 예시 행 수를 함께 돌려준다(값 줄·프로파일 줄을 블록 끝에 붙이려고).
- 원본 75행 `if table == 'sqlite_sequence'`는 튜플과 글을 비교해 늘 거짓이므로 옮기지 않았다(sqlite_sequence도 들어간다).
값 표기는 원본처럼 Python `str()` 그대로다.
"""

from __future__ import annotations

from dataclasses import dataclass

from t2s.datasource.base import DataSource

BACKTICK_TABLES = ("order", "by", "group")


@dataclass(frozen=True)
class SchemaText:
    tables: list[str]  # source.tables() 순서
    rows: dict[str, int]  # 테이블 → 붙인 예시 행 수
    blocks: list[str]  # 테이블 블록. text = "\n\n".join(blocks)
    text: str


def nice_look_table(column_names: list[str], values: list[tuple]) -> str:
    rows = []
    # 열마다 너비 = 값들과 열 이름의 str() 글자 수 가운데 최대
    widths = [max(len(str(value[i])) for value in [*values, column_names]) for i in range(len(column_names))]
    header = "".join(f"{column.rjust(width)} " for column, width in zip(column_names, widths, strict=True))
    for value in values:
        row = "".join(f"{str(v).rjust(width)} " for v, width in zip(value, widths, strict=True))
        rows.append(row)
    return header + "\n" + "\n".join(rows)


def build_schema(source: DataSource, num_rows: int = 3) -> SchemaText:
    tables = source.tables()
    schemas: dict[str, str] = {}
    rows: dict[str, int] = {}
    for table in tables:
        create_prompt = source.ddl(table)
        schemas[table] = create_prompt
        if num_rows:
            cur_table = table
            if cur_table in BACKTICK_TABLES:
                cur_table = f"`{cur_table}`"
            sample = source.sample_rows(table, num_rows)
            rows_prompt = nice_look_table(column_names=sample.columns, values=sample.rows)
            verbose_prompt = (
                f"/* \n {num_rows} example rows: \n SELECT * FROM {cur_table} LIMIT {num_rows}; \n {rows_prompt} \n */"
            )
            schemas[table] = f"{create_prompt} \n {verbose_prompt}"
            rows[table] = len(sample.rows)
    blocks = list(schemas.values())
    return SchemaText(tables=tables, rows=rows, blocks=blocks, text="\n\n".join(blocks))
