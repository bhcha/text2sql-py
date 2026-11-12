"""모델 호출 계약: 설정, 결과, 백엔드 프로토콜, 호출 파일 쓰기.

TS engines/packkit/llm/types.ts의 LlmSettings·LlmOutcome·LlmBackend·sha256을 옮긴 자체 코드다.
TS와 다른 점: `call`이 호출 이름(`name`)과 차례(`attempt`)를 받는다(가짜 백엔드가 프롬프트 글로 종류를 맞히지 않게).
호출 폴더에 `prompt.txt`를 남긴다.
이 패키지는 t2s의 다른 패키지를 import하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

CALL_NAMES = ("extract_keywords", "generate_sql", "refine_sql")
# 이어 돌 때 다시 도는 실패 종류(TS benchmarks/benchkit/run.ts RETRYABLE_EXCEPTIONS)
RETRYABLE_FAILURES = ("CodexExit", "CodexUsageLimit", "ClaudeExit", "ClaudeUsageLimit")


@dataclass(frozen=True)
class LlmSettings:
    backend: str  # "fake" | "scripted" | "codex-cli" | "claude-code"
    model: str
    reasoning_effort: str
    timeout_seconds: float


@dataclass(frozen=True)
class LlmFailure:
    type: str  # 예: "FakeExit", "FakeUsageLimit", "ScriptExhausted", "ScriptMismatch"
    message: str


@dataclass(frozen=True)
class LlmOutcome:
    response: str | None  # 실패면 None
    failure: LlmFailure | None
    meta: dict


class LlmBackend(Protocol):
    id: str
    settings: LlmSettings

    def call(self, prompt: str, call_dir: Path | None, *, name: str, attempt: int) -> LlmOutcome: ...


def sha256_text(text: str) -> str:
    """UTF-8 바이트의 sha256 16진(TS types.ts sha256과 같다)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def base_meta(
    settings: LlmSettings,
    prompt: str,
    response: str | None,
    *,
    name: str,
    attempt: int,
    elapsed_ms: int = 0,
    tool_calls: int = 0,
    usage: dict | None = None,
) -> dict:
    """llm-meta.json에 쓰는 기본 칸."""
    return {
        "backend": settings.backend,
        "model": settings.model,
        "reasoning_effort": settings.reasoning_effort,
        "timeout_seconds": settings.timeout_seconds,
        "elapsed_ms": elapsed_ms,
        "tool_calls": tool_calls,
        "usage": usage,
        "prompt_chars": len(prompt),
        "prompt_sha256": sha256_text(prompt),
        "response_sha256": None if response is None else sha256_text(response),
        "name": name,
        "attempt": attempt,
    }


def write_call_files(call_dir: Path, prompt: str, response: str | None, meta: dict) -> None:
    """호출 폴더에 prompt.txt, response.txt(응답이 있을 때만), llm-meta.json을 UTF-8로 쓴다."""
    call_dir = Path(call_dir)
    call_dir.mkdir(parents=True, exist_ok=True)
    (call_dir / "prompt.txt").write_text(prompt, encoding="utf-8", newline="")
    if response is not None:
        (call_dir / "response.txt").write_text(response, encoding="utf-8", newline="")
    (call_dir / "llm-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_usage_limit(failure_type: str | None) -> bool:
    """실패 종류 이름이 "UsageLimit"으로 끝나는가(FakeUsageLimit 포함)."""
    return bool(failure_type) and str(failure_type).endswith("UsageLimit")


def usage_totals(usage: dict | None) -> tuple[int, int] | None:
    """(입력, 출력) 토큰 합. usage가 없으면 None.

    입력 = input_tokens + cache_read_input_tokens + cache_creation_input_tokens(없는 칸은 0), 출력 = output_tokens.
    Codex의 cached_input_tokens는 input_tokens에 이미 든 것으로 보고 더하지 않는다(추정, TS ask.ts totalTokens와 같은 식).
    """
    if not isinstance(usage, dict):
        return None

    def n(key: str) -> int:
        v = usage.get(key)
        return int(v) if isinstance(v, (int, float)) else 0

    return (
        n("input_tokens") + n("cache_read_input_tokens") + n("cache_creation_input_tokens"),
        n("output_tokens"),
    )
