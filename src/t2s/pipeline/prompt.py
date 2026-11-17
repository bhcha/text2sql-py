"""⑥ 질문·지식 주석과 ⑦ 결합(CoT 문장, `SELECT ` 꼬리).

출처(복사 후 수정): DAMO-ConvAI `bird/llm/src/gpt_request.py` `generate_comment_prompt` 99-111행, `cot_wizard` 113-116행,
`generate_combined_prompts_one` 138-147행(https://github.com/AlibabaResearch/DAMO-ConvAI, 커밋
faf754671f5b850e1b4d1ff870db37a6a2480df6, MIT License, Copyright (c) 2022 Alibaba Research, LICENSES/MIT-DAMO-ConvAI.txt).
바꾼 것:
- 방언 이름을 인자로 받는다(`source.dialect.name`). SQLite면 원본과 같은 글이다.
- 스키마 글을 DB 경로 대신 밖에서 받는다(①·④·⑤를 거친 글).
- 원본 106행 `if not knowledge_prompt:`는 늘 거짓이라 no-kg 문장 갈래는 옮기지 않았다. 지식이 None이면 원본처럼
  `-- External Knowledge: None`이 들어간다(evidence not_used). evidence used면 문항의 힌트 글이 글자 그대로 들어가고,
  빈 글이면 `-- External Knowledge: `가 된다(원본 104행 format과 같은 결과). 원본은 `--use_knowledge True`일 때
  `knowledge_package` 208-213행·`decouple_question_schema` 218-225행이 문항의 evidence를 지식으로 넘긴다(179-180행,
  261-264행). 이 하네스는 evidence used run에서만 그렇게 한다.
문장의 철자(`understading`)는 원본 그대로다. 고치지 않는다.

실험용(2026-11-17, 자체 문장): `output_spec=True`면 끝의 CoT 문장과 `SELECT ` 꼬리 대신 OUTPUT_SPEC을 붙인다.
모델이 결과 열과 한 행의 뜻을 먼저 적고 `SQL: ` 뒤에 SQL을 쓴다(⑨ 꺼내기의 CoT 가르기가 `: SELECT`로 자른다).
빈 칸 양식뿐이고 문항·DB별 내용은 없다. 켜는 곳은 `pipeline/run.py`의 환경변수 `T2S_GEN_OUTPUT_SPEC=1`이다.
"""

from __future__ import annotations

COT = "\nGenerate the SQL after thinking step by step: "
TAIL = "\nSELECT "
OUTPUT_SPEC = (
    "\nBefore writing the SQL, state the expected output of the query, then write the SQL after thinking step by step."
    "\nOutput columns: <the result columns, in order, and what each one is>"
    "\nOne row = <what one row of the result represents>"
    "\nSQL: "
)


def comment_prompt(question: str, knowledge: str | None, dialect_name: str = "SQLite") -> str:
    pattern_prompt_kg = (
        f"-- Using valid {dialect_name} and understading External Knowledge, "
        "answer the following questions for the tables provided above."
    )
    question_prompt = f"-- {question}"
    knowledge_prompt = f"-- External Knowledge: {knowledge}"
    return knowledge_prompt + "\n" + pattern_prompt_kg + "\n" + question_prompt


def cot_wizard() -> str:
    return COT


def combined_prompt(
    schema_text: str, question: str, knowledge: str | None, dialect_name: str = "SQLite", *, output_spec: bool = False
) -> str:
    tail = OUTPUT_SPEC if output_spec else cot_wizard() + TAIL
    return schema_text + "\n\n" + comment_prompt(question, knowledge, dialect_name) + tail
