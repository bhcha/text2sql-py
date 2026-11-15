"""문항 힌트 읽기: 공개 힌트 파일(BIRD)과 스터디 문항 조건 잇기(Olist). evidence used run을 시작할 때만 쓴다.

이 패키지 안에서는 evidence라는 말 대신 hint라는 말만 쓴다(tests/test_import_rules.py 정답 글자 검사).
- 공개 힌트 파일은 받기 단계(/getbird)가 공식 문제 파일에서 위치별 question_id와 evidence 글만 떼어 만든 것이다.
  항목은 정확히 두 칸(question_id, hint)이고, 위치마다 question_id가 공개 문항과 같아야 한다.
- 스터디 문항 조건은 각 줄의 앞뒤 공백만 떼고 "; "로 이은 한 줄이다. 글자는 고치지 않는다.
- 빈 힌트는 빈 글 그대로 둔다.
오류 글에는 위치 번호와 칸 수만 넣고 값은 넣지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path

from t2s.dataset.questions import PublicQuestion, QuestionsError

HINT_RULES_VERSION = "1.0.0"
HINT_KEYS = ("question_id", "hint")
JOIN = "; "


def load_public_hints(path: Path, questions: list[PublicQuestion]) -> list[str]:
    """위치 순서의 힌트 글 목록. 칸이 둘이 아니거나, 수가 다르거나, 위치별 question_id가 다르면 멈춘다."""
    try:
        items = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise QuestionsError(f"힌트 파일을 JSON으로 읽지 못했습니다: {path} ({e.lineno}행)") from None
    if not isinstance(items, list):
        raise QuestionsError(f"힌트 파일이 목록이 아닙니다: {path}")
    if len(items) != len(questions):
        raise QuestionsError(f"힌트 파일의 항목 수 {len(items)}가 문항 수 {len(questions)}와 다릅니다")
    out: list[str] = []
    for position, (item, q) in enumerate(zip(items, questions, strict=True)):
        if not isinstance(item, dict) or set(item) != set(HINT_KEYS) or len(item) != len(HINT_KEYS):
            n = len(item) if isinstance(item, dict) else 0
            raise QuestionsError(f"힌트 {position}번 항목의 칸이 힌트 꼴이 아닙니다(칸 {n}개, 기대 {len(HINT_KEYS)}개)")
        if not isinstance(item["hint"], str):
            raise QuestionsError(f"힌트 {position}번 항목의 hint가 글이 아닙니다")
        if item["question_id"] != q.question_id:
            raise QuestionsError(f"힌트 {position}번 항목의 question_id가 공개 문항과 다릅니다")
        out.append(item["hint"])
    return out


def join_conditions(conditions: list) -> str:
    """스터디 문항 조건(글 목록)을 앞뒤 공백만 떼고 "; "로 이은 한 줄. 빈 목록이면 빈 글."""
    if not isinstance(conditions, list) or not all(isinstance(c, str) for c in conditions):
        raise QuestionsError("문항 조건이 글 목록이 아닙니다")
    return JOIN.join(c.strip() for c in conditions)
