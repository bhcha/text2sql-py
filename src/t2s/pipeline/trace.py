"""단계별 기록 자료형. 단계 id와 칸 이름은 TS pack-result.json `record.steps[]`와 같게 둔다(TS 기록과 이름으로 견줄 수 있게).

글자 수는 모두 `len(str)`(코드 포인트)이다.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

# TS engines/pure-fix-h/stages.json과 같은 11개, 같은 순서
STAGE_IDS = (
    "schema",
    "extract_keywords",
    "retrieve_entity",
    "attach_values",
    "attach_profile",
    "comment",
    "prompt",
    "model",
    "extract",
    "execute",
    "refine",
)
TRACE_FORMAT = "t2s-trace/1"


@dataclass
class CallRecord:
    k: int  # 1부터, 요청 순서. calls/<k>/ 폴더 번호
    name: str  # CALL_NAMES 가운데 하나
    attempt: int  # 키워드는 시도 번호, 생성은 1, 고치기는 차례 번호
    prompt_sha256: str
    prompt_chars: int
    response_sha256: str | None
    response_chars: int | None
    parse_ok: bool | None  # 키워드: 읽었는가, 고치기: 블록이 있는가, 생성: None
    elapsed_ms: int
    tool_calls: int
    usage: dict | None
    failure: str | None  # LlmFailure.type


@dataclass
class RoundRecord:
    """고치기 한 차례(⑩ 한 번 + 있으면 ⑪ 한 번)."""

    round: int  # 1부터
    old_sql: str  # 이 차례에 실행한(또는 건너뛴) SQL
    executed: bool = False
    status: str | None = None  # rows | empty | error | timeout | None(건너뜀)
    row_count: int | None = None
    column_count: int | None = None
    has_null: bool | None = None  # 앞 5행에 NULL이 있는가
    head_text: str | None = None  # 성공이면 str(앞 5행), 실패면 오류 글. 기록용이고 모델에 보내지 않는다
    sqlite_error: str | None = None  # 상태가 error·timeout일 때의 글
    exception_class: str | None = None
    denied: bool | None = None  # authorizer가 거절했는가
    elapsed_ms: int | None = None
    reason: str = ""  # judge.py의 갈래
    refine: bool = False
    sql_arg_prefixed: bool | None = None
    prompt_chars: int | None = None
    prompt_sha256: str | None = None
    call_k: int | None = None
    parse: str | None = None  # sql_block | no_sql_block
    blocks: int | None = None
    new_sql: str | None = None


def _plain(value):
    """json.dumps로 바로 쓸 수 있는 꼴(튜플은 목록으로)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        raise TypeError("Trace에는 bytes를 넣지 않는다")
    return value


@dataclass
class Trace:
    question: str
    sql: str | None = None  # 최종 SQL. 실패면 None
    exception: dict | None = None  # {"type", "message"}
    stages: dict[str, dict | None] = field(default_factory=lambda: dict.fromkeys(STAGE_IDS))
    calls: list[CallRecord] = field(default_factory=list)
    rounds: list[RoundRecord] = field(default_factory=list)
    timing: dict[str, int] = field(default_factory=dict)
    versions: dict[str, str] = field(default_factory=dict)

    def set_stage(self, stage_id: str, value: dict | None) -> None:
        if stage_id not in STAGE_IDS:
            raise KeyError(f"모르는 단계 id: {stage_id}")
        self.stages[stage_id] = value

    def to_json(self) -> dict:
        return {
            "format": TRACE_FORMAT,
            "question": self.question,
            "sql": self.sql,
            "exception": _plain(self.exception),
            "stages": [{"id": s, "value": _plain(self.stages.get(s))} for s in STAGE_IDS],
            "calls": _plain(self.calls),
            "rounds": _plain(self.rounds),
            "timing": dict(self.timing),
            "versions": dict(self.versions),
        }
