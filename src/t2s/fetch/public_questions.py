"""공식 문제 파일(정답·evidence 포함)에서 생성용 공개 문항 파일을 만드는 규칙(TS harness/lib/public-questions.ts와 같은 일)과
공개 evidence 파일을 만드는 규칙.

공개 evidence 파일은 위치 순서 목록이고 항목은 정확히 {"question_id", "hint"}다(hint는 공식 evidence 글 그대로).
생성 쪽은 evidence used run을 시작할 때만 이 파일을 읽는다(AGENTS 규칙 3).
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


HINT_KEYS = ("question_id", "hint")


def build_public_hints(official: list[dict]) -> list[dict]:
    """위치 순서 그대로 {"question_id", "hint"}만 남긴다. hint는 공식 evidence 글 그대로(고치지 않는다)."""
    if not isinstance(official, list):
        raise PublicQuestionsError("공식 문제 파일이 목록이 아님")
    out: list[dict] = []
    for position, item in enumerate(official):
        if not isinstance(item, dict) or tuple(item) != OFFICIAL_KEYS:
            raise PublicQuestionsError(f"공식 문제 {position}번 항목의 키가 예상과 다름")
        if not isinstance(item["evidence"], str):
            raise PublicQuestionsError(f"공식 문제 {position}번 항목의 evidence가 글이 아님")
        out.append({"question_id": item["question_id"], "hint": item["evidence"]})
    return out


def serialize_hints(items: list[dict]) -> str:
    return json.dumps(items, indent=2, ensure_ascii=False) + "\n"


def check_hints_against_official(items: list[dict], official: list[dict]) -> list[str]:
    """다른 곳의 위치 번호와 키 이름만 돌려준다."""
    errors: list[str] = []
    if len(items) != len(official):
        errors.append(f"항목 수 {len(items)} (기대 {len(official)})")
    for i, (p, o) in enumerate(zip(items, official, strict=False)):
        if tuple(p) != HINT_KEYS:
            errors.append(f"공개 evidence {i}번 항목의 키가 다름")
            continue
        if p["question_id"] != o.get("question_id"):
            errors.append(f"공개 evidence {i}번 항목의 question_id가 공식 파일과 다름")
        if p["hint"] != o.get("evidence"):
            errors.append(f"공개 evidence {i}번 항목의 hint가 공식 파일의 evidence와 다름")
    return errors
