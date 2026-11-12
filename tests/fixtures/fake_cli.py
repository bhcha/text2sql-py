"""가짜 codex·claude 실행 파일을 만드는 도우미. 모델을 부르지 않는다.

make_fake_cli(tmp_path, kind)가 `#!<지금 Python>`으로 시작하는 실행 파일을 쓰고 경로를 돌려준다. 동작은 환경변수로 고른다.
- FAKE_CLI_LOG: 받은 인자·작업 폴더·작업 폴더 파일·CLAUDE로 시작하는 환경변수 이름·프롬프트를 JSON 한 줄로 덧붙인다
- FAKE_CLI_MODE: ok(기본) | exit | limit | hang | tool | noresponse | wrongmodel(claude만)
- FAKE_CLI_RESPONSES: JSON 목록. 부를 때마다 차례로 하나씩(FAKE_CLI_STATE 파일로 센다). 없으면 "SELECT 1"
- FAKE_CLI_PIDFILE: hang 모드에서 띄운 손자 프로세스의 pid를 적는다
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

SCRIPT = r"""
import json, os, subprocess, sys, time

KIND = __KIND__
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli fake" if KIND == "codex" else "0.0.0 (Claude Code fake)")
    sys.exit(0)
prompt = sys.stdin.read()
mode = os.environ.get("FAKE_CLI_MODE", "ok")
cwd = os.getcwd()
log = os.environ.get("FAKE_CLI_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "args": args, "cwd": cwd, "cwd_files": sorted(os.listdir(cwd)),
            "claude_env": sorted(k for k in os.environ if k.startswith("CLAUDE")),
            "env_names": sorted(os.environ), "prompt": prompt,
        }, ensure_ascii=False) + "\n")

def next_response():
    raw = os.environ.get("FAKE_CLI_RESPONSES")
    if not raw:
        return "SELECT 1"
    items = json.loads(raw)
    state = os.environ.get("FAKE_CLI_STATE")
    i = 0
    if state:
        try:
            i = int(open(state).read() or 0)
        except FileNotFoundError:
            i = 0
        with open(state, "w") as f:
            f.write(str(i + 1))
    return items[min(i, len(items) - 1)]

def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

if mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pidfile = os.environ.get("FAKE_CLI_PIDFILE")
    if pidfile:
        with open(pidfile, "w") as f:
            f.write(str(child.pid))
    time.sleep(60)
    sys.exit(0)

if KIND == "codex":
    out = args[args.index("-o") + 1] if "-o" in args else None
    model = args[args.index("-m") + 1] if "-m" in args else None
    emit({"type": "thread.started", "thread_id": "fake-thread"})
    if mode == "exit":
        sys.stderr.write("something went wrong\n")
        sys.exit(1)
    if mode == "limit":
        emit({"type": "error", "message": "You've hit your usage limit."})
        emit({"type": "turn.failed", "error": {"message": "You've hit your usage limit."}})
        sys.exit(1)
    emit({"type": "turn.started"})
    if mode == "tool":
        emit({"type": "item.started", "item": {"id": "item_0", "type": "command_execution"}})
        emit({"type": "item.completed", "item": {"id": "item_0", "type": "command_execution"}})
    text = next_response()
    emit({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": text}})
    emit({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 7, "reasoning_output_tokens": 3}})
    if mode != "noresponse" and out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
    sys.exit(0)

# claude
model = args[args.index("--model") + 1] if "--model" in args else None
if mode == "exit":
    sys.stderr.write("something went wrong\n")
    sys.exit(1)
emit({"type": "system", "subtype": "init", "tools": [], "mcp_servers": [], "model": model})
if mode == "limit":
    emit({"type": "result", "subtype": "success", "is_error": True, "result": "Claude usage limit reached; resets at 3pm",
          "usage": None, "session_id": "fake-session", "num_turns": 1})
    sys.exit(1)
reported = "claude-other-model" if mode == "wrongmodel" else model
text = next_response()
content = [{"type": "text", "text": text}]
if mode == "tool":
    content.append({"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {}})
emit({"type": "assistant", "message": {"model": reported, "content": content}})
if mode == "noresponse":
    emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "usage": None,
          "session_id": "fake-session", "num_turns": 1})
    sys.exit(0)
emit({"type": "result", "subtype": "success", "is_error": False, "result": text,
      "usage": {"input_tokens": 50, "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 4},
      "session_id": "fake-session", "num_turns": 1})
sys.exit(0)
"""


def make_fake_cli(tmp_path: Path, kind: str) -> Path:
    assert kind in ("codex", "claude")
    folder = Path(tmp_path) / "fakebin"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / kind
    path.write_text(f"#!{sys.executable}\n" + SCRIPT.replace("__KIND__", repr(kind)), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def read_log(path: Path) -> list[dict]:
    import json

    if not os.path.exists(path):
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
