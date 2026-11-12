"""공식 문제 파일(정답·evidence 포함)에서 생성용 공개 문항 파일을 만드는 규칙(TS harness/lib/public-questions.ts와 같은 일).

오류 글에는 위치 번호와 키 이름만 넣고 값은 넣지 않는다.
"""

from __future__ import annotations

import json

PUBLIC_KEYS = ("question_id", "db_id", "question", "difficulty")
OFFICIAL_KEYS = ("question_id", "db_id", "question", "evidence", "SQL", "difficulty")


class PublicQuestionsError(Exception):
    pass


def build_public_items(official: list[dict]) -> list[dict]:
    if not isinstance(official, list):
        raise PublicQuestionsError("공식 문제 파일이 목록이 아님")
    out: list[dict] = []
    for position, item in enumerate(official):
        if not isinstance(item, dict) or tuple(item) != OFFICIAL_KEYS:
            raise PublicQuestionsError(f"공식 문제 {position}번 항목의 키가 예상과 다름")
        out.append({k: item[k] for k in PUBLIC_KEYS})
    return out


def serialize_public(items: list[dict]) -> str:
    return json.dumps(items, indent=2, ensure_ascii=False) + "\n"


def check_public_against_official(items: list[dict], official: list[dict]) -> list[str]:
    """다른 곳의 위치 번호와 키 이름만 돌려준다."""
    errors: list[str] = []
    if len(items) != len(official):
        errors.append(f"항목 수 {len(items)} (기대 {len(official)})")
    for i, (p, o) in enumerate(zip(items, official, strict=False)):
        if tuple(p) != PUBLIC_KEYS:
            errors.append(f"공개 문항 {i}번 항목의 키가 다름")
            continue
        for k in PUBLIC_KEYS:
            if p[k] != o.get(k):
                errors.append(f"공개 문항 {i}번 항목의 {k}가 공식 파일과 다름")
    return errors
