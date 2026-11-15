"""공개 문항 파일 읽기. 항목은 정확히 네 칸(question_id, db_id, question, difficulty)이어야 한다.

칸이 더 있으면(정답이 든 파일을 실수로 가리키면) 읽지 않고 멈춘다. 오류 글에는 위치 번호와 칸 개수만 넣는다.
위치(0부터)가 문항의 열쇠다(BIRD Mini-Dev에는 question_id가 겹치는 항목이 둘 있다).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

PUBLIC_KEYS = ("question_id", "db_id", "question", "difficulty")
STUDY_KEYS = ("id", "question", "question_type", "task_conditions")
STUDY_FORMAT = "study-model-inputs"


class QuestionsError(Exception):
    pass


@dataclass(frozen=True)
class PublicQuestion:
    position: int
    question_id: int
    db_id: str
    question: str
    difficulty: str


def load_public_questions(path: Path) -> list[PublicQuestion]:
    try:
        items = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise QuestionsError(f"문항 파일을 JSON으로 읽지 못했습니다: {path} ({e.lineno}행)") from None
    if not isinstance(items, list):
        raise QuestionsError(f"문항 파일이 목록이 아닙니다: {path}")
    out: list[PublicQuestion] = []
    for position, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != set(PUBLIC_KEYS) or len(item) != len(PUBLIC_KEYS):
            n = len(item) if isinstance(item, dict) else 0
            raise QuestionsError(
                f"문항 {position}번 항목의 칸이 공개 문항 꼴이 아닙니다(칸 {n}개, 기대 {len(PUBLIC_KEYS)}개)"
            )
        out.append(
            PublicQuestion(
                position=position,
                question_id=int(item["question_id"]),
                db_id=str(item["db_id"]),
                question=str(item["question"]),
                difficulty=str(item["difficulty"]),
            )
        )
    return out


def load_text_questions(path: Path, source_name: str) -> list[PublicQuestion]:
    """질문이 한 줄에 하나씩 든 텍스트. 빈 줄과 `#`로 시작하는 줄은 건너뛴다.

    question_id는 줄 차례(0부터 센 문항 차례), db_id는 소스 이름, difficulty는 빈 글이다.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise QuestionsError(f"문항 파일을 UTF-8로 읽지 못했습니다: {path}") from None
    out: list[PublicQuestion] = []
    for line in text.splitlines():
        q = line.strip()
        if not q or q.startswith("#"):
            continue
        n = len(out)
        out.append(PublicQuestion(position=n, question_id=n, db_id=source_name, question=q, difficulty=""))
    if not out:
        raise QuestionsError(f"문항 파일에 질문이 없습니다: {path}")
    return out


def to_public_json(questions: list[PublicQuestion]) -> list[dict]:
    return [
        {"question_id": q.question_id, "db_id": q.db_id, "question": q.question, "difficulty": q.difficulty}
        for q in questions
    ]


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise QuestionsError(f"문항 파일을 JSON으로 읽지 못했습니다: {path} ({e.lineno}행)") from None


def load_study_inputs(path: Path, source_name: str) -> tuple[list[PublicQuestion], dict]:
    """스터디 모델 입력 파일({"items": [...]})을 읽는다. 돌려주는 것: (문항, run 기록에 넣을 칸).

    항목은 정확히 네 칸(id, question, question_type, task_conditions)이어야 하고 question_type은 "sql"이어야 한다.
    문항 조건(task_conditions)은 모델에 주지 않으므로 여기서 버린다(AGENTS 규칙 9). 오류 글에는 위치와 칸 수만 넣는다.
    question_id는 차례(0부터), db_id는 소스 이름, difficulty는 빈 글이다.
    """
    data = _read_json(path)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise QuestionsError(f"스터디 모델 입력 파일에 items 목록이 없습니다: {path}")
    out: list[PublicQuestion] = []
    ids: list[str] = []
    for position, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != set(STUDY_KEYS) or len(item) != len(STUDY_KEYS):
            n = len(item) if isinstance(item, dict) else 0
            raise QuestionsError(
                f"문항 {position}번 항목의 칸이 스터디 모델 입력 꼴이 아닙니다(칸 {n}개, 기대 {len(STUDY_KEYS)}개)"
            )
        if item["question_type"] != "sql":
            raise QuestionsError(f"문항 {position}번 항목이 SQL 문항이 아닙니다(question_type)")
        qid = str(item["id"])
        if qid in ids:
            raise QuestionsError(f"문항 {position}번 항목의 id가 앞 항목과 겹칩니다")
        ids.append(qid)
        out.append(
            PublicQuestion(
                position=position,
                question_id=position,
                db_id=source_name,
                question=str(item["question"]),
                difficulty="",
            )
        )
    if not out:
        raise QuestionsError(f"문항 파일에 질문이 없습니다: {path}")
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return out, {"input_format": STUDY_FORMAT, "ids": ids, "source_sha256": digest, "conditions": "not_used"}


def load_questions_json(path: Path, source_name: str | None) -> tuple[list[PublicQuestion], dict | None]:
    """JSON 문항 파일. 목록이면 공개 문항 꼴, 사전이면 스터디 모델 입력 꼴(소스가 하나인 데이터셋만).

    돌려주는 것: (문항, run 기록에 더할 칸 또는 None).
    """
    data = _read_json(path)
    if isinstance(data, list):
        return load_public_questions(path), None
    if isinstance(data, dict):
        if source_name is None:
            raise QuestionsError("스터디 모델 입력 파일은 소스가 하나인 데이터셋에서만 쓴다")
        return load_study_inputs(path, source_name)
    raise QuestionsError(f"문항 파일이 목록도 사전도 아닙니다: {path}")
