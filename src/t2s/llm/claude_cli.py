"""Claude Code CLI 백엔드(`claude -p`). 프롬프트는 표준 입력, 응답은 stream-json의 `result` 이벤트 글이다.

TS engines/packkit/llm/claude.ts(인자 배열, 고정 시스템 프롬프트, 자식 환경에서 빼는 변수, 이벤트 읽기, 실패 분류)를
옮긴 자체 코드다. TS와 다른 점은 codex_cli.py 머리말과 같다(저장소 밖 작업 폴더, 기록 파일 이름, `cli_version` 칸).
실행 파일 이름은 `T2S_CLAUDE_BIN`으로만 바꾼다(테스트용). 한도 안내 문구는 TS에서도 확인하지 못했다(미확인).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from t2s.llm import _proc
from t2s.llm.base import LlmFailure, LlmOutcome, LlmSettings, base_meta, write_call_files
from t2s.llm.codex_cli import EVENTS_FILE, STDERR_FILE, WORK_PREFIX, failure_detail

SYSTEM_PROMPT = "Answer the user's message directly. Do not call tools."
DROPPED_ENV = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
)
DROPPED_ENV_PREFIX = "CLAUDE_CODE_MESSAGING_"
USAGE_LIMIT = re.compile(
    r"usage limit|rate limit|quota|too many requests|\b429\b|limit reached|resets? at|out of .*usage|exceeded",
    re.IGNORECASE,
)
BIN_ENV = "T2S_CLAUDE_BIN"


def claude_bin() -> str:
    return os.environ.get(BIN_ENV) or "claude"


def claude_args(settings: LlmSettings) -> list[str]:
    """TS claudeArgs와 같은 인자 배열(실행 파일 이름 제외)."""
    return [
        "-p",
        "--model",
        settings.model,
        "--effort",
        settings.reasoning_effort,
        "--tools",
        "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--setting-sources",
        "",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
        "--system-prompt",
        SYSTEM_PROMPT,
    ]


def child_env(env: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    """(넘길 환경, 뺀 이름들). 부모 Claude Code 세션의 변수만 뺀다. 인증 변수는 건드리지 않는다."""
    out: dict[str, str] = {}
    dropped: list[str] = []
    for k, v in env.items():
        if k in DROPPED_ENV or k.startswith(DROPPED_ENV_PREFIX):
            dropped.append(k)
        else:
            out[k] = v
    return out, dropped


def read_claude_events(stdout: str) -> dict:
    result: dict = {
        "response": None,
        "is_error": False,
        "model_reported": None,
        "tool_calls": 0,
        "tool_items": [],
        "usage": None,
        "session_id": None,
        "num_turns": None,
        "init": None,
        "errors": [],
    }
    for line in stdout.split("\n"):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        t = event.get("type")
        if t == "system" and event.get("subtype") == "init":
            tools = event.get("tools")
            mcp = event.get("mcp_servers")
            result["init"] = {
                "model": event.get("model"),
                "tools": len(tools) if isinstance(tools, list) else -1,
                "mcp_servers": len(mcp) if isinstance(mcp, list) else -1,
            }
        if t == "assistant":
            message = event.get("message") or {}
            if result["model_reported"] is None:
                result["model_reported"] = message.get("model")
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    result["tool_calls"] += 1
                    result["tool_items"].append(str(block.get("name")))
        if t == "error" and isinstance(event.get("message"), str):
            result["errors"].append(event["message"])
        if t == "result":
            result["usage"] = event.get("usage")
            result["session_id"] = event.get("session_id")
            result["num_turns"] = event.get("num_turns")
            result["is_error"] = event.get("is_error") is True or event.get("subtype") != "success"
            if isinstance(event.get("result"), str):
                if result["is_error"]:
                    result["errors"].append(event["result"])
                else:
                    result["response"] = event["result"]
    return result


def classify_failure(detail: str) -> str:
    return "ClaudeUsageLimit" if USAGE_LIMIT.search(detail) else "ClaudeExit"


class ClaudeBackend:
    id = "claude-code"

    def __init__(self, settings: LlmSettings) -> None:
        self.settings = settings

    def call(self, prompt: str, call_dir: Path | None, *, name: str, attempt: int) -> LlmOutcome:
        bin = claude_bin()
        work = tempfile.mkdtemp(prefix=WORK_PREFIX)
        try:
            args = claude_args(self.settings)
            env, dropped = child_env(os.environ)
            r = _proc.run_cli(
                [bin, *args], prompt, cwd=Path(work), env=env, timeout_seconds=self.settings.timeout_seconds
            )
            work_files = sum(len(files) + len(dirs) for _, dirs, files in os.walk(work))
            events = read_claude_events(r.stdout)
            response = events["response"] if r.exit_code == 0 and not events["is_error"] else None
            failure: LlmFailure | None = None
            if r.spawn_error is not None:
                failure = LlmFailure("ClaudeExit", f"claude를 실행하지 못했습니다: {r.spawn_error}")
            elif r.timed_out:
                failure = LlmFailure(
                    "ClaudeTimeout", f"claude가 {self.settings.timeout_seconds:g}초 안에 끝나지 않았습니다"
                )
            elif response is None:
                detail = failure_detail(events["errors"], r.stderr)
                failure = LlmFailure(classify_failure(detail), f"exit {r.exit_code}: {detail}")
            if failure is not None:
                response = None
            meta = base_meta(
                self.settings,
                prompt,
                response,
                name=name,
                attempt=attempt,
                elapsed_ms=r.elapsed_ms,
                tool_calls=events["tool_calls"],
                usage=events["usage"],
            )
            meta.update(
                {
                    "cli_version": _proc.cli_version(bin),
                    "command": [bin, *args],
                    "exit_code": r.exit_code,
                    "timed_out": r.timed_out,
                    "tool_items": events["tool_items"],
                    "thread_id": events["session_id"],
                    "errors": [e[:1000] for e in events["errors"][-3:]],
                    "work_dir_outside_repo": True,
                    "work_dir_files": work_files,
                    "model_reported": events["model_reported"],
                    "model_reported_matches": events["model_reported"] == self.settings.model,
                    "num_turns": events["num_turns"],
                    "init": events["init"],
                    "system_prompt": SYSTEM_PROMPT,
                    "env_dropped": sorted(dropped),
                }
            )
            if call_dir is not None:
                call_dir = Path(call_dir)
                call_dir.mkdir(parents=True, exist_ok=True)
                (call_dir / EVENTS_FILE).write_text(r.stdout, encoding="utf-8", newline="")
                (call_dir / STDERR_FILE).write_text(r.stderr, encoding="utf-8", newline="")
                write_call_files(call_dir, prompt, response, meta)
            return LlmOutcome(response=response, failure=failure, meta=meta)
        finally:
            shutil.rmtree(work, ignore_errors=True)
