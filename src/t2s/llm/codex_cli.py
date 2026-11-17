"""Codex CLI 백엔드(`codex exec`). 프롬프트는 표준 입력, 마지막 응답은 `-o` 파일이다.

TS engines/packkit/llm/codex.ts(인자 배열, 끄는 기능 11개, 이벤트 읽기, 실패 분류)를 옮긴 자체 코드다.
TS와 다른 점(docs/provenance.md 4절에도 적었다):
- 작업 폴더: 호출 폴더가 있으면 TS와 같이 그 안의 `codex-work`(호출 뒤 지운다), 없으면 저장소 밖 임시 폴더
  (`t2s-llm-` 접두어). 처음에는 늘 저장소 밖 임시 폴더였으나, codex가 작업 폴더 경로를 모델 입력에 넣어 같은
  프롬프트에서도 답이 달라지는 것을 확인해(2026-11-16, 경로 실험) TS와 같은 자리로 되돌렸다.
- 이벤트·오류 파일 이름은 `cli-events.jsonl`, `cli-stderr.txt`이고 CLI 판 칸은 `cli_version`이다.
- 시간 제한 환경변수(TS `BB_LLM_TIMEOUT_SECONDS`)는 없다. 실행 파일 이름만 `T2S_CODEX_BIN`으로 바꿀 수 있다(테스트용).
- 하네스 입력(2026-11-17부터 기본): codex 기본 지시를 한 줄 파일로 바꾸고 환경 블록·내장 스킬 목록을 끄며(minimal),
  범용 과제 설명 한 문장을 developer 지시로 더한다(task). TS 인자에는 없는 설정이다. 하네스 정리 실험(terra 500문항
  2×2)에서 task가 점수를 올리고 minimal은 점수는 그대로 입력을 57% 줄였다. `T2S_CODEX_HARNESS=none`이면 TS와 같은 인자다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from t2s.llm import _proc
from t2s.llm.base import LlmFailure, LlmOutcome, LlmSettings, base_meta, write_call_files

DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "apps",
    "browser_use",
    "computer_use",
    "plugins",
    "multi_agent",
    "image_generation",
    "view_image",
    "hooks",
    "skill_search",
)
NON_TOOL_ITEMS = frozenset({"agent_message", "reasoning", "error"})
USAGE_LIMIT = re.compile(r"usage limit|rate limit|quota|too many requests|\b429\b", re.IGNORECASE)
BIN_ENV = "T2S_CODEX_BIN"
EVENTS_FILE = "cli-events.jsonl"
STDERR_FILE = "cli-stderr.txt"
WORK_PREFIX = "t2s-llm-"
WORK_DIR_NAME = "codex-work"  # TS engines/pure-fix/pack/llm.ts callModel의 workDir 이름
# 실험용: 작업 폴더 경로를 바꿔 잰다. {pos}=문항 위치 폴더 이름, {k}=호출 번호 폴더 이름. 값은 호출 기록에 남는다.
WORK_TEMPLATE_ENV = "T2S_CODEX_WORK_TEMPLATE"
# codex가 붙이는 하네스 입력. 값이 없으면 DEFAULT_HARNESS, `none`이면 아무것도 더하지 않는다(TS와 같은 인자). 쉼표로 여러 개.
# minimal = 기본 지시를 한 줄 파일로 교체 + 환경 블록(작업 폴더 경로) 끔 + 내장 스킬 목록 끔
# task = 범용 과제 설명을 developer 지시로 더함(문항·DB별 내용 없음)
HARNESS_ENV = "T2S_CODEX_HARNESS"
DEFAULT_HARNESS = ("minimal", "task")
MINIMAL_INSTRUCTIONS_FILE = Path(__file__).with_name("codex_minimal_instructions.txt")
TASK_CONTEXT = (
    "You are assisting with a text-to-SQL task over a SQLite database. Follow the instructions in each message."
)


def work_path_for(call_dir: Path) -> Path:
    template = os.environ.get(WORK_TEMPLATE_ENV)
    if not template:
        return call_dir / WORK_DIR_NAME
    return Path(template.format(pos=call_dir.parent.parent.name, k=call_dir.name))


def harness_flags() -> tuple[str, ...]:
    raw = os.environ.get(HARNESS_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_HARNESS
    if raw.strip() == "none":
        return ()
    flags = tuple(f.strip() for f in raw.split(",") if f.strip())
    unknown = [f for f in flags if f not in ("minimal", "task")]
    if unknown:
        raise RuntimeError(f"{HARNESS_ENV}에 모르는 값: {', '.join(unknown)}")
    return flags


def harness_args(flags: tuple[str, ...]) -> list[str]:
    """codex_args 뒤 `-s` 앞에 끼울 설정 인자. 값은 TOML 문자열로 쓴다."""
    out: list[str] = []
    if "minimal" in flags:
        out += [
            "-c",
            f"model_instructions_file={json.dumps(str(MINIMAL_INSTRUCTIONS_FILE))}",
            "-c",
            "include_environment_context=false",
            "-c",
            "skills.include_instructions=false",
        ]
    if "task" in flags:
        out += ["-c", f"developer_instructions={json.dumps(TASK_CONTEXT)}"]
    return out


def codex_bin() -> str:
    return os.environ.get(BIN_ENV) or "codex"


def codex_args(settings: LlmSettings, last_file: str, work_dir: str) -> list[str]:
    """TS codexArgs와 글자까지 같은 인자 배열(실행 파일 이름 제외)."""
    args = [
        "exec",
        "-m",
        settings.model,
        "-c",
        f'model_reasoning_effort="{settings.reasoning_effort}"',
        "-c",
        'web_search="disabled"',
        "-c",
        "project_doc_max_bytes=0",
    ]
    for f in DISABLED_FEATURES:
        args += ["--disable", f]
    args += [
        "-s",
        "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--color",
        "never",
        "--json",
        "-o",
        last_file,
        "-C",
        work_dir,
        "-",
    ]
    return args


def read_codex_events(stdout: str) -> dict:
    """`--json` 이벤트 줄에서 도구 호출 수·사용량·오류 글·thread id를 뽑는다. JSON이 아닌 줄은 건너뛴다."""
    seen: set[str] = set()
    tool_items: list[str] = []
    usage = None
    errors: list[str] = []
    thread_id = None
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
        if t == "thread.started":
            thread_id = event.get("thread_id")
        if t == "turn.completed":
            usage = event.get("usage")
        if t == "error" and isinstance(event.get("message"), str):
            errors.append(event["message"])
        if t == "turn.failed" and isinstance((event.get("error") or {}).get("message"), str):
            errors.append(event["error"]["message"])
        item = event.get("item")
        if (
            t in ("item.started", "item.completed")
            and isinstance(item, dict)
            and item.get("type") not in NON_TOOL_ITEMS
        ):
            key = str(item.get("id"))
            if key not in seen:
                seen.add(key)
                tool_items.append(str(item.get("type")))
    return {
        "tool_calls": len(tool_items),
        "tool_items": tool_items,
        "usage": usage,
        "errors": errors,
        "thread_id": thread_id,
    }


def failure_detail(errors: list[str], stderr: str) -> str:
    """마지막 오류 글, 없으면 stderr 마지막 줄(2,000자까지)."""
    if errors:
        return errors[-1][:2000]
    lines = stderr.strip().split("\n")
    return (lines[-1] if lines else "")[:2000]


def shown_command(bin: str, args: list[str], call_dir: Path | None, work_dir: str) -> list[str]:
    out = [bin]
    base = str(call_dir) if call_dir is not None else None
    for a in args:
        if a == work_dir:
            out.append("<work>")
        elif base is not None and a.startswith(base):
            out.append(os.path.relpath(a, base))
        else:
            out.append(a)
    return out


class CodexBackend:
    id = "codex-cli"

    def __init__(self, settings: LlmSettings) -> None:
        self.settings = settings

    def call(self, prompt: str, call_dir: Path | None, *, name: str, attempt: int) -> LlmOutcome:
        bin = codex_bin()
        if call_dir is not None:
            call_dir = Path(call_dir)
            call_dir.mkdir(parents=True, exist_ok=True)
            work_path = work_path_for(call_dir)
            shutil.rmtree(work_path, ignore_errors=True)  # 남은 것이 있으면 빈 폴더로 시작한다
            work_path.mkdir(parents=True)
            if os.environ.get(WORK_TEMPLATE_ENV) and any(work_path.iterdir()):
                raise RuntimeError(f"작업 폴더가 비어 있지 않다: {work_path}")
            work = str(work_path)
        else:
            work = tempfile.mkdtemp(prefix=WORK_PREFIX)
        scratch = None
        try:
            if call_dir is not None:
                out_dir = call_dir
            else:
                scratch = tempfile.mkdtemp(prefix=WORK_PREFIX + "out-")
                out_dir = Path(scratch)
            last_file = out_dir / "response.txt"
            if last_file.exists():
                last_file.unlink()
            args = codex_args(self.settings, str(last_file), work)
            flags = harness_flags()
            if flags:
                i = args.index("-s")
                args = args[:i] + harness_args(flags) + args[i:]
            r = _proc.run_cli(
                [bin, *args], prompt, cwd=Path(work), env=None, timeout_seconds=self.settings.timeout_seconds
            )
            work_files = sum(len(files) + len(dirs) for _, dirs, files in os.walk(work))
            if call_dir is not None:
                (call_dir / EVENTS_FILE).write_text(r.stdout, encoding="utf-8", newline="")
                (call_dir / STDERR_FILE).write_text(r.stderr, encoding="utf-8", newline="")
            events = read_codex_events(r.stdout)
            response = last_file.read_text(encoding="utf-8") if last_file.is_file() else None
            failure: LlmFailure | None = None
            detail = failure_detail(events["errors"], r.stderr)
            if r.spawn_error is not None:
                failure = LlmFailure("CodexExit", f"codex를 실행하지 못했습니다: {r.spawn_error}")
            elif r.timed_out:
                failure = LlmFailure(
                    "CodexTimeout", f"codex가 {self.settings.timeout_seconds:g}초 안에 끝나지 않았습니다"
                )
            elif r.exit_code != 0 or response is None:
                kind = "CodexUsageLimit" if USAGE_LIMIT.search(detail) else "CodexExit"
                failure = LlmFailure(kind, f"exit {r.exit_code}: {detail}")
            if failure is not None:
                response = None
                if last_file.is_file() and call_dir is not None:
                    last_file.unlink()  # 실패면 응답 파일을 남기지 않는다(쓰지 않는다)
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
                    "command": shown_command(bin, args, call_dir, work),
                    "exit_code": r.exit_code,
                    "timed_out": r.timed_out,
                    "tool_items": events["tool_items"],
                    "thread_id": events["thread_id"],
                    "errors": [e[:1000] for e in events["errors"][-3:]],
                    "work_dir_outside_repo": call_dir is None,
                    "work_dir_template": os.environ.get(WORK_TEMPLATE_ENV) if call_dir is not None else None,
                    "work_dir": work if os.environ.get(WORK_TEMPLATE_ENV) and call_dir is not None else None,
                    "work_dir_files": work_files,
                    "harness": list(flags),
                }
            )
            if call_dir is not None:
                write_call_files(call_dir, prompt, response, meta)
            return LlmOutcome(response=response, failure=failure, meta=meta)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
