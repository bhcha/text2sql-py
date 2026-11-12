"""② 키워드 추출: 프롬프트 채우기와 응답 읽기.

출처(복사 후 수정): CHESS(https://github.com/ShayanTalaei/CHESS, 커밋 3d6e835f858d26885d21d4bc0215aeecf855efbe,
Apache License 2.0, LICENSES/Apache-2.0-CHESS.txt)
- `templates/template_extract_keywords.txt`: 바이트 그대로 복사해 `templates/extract_keywords.txt`로 두었다
  (sha256 4ffe8fdcb1d9c8b07ab27c15c2fd53195406b87541d3b43ee7fd0889c8eeba17, 고치지 않는다).
- `src/workflow/agents/information_retriever/tool_kit/extract_keywords.py` `ExtractKeywords._run` 21-36행: 칸 QUESTION·HINT, 응답의
  첫 결과가 키워드.
- `src/llm/parsers.py` `PythonListOutputParser.parse` 18-32행: ```python 블록 안만, 앞 공백 제거, 값 읽기.
바꾼 것:
- 원본의 `eval`은 쓰지 않는다. `ast`로 리터럴만 읽는다(모델 응답을 실행하지 않게). 목록·튜플·집합·사전은 적힌 순서대로 항목을 읽는다.
- langchain을 쓰지 않는다. 자리표는 원본 `\\{(.*?)\\}` 규칙으로 한 번에 바꾼다.
- 힌트 칸은 늘 빈 글이다(AGENTS 규칙 9). 못 읽으면 부르는 쪽(run.py)이 같은 프롬프트로 다시 부른다(TS 규칙 1.0.0).
TS engines/pure-fix-h/pack/keywords.ts·parsers.ts parseKeywords와 같은 결과를 낸다(TS의 parsePyLiteral은 ast로 대체).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

KEYWORD_RULES_VERSION = "1.0.0"  # TS parsers.ts RULES_VERSION
TEMPLATE_FILE = Path(__file__).resolve().parent / "templates" / "extract_keywords.txt"
TEMPLATE_SHA256 = "4ffe8fdcb1d9c8b07ab27c15c2fd53195406b87541d3b43ee7fd0889c8eeba17"
_PLACEHOLDER = re.compile(r"\{(.*?)\}")
_LEADING_SPACE = re.compile(r"^\s+")
_PARSE_ERRORS = (SyntaxError, ValueError, TypeError, MemoryError, RecursionError)


@cache
def template_text() -> str:
    return TEMPLATE_FILE.read_text(encoding="utf-8")


def keywords_prompt(question: str, hint: str = "") -> str:
    values = {"QUESTION": question, "HINT": hint}

    def fill(m: re.Match) -> str:
        key = m.group(1)
        if key not in values:
            raise KeyError(f"키워드 템플릿에 모르는 자리표가 있습니다: {key}")
        return values[key]

    return _PLACEHOLDER.sub(fill, template_text())


@dataclass(frozen=True)
class KeywordParse:
    ok: bool
    items: list | None  # 읽은 항목(글이 아닌 것도 그대로)
    why: str | None


def _items(node: ast.expr) -> list:
    if isinstance(node, (ast.List, ast.Tuple)):
        return [ast.literal_eval(e) for e in node.elts]
    if isinstance(node, ast.Set):
        # set으로 만들면 순서가 실행마다 달라지므로 적힌 순서로 겹친 것만 줄인다(겹친 판정은 set과 같다)
        return list(dict.fromkeys(ast.literal_eval(e) for e in node.elts))
    # 사전은 literal_eval이 적힌 순서를 지키고 겹친 키를 하나로 줄인다(eval과 같다). 집합만 노드에서 읽는다.
    value = ast.literal_eval(node)
    if isinstance(value, (list, tuple, set, frozenset)):
        return list(value)
    if isinstance(value, dict):
        return list(value)
    raise _NotAList(type(value).__name__)


class _NotAList(Exception):
    pass


def parse_keywords(output: str) -> KeywordParse:
    if not output.strip():
        return KeywordParse(False, None, "empty output")
    text = output
    if "```python" in text:
        text = text.split("```python")[1].split("```")[0]
    text = _LEADING_SPACE.sub("", text)
    try:
        tree = ast.parse(text.lstrip(" \t"), mode="eval")
        return KeywordParse(True, _items(tree.body), None)
    except _NotAList as e:
        return KeywordParse(False, None, f"not a list ({e.args[0]})")
    except _PARSE_ERRORS as e:
        return KeywordParse(False, None, f"{type(e).__name__}: {e}")
