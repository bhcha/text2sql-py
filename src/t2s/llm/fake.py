"""가짜 백엔드 둘: 이름으로 고정 응답을 고르는 FakeBackend, 대본을 순서대로 내주는 ScriptedBackend.

TS engines/packkit/llm/fake.ts의 뼈대를 옮긴 자체 코드다. 프롬프트 글을 보고 호출 종류를 맞히지 않는다.
대본 재생은 프로세스 안 ScriptedBackend로 한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from t2s.llm.base import LlmFailure, LlmOutcome, LlmSettings, base_meta, write_call_files

DEFAULT_RESPONSES: dict[str, str] = {
    "extract_keywords": '["Apple Juice", "paid"]',
    "generate_sql": "SELECT COUNT(*) FROM product",
    "refine_sql": "```sql\nSELECT COUNT(*) FROM product\n```",
}
FAIL_KINDS = {"exit": "FakeExit", "limit": "FakeUsageLimit"}


def _finish(settings: LlmSettings, prompt: str, call_dir: Path | None, response: str | None, failure, name, attempt):
    meta = base_meta(settings, prompt, response, name=name, attempt=attempt)
    if call_dir is not None:
        write_call_files(call_dir, prompt, response, meta)
    return LlmOutcome(response=response, failure=failure, meta=meta)


class FakeBackend:
    id = "fake"

    def __init__(
        self,
        responses: Mapping[str, str | Sequence[str]] | None = None,
        *,
        fail: str | None = None,
        settings: LlmSettings | None = None,
    ) -> None:
        if fail is not None and fail not in FAIL_KINDS:
            raise ValueError(f"fail은 {', '.join(FAIL_KINDS)} 가운데 하나입니다: {fail}")
        self.responses: dict[str, str | Sequence[str]] = dict(DEFAULT_RESPONSES)
        if responses:
            self.responses.update(responses)
        self.fail = fail
        self.settings = settings or LlmSettings("fake", "fake-model", "none", 1)
        self.calls: list[dict] = []
        self._used: dict[str, int] = {}

    def _pick(self, name: str) -> str:
        value = self.responses.get(name)
        if value is None:
            raise KeyError(f"가짜 응답이 없는 호출 이름: {name}")
        if isinstance(value, str):
            return value
        i = self._used.get(name, 0)
        self._used[name] = i + 1
        return value[min(i, len(value) - 1)]

    def call(self, prompt: str, call_dir: Path | None, *, name: str, attempt: int) -> LlmOutcome:
        self.calls.append({"name": name, "attempt": attempt, "prompt": prompt})
        if self.fail is not None:
            failure = LlmFailure(FAIL_KINDS[self.fail], f"가짜 백엔드 실패({self.fail})")
            return _finish(self.settings, prompt, call_dir, None, failure, name, attempt)
        return _finish(self.settings, prompt, call_dir, self._pick(name), None, name, attempt)


@dataclass(frozen=True)
class ScriptedCall:
    name: str
    response: str


class ScriptedBackend:
    id = "scripted"

    def __init__(self, script: Sequence[ScriptedCall], *, settings: LlmSettings | None = None) -> None:
        self.script = list(script)
        self.settings = settings or LlmSettings("scripted", "fake-model", "none", 1)
        self._next = 0
        self.calls: list[dict] = []

    def used(self) -> int:
        return self._next

    def remaining(self) -> int:
        return len(self.script) - self._next

    def call(self, prompt: str, call_dir: Path | None, *, name: str, attempt: int) -> LlmOutcome:
        self.calls.append({"name": name, "attempt": attempt, "prompt": prompt})
        if self._next >= len(self.script):
            failure = LlmFailure(
                "ScriptExhausted", f"대본이 떨어졌습니다({len(self.script)}개 다 씀, 부른 이름 {name})"
            )
            return _finish(self.settings, prompt, call_dir, None, failure, name, attempt)
        item = self.script[self._next]
        if item.name != name:
            failure = LlmFailure("ScriptMismatch", f"대본 {self._next + 1}번은 {item.name}인데 {name}을 불렀습니다")
            return _finish(self.settings, prompt, call_dir, None, failure, name, attempt)
        self._next += 1
        return _finish(self.settings, prompt, call_dir, item.response, None, name, attempt)
