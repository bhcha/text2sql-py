"""⑥ 질문·지식 주석과 ⑦ 결합(CoT 문장, `SELECT ` 꼬리).

출처(복사 후 수정): DAMO-ConvAI `bird/llm/src/gpt_request.py` `generate_comment_prompt` 99-111행, `cot_wizard` 113-116행,
`generate_combined_prompts_one` 138-147행(https://github.com/AlibabaResearch/DAMO-ConvAI, 커밋
faf754671f5b850e1b4d1ff870db37a6a2480df6, MIT License, Copyright (c) 2022 Alibaba Research, LICENSES/MIT-DAMO-ConvAI.txt).
바꾼 것:
- 방언 이름을 인자로 받는다(`source.dialect.name`). SQLite면 원본과 같은 글이다.
- 스키마 글을 DB 경로 대신 밖에서 받는다(①·④·⑤를 거친 글).
- 원본 106행 `if not knowledge_prompt:`는 늘 거짓이라 no-kg 문장 갈래는 옮기지 않았다. 지식이 None이면 원본처럼
  `-- External Knowledge: None`이 들어간다.
문장의 철자(`understading`)는 원본 그대로다. 고치지 않는다.
"""

from __future__ import annotations

COT = "\nGenerate the SQL after thinking step by step: "
TAIL = "\nSELECT "


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


def combined_prompt(schema_text: str, question: str, knowledge: str | None, dialect_name: str = "SQLite") -> str:
    return schema_text + "\n\n" + comment_prompt(question, knowledge, dialect_name) + cot_wizard() + TAIL
